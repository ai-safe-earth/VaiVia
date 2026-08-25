"""Generate the catalogue: bounded loops from real starts, over our own edges.

anchor x distance x seed -> route the triangle, assemble along the walked
sequence, score, keep the best distinct few. Every knob is a CLI argument with
a bounded default, so the catalogue size is predictable before it runs
(docs/route-pipeline.md: reviewable before a user sees it).

pgRouting does the drawing (pgr_dijkstra over routable_foot edges, undirected).
The second and third legs re-route with the already-walked edges cost-inflated
— a soft penalty, not an exclusion: walking back the same valley is sometimes
the only way home, and an impossible leg would kill loops a walker would
happily take as partial out-and-backs. `retrace_share` records what happened
and the scorer prices it; nothing is hidden.

REPLACE, NEVER MERGE, like every derived table. Routes hold edge_ids, so
build_network and repair clear them (the vertex_degree lesson); the
geometry-derived route_id is what survives — a rebuild that produces the same
ground produces the same ids, and ON CONFLICT would be a bug mask, so it is a
plain INSERT into a truncated table.

Run from pipeline/ (network built, elevation sampled, places snapped):
    uv run python -m draw.generate --dry-run
    uv run python -m draw.generate --starts 12
    uv run python -m draw.generate --starts 12 --emit   # also write route documents
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
import uuid

from core import connect
from draw.assemble import WalkedEdge, assemble, score, strict_return
from draw.destinations import Destination, crow_band, rank, route_name
from draw.loops import keep_distinct, ring_points
from export.document import SAC_ORDER
from ids import DIRECTED_SHAPES, forward_is_stored, route_id

# The starts worth drawing from, best first: on the main component (a start on
# an island goes nowhere), preferring many anchors (a real trailhead, not a
# lone bay) and car-free access. LIMIT is the CLI's --starts.
STARTS = """
SELECT s.vertex_id, ST_X(s.geom), ST_Y(s.geom), s.anchors, s.car_free
FROM qa.v_start s
WHERE s.reachability_class = '0 main network'
  AND (s.anchors >= 2 OR s.car_free)
  -- The parameter surface, null-idiom like the query service's fragments:
  -- an unstated filter filters nothing.
  AND (%(classes)s::text[] IS NULL OR s.start_classes && %(classes)s::text[])
  AND (NOT %(car_free_only)s OR s.car_free)
  AND (%(region)s::text IS NULL OR EXISTS (
        SELECT 1 FROM source_map.vertex_degree vd
        WHERE vd.vertex_id = s.vertex_id
          AND vd.regions @> ARRAY[%(region)s::text]))
ORDER BY s.car_free DESC, s.anchors DESC, s.vertex_id
LIMIT %(limit)s
"""

# Interesting places near a start, within the crow band for this target: the
# pool draw.destinations ranks. Reachability and adjacency are structural
# requirements (an island destination routes nowhere; a peak 1 km off-network
# has no path to it), not judgement calls.
DESTINATIONS = """
SELECT p.source || ':' || p.source_id, p.kind, p.name, p.vertex_id,
       ST_Distance(p.geom::geography,
                   ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography)
FROM qa.v_place p
WHERE p.reachability_class = '0 main network'
  AND NOT p.is_start
  AND p.distance_m <= 100
  AND p.kind = ANY(%(kinds)s)
  AND ST_DWithin(ST_Transform(p.geom, 32632),
                 ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 32632),
                 %(max_crow)s)
  AND NOT ST_DWithin(ST_Transform(p.geom, 32632),
                     ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 32632),
                     %(min_crow)s)
"""

NEAREST_VERTEX = """
SELECT v.vertex_id
FROM source_map.vertex v
WHERE v.component_id = (SELECT component_id FROM source_map.vertex
                        GROUP BY component_id ORDER BY count(*) DESC LIMIT 1)
ORDER BY ST_Transform(v.geom, 32632)
     <-> ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 32632)
LIMIT 1
"""

# One leg. The edges-SQL is assembled here (cost inflation for already-walked
# edges) but always from a fixed template plus a bare list of integer ids —
# nothing user-supplied is ever interpolated.
LEG = """
SELECT node, edge FROM pgr_dijkstra(
    %(edges_sql)s,
    %(from_vertex)s::bigint, %(to_vertex)s::bigint,
    directed := true)
WHERE edge >= 0
ORDER BY seq
"""

# Per activity: an mtb loop is drawn over routable_bike edges ONLY, so it is
# bike-legal BY CONSTRUCTION — the owner's observation made mechanism: a loop
# that shares a foot loop's legal segments and detours around its forbidden
# ones is exactly what the router produces when the forbidden ones are not in
# its graph. The 0005 views carry cost/reverse_cost with oneway (and implied
# roundabout oneway) honoured, and the legs run directed := true — the claim
# `oneway is never read` finally retired where it was made.
EDGES_BASE = {
    "foot": (
        "SELECT id, source, target, cost, reverse_cost " "FROM catalogue.v_edges_foot"
    ),
    "mtb": (
        "SELECT id, source, target, cost, reverse_cost " "FROM catalogue.v_edges_bike"
    ),
}

EDGE_DETAILS = """
SELECT e.edge_id, e.source, e.target, e.length_m,
       ST_AsGeoJSON(e.geom),
       e.profile_m, e.ascent_m, e.descent_m,
       e.tags ->> 'surface', e.tags ->> 'sac_scale', e.tags ->> 'mtb:scale',
       e.tags ->> 'highway', e.routable_bike, e.urban_m
FROM source_map.edge e WHERE e.edge_id = ANY(%(ids)s)
"""


def edges_sql(activity: str, penalised: set[int], factor: float = 3.0) -> str:
    """The pgr_dijkstra edges query, with walked edges made expensive.

    The penalty multiplies BOTH directions' costs but never resurrects a
    forbidden one: a -1 (oneway's "no such arc") stays -1, or an inflation
    would turn illegal into merely expensive.
    """
    view = "catalogue.v_edges_bike" if activity == "mtb" else "catalogue.v_edges_foot"
    if not penalised:
        return EDGES_BASE[activity]
    ids = ",".join(str(int(edge_id)) for edge_id in sorted(penalised))
    return (
        "SELECT id, source, target, "
        f"CASE WHEN cost < 0 THEN cost "
        f"WHEN id IN ({ids}) THEN cost * {factor} ELSE cost END AS cost, "
        f"CASE WHEN reverse_cost < 0 THEN reverse_cost "
        f"WHEN id IN ({ids}) THEN reverse_cost * {factor} "
        f"ELSE reverse_cost END AS reverse_cost "
        f"FROM {view}"
    )


def route_leg(
    conn, activity: str, from_vertex: int, to_vertex: int, penalised: set[int]
):
    """One Dijkstra leg as [(entered_from_node, edge_id), ...]."""
    return conn.execute(
        LEG,
        {
            "edges_sql": edges_sql(activity, penalised),
            "from_vertex": from_vertex,
            "to_vertex": to_vertex,
        },
    ).fetchall()


def directions(conn, steps: list[tuple[int, int]]) -> list[tuple[int, bool]]:
    """pgRouting's (node, edge) steps as explicit (edge, walked-forward) pairs.

    pgr_dijkstra's `node` is the vertex the step ENTERS the edge from, so the
    edge is walked forward exactly when that node is its stored source. This is
    the ONLY place direction is decided; everything downstream consumes it.
    """
    ids = [edge_id for _node, edge_id in steps]
    sources = dict(
        conn.execute(
            "SELECT edge_id, source FROM source_map.edge WHERE edge_id = ANY(%(ids)s)",
            {"ids": ids},
        )
    )
    return [(edge_id, node == sources[edge_id]) for node, edge_id in steps]


def walked_sequence(conn, steps: list[tuple[int, bool]]) -> list[WalkedEdge]:
    """Hydrate explicit (edge_id, forward) steps into WalkedEdges."""
    ids = [edge_id for edge_id, _forward in steps]
    rows = {row[0]: row for row in conn.execute(EDGE_DETAILS, {"ids": ids})}
    out: list[WalkedEdge] = []
    for edge_id, forward in steps:
        (
            _id,
            _source,
            _target,
            length_m,
            geometry,
            profile_m,
            ascent_m,
            descent_m,
            surface,
            sac,
            mtb_scale,
            highway,
            bike,
            urban_m,
        ) = rows[edge_id]
        coords = [(x, y) for x, y in json.loads(geometry)["coordinates"]]
        out.append(
            WalkedEdge(
                edge_id=edge_id,
                forward=forward,
                length_m=length_m,
                coords=coords,
                profile_m=profile_m,
                ascent_m=ascent_m,
                descent_m=descent_m,
                surface=surface,
                sac_scale=sac,
                mtb_scale=mtb_scale,
                highway=highway,
                routable_bike=bike,
                urban_m=urban_m,
            )
        )
    return out


def draw_loop(
    conn,
    activity: str,
    start_vertex: int,
    start: tuple[float, float],
    target_m: float,
    seed: int,
) -> list[WalkedEdge] | None:
    """start → via₁ → via₂ → start, with walked legs soft-penalised."""
    vias = []
    for point in ring_points(start, target_m, seed):
        row = conn.execute(
            NEAREST_VERTEX, {"lon": point.lon, "lat": point.lat}
        ).fetchone()
        if row is None:
            return None
        vias.append(row[0])

    waypoints = [start_vertex, *vias, start_vertex]
    steps: list[tuple[int, int]] = []
    walked: set[int] = set()
    for leg_from, leg_to in itertools.pairwise(waypoints):
        if leg_from == leg_to:
            continue
        leg = route_leg(conn, activity, leg_from, leg_to, walked)
        if not leg:
            return None  # disconnected ask — the whole loop is off
        steps.extend(leg)
        walked.update(edge_id for _node, edge_id in leg)
    if not steps:
        return None
    return walked_sequence(conn, directions(conn, steps))


def draw_out_and_back(
    conn, activity: str, start_vertex: int, destination: Destination
) -> list[WalkedEdge] | None:
    """Out to the destination, back with the out leg soft-penalised.

    The penalty makes the return take a different path where one exists at
    reasonable cost, and honestly retrace where the valley allows only one way
    - retrace_share reports which happened, and shape_class shows it.
    """
    if destination.vertex_id == start_vertex:
        return None
    out = route_leg(conn, activity, start_vertex, destination.vertex_id, set())
    if not out:
        return None
    walked = {edge_id for _node, edge_id in out}
    back = route_leg(conn, activity, destination.vertex_id, start_vertex, walked)
    if not back:
        return None
    steps = out + back
    return walked_sequence(conn, directions(conn, steps))


def draw_strict_out_and_back(
    conn, activity: str, start_vertex: int, destination: Destination
) -> list[WalkedEdge] | None:
    """Out to the destination and home on EXACTLY the outbound edges.

    No second routing: the return is the outbound steps reversed with each
    direction flipped (assemble.strict_return), so the promise "same edges
    in reverse" holds by construction. On the directed graph the reverse of
    a legal step is only legal where the edge is two-way — a oneway on the
    way out would make the way home illegal — so the out leg runs over the
    two-way subset (reverse_cost >= 0 filtered here, not a new view).
    """
    if destination.vertex_id == start_vertex:
        return None
    out = conn.execute(
        LEG,
        {
            "edges_sql": EDGES_BASE[activity] + " WHERE reverse_cost >= 0",
            "from_vertex": start_vertex,
            "to_vertex": destination.vertex_id,
        },
    ).fetchall()
    if not out:
        return None
    out_steps = directions(conn, out)
    return walked_sequence(conn, out_steps + strict_return(out_steps))


def destination_pool(
    conn, start: tuple[float, float], target_m: float
) -> list[Destination]:
    from draw.destinations import INTEREST

    min_crow, max_crow = crow_band(target_m)
    lon, lat = start
    rows = conn.execute(
        DESTINATIONS,
        {
            "lon": lon,
            "lat": lat,
            "kinds": list(INTEREST),
            "min_crow": min_crow,
            "max_crow": max_crow,
        },
    ).fetchall()
    return [Destination(*row) for row in rows]


#: The strict shape's product bands: one way 2-20 km, total 4-40 km.
#: Enforced at persistence like the length gate — a hard product rule, so
#: rejects are counted and reported, never silently absent.
OUT_AND_BACK_ONE_WAY_M = (2000.0, 20000.0)
TOTAL_BAND_M = (4000.0, 40000.0)


def _rejected(facts, args, rejections: dict[str, int]) -> bool:
    """The parameter surface's hard filters, counted per reason.

    Score stays descriptive — these are the caller's stated LIMITS, plus
    the strict shape's product bands. A drop is a coverage fact, so every
    reason is reported at the end.
    """

    def count(reason: str) -> bool:
        rejections[reason] = rejections.get(reason, 0) + 1
        return True

    if (
        args.max_sac_exigent is not None
        and facts.sac_max is not None
        and SAC_ORDER.index(facts.sac_max) > SAC_ORDER.index(args.max_sac_exigent)
    ):
        return count(f"exigent grade over {args.max_sac_exigent}")
    if args.max_ascent is not None and (
        facts.ascent_m is None or facts.ascent_m > args.max_ascent
    ):
        return count(f"ascent over {args.max_ascent:.0f} m (or unknown)")
    if (
        args.max_retrace is not None
        and args.shape != "out_and_back"
        and facts.retrace_share > args.max_retrace
    ):
        return count(f"retrace over {args.max_retrace:.0%}")
    if (
        args.max_urban_share is not None
        and facts.urban_share is not None
        and facts.urban_share > args.max_urban_share
    ):
        return count(f"urban share over {args.max_urban_share:.0%}")
    if args.shape == "out_and_back":
        one_way = facts.distance_m / 2.0
        if not (OUT_AND_BACK_ONE_WAY_M[0] <= one_way <= OUT_AND_BACK_ONE_WAY_M[1]):
            return count("one-way outside 2-20 km")
    if not (TOTAL_BAND_M[0] <= facts.distance_m <= TOTAL_BAND_M[1]):
        return count("total outside 4-40 km")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--starts", type=int, default=12, help="anchors to draw from")
    parser.add_argument(
        "--distances",
        default="5000,10000,15000",
        help="comma-separated target metres",
    )
    parser.add_argument("--seeds", type=int, default=4, help="attempts per ask")
    parser.add_argument(
        "--keep", type=int, default=2, help="best distinct kept per ask"
    )
    parser.add_argument(
        "--shape",
        choices=("loop", "destination", "out_and_back"),
        default="loop",
        help="loops, destination routes with an alternative return, or "
        "STRICT out-and-backs (same edges home, retrace 1.0 by construction)",
    )
    parser.add_argument(
        "--activity",
        choices=("foot", "mtb"),
        default="foot",
        help="mtb routes over bike-legal edges only: legal by construction",
    )
    parser.add_argument(
        "--start-class",
        action="append",
        default=None,
        help="only starts offering this arrival (repeatable: parking, "
        "station, bus_stop, urban_exit, settlement, campsite)",
    )
    parser.add_argument(
        "--car-free-only",
        action="store_true",
        help="only starts reachable without a car",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="only starts whose vertex touches this region's edges",
    )
    parser.add_argument(
        "--max-sac-exigent",
        default=None,
        choices=SAC_ORDER,
        help="drop candidates whose hardest metre exceeds this SAC grade "
        "(safety filters use the EXIGENT grade, never the character label)",
    )
    parser.add_argument(
        "--max-ascent",
        type=float,
        default=None,
        help="drop candidates above this climb",
    )
    parser.add_argument(
        "--max-retrace",
        type=float,
        default=None,
        help="drop candidates re-walking more than this share (ignored for "
        "out_and_back, whose repeat share is 0.5 by construction)",
    )
    parser.add_argument(
        "--max-urban-share",
        type=float,
        # Measured 2026-08-25 over the 228-route corpus: p95 at 0.66, then a
        # thin tail to 0.92 of unnamed town walks. 0.8 prunes the wholly-in-
        # town tail; every drop is REPORTED, so the default can never narrow
        # the catalogue silently. sql/v2/0008 carries the measurement.
        default=0.8,
        help="drop candidates with more than this share inside residential "
        "fabric (measured default 0.8; pass a large value to disable)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--emit",
        action="store_true",
        help="also write route documents for the kept routes",
    )
    args = parser.parse_args()
    targets = [float(t) for t in args.distances.split(",")]

    started = time.monotonic()
    run_id = f"draw-{uuid.uuid4().hex[:8]}"
    with connect() as conn:
        starts = conn.execute(
            STARTS,
            {
                "limit": args.starts,
                "classes": args.start_class,
                "car_free_only": args.car_free_only,
                "region": args.region,
            },
        ).fetchall()
        asks = len(starts) * len(targets)
        print(
            f"{len(starts)} starts x {len(targets)} distances x {args.seeds} seeds "
            f"-> at most {asks * args.seeds} candidates, keeping <= {asks * args.keep}"
        )
        if args.dry_run:
            for vertex_id, lon, lat, anchors, car_free in starts:
                print(
                    f"  vertex {vertex_id:>6}  ({lat:.4f}, {lon:.4f})  "
                    f"{anchors} anchors{'  car-free' if car_free else ''}"
                )
            print("\n--dry-run: nothing routed, nothing written")
            return

        conn.execute(
            "INSERT INTO provenance.build_run (run_id, stage, parameters) VALUES (%s, 'draw', %s)",
            (
                run_id,
                json.dumps(
                    {
                        "builder": "draw.generate",
                        "activity": args.activity,
                        "shape": args.shape,
                        "starts": args.starts,
                        "distances": targets,
                        "seeds": args.seeds,
                        "keep": args.keep,
                        "network_run_id": sorted(
                            r
                            for (r,) in conn.execute(
                                "SELECT DISTINCT run_id FROM source_map.edge"
                            )
                        ),
                    }
                ),
            ),
        )

        kept_total: list[dict] = []
        rejections: dict[str, int] = {}
        for vertex_id, lon, lat, _anchors, _car_free in starts:
            for target_m in targets:
                candidates = []
                if args.shape in ("destination", "out_and_back"):
                    # Deterministic like the seeds: the same pool ranks the
                    # same way, so the same asks draw the same routes.
                    pool = rank(
                        destination_pool(conn, (lon, lat), target_m),
                        top=args.seeds,
                    )
                    attempts = [("destination", d) for d in pool]
                else:
                    attempts = [("seed", seed) for seed in range(args.seeds)]
                for index, (mode, ask) in enumerate(attempts):
                    if mode == "destination" and args.shape == "out_and_back":
                        sequence = draw_strict_out_and_back(
                            conn, args.activity, vertex_id, ask
                        )
                    elif mode == "destination":
                        sequence = draw_out_and_back(
                            conn, args.activity, vertex_id, ask
                        )
                    else:
                        sequence = draw_loop(
                            conn, args.activity, vertex_id, (lon, lat), target_m, ask
                        )
                    if not sequence:
                        continue
                    facts = assemble(sequence)
                    if _rejected(facts, args, rejections):
                        continue
                    candidates.append(
                        {
                            "score": score(facts, target_m),
                            "edge_ids": {e.edge_id for e in sequence},
                            "sequence": sequence,
                            "facts": facts,
                            "seed": index,
                            "start_vertex": vertex_id,
                            "target_m": target_m,
                            "destination": ask if mode == "destination" else None,
                        }
                    )
                kept = keep_distinct(candidates, max_keep=args.keep)
                kept_total.extend(kept)
                if kept:
                    best = kept[0]["facts"]
                    print(
                        f"  v{vertex_id} @{target_m / 1000:.0f}km: "
                        f"{len(candidates)} drawn, {len(kept)} kept - best "
                        f"{best.distance_m / 1000:.1f} km, "
                        f"off-road {best.off_road_share:.0%}, "
                        f"retrace {best.retrace_share:.0%}"
                    )

        if rejections:
            print("\nrejected by the stated limits (a drop is a coverage fact):")
            for reason, n in sorted(rejections.items(), key=lambda kv: -kv[1]):
                print(f"  {n:>5}  {reason}")

        # Replace, not merge — PER (ACTIVITY, SHAPE): loops and destination
        # routes are siblings the same way foot and mtb are, and regenerating
        # one family must not silently delete the other.
        conn.execute(
            "DELETE FROM catalogue.route WHERE activity = %s AND shape = %s",
            (args.activity, args.shape),
        )
        inserted = 0
        for candidate in kept_total:
            facts = candidate["facts"]
            # The v2 mint: directed shapes carry the direction this walk
            # was drawn in, sense fixed by geometry alone (pipeline/ids.py).
            direction = None
            if args.shape in DIRECTED_SHAPES:
                direction = "fwd" if forward_is_stored(facts.coords) else "rev"
            rid = route_id([facts.coords], args.shape, direction or "fwd")
            # The same ground can win from two nearby starts — one row speaks.
            # It can also already exist as the OTHER activity's loop (a fully
            # bike-legal foot loop IS the mtb loop over the same ground): the
            # id is the ground, so the earlier row stands and this candidate
            # folds into it rather than duplicating the geometry.
            exists = conn.execute(
                "SELECT 1 FROM catalogue.route WHERE route_id = %s", (rid,)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO catalogue.route
                    (route_id, direction, kind, activity, shape, name, urban_share,
                     destination_id, destination_kind, destination_name,
                     start_vertex, target_m, distance_m,
                     ascent_m, descent_m, sac_scale, sac_max, graded_share,
                     mtb_rideable, mtb_scale, bike_blocked_m,
                     surface, off_road_share, retrace_share, score, seed,
                     geom, run_id)
                VALUES (%s, %s, 'generated', %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)
                """,
                (
                    rid,
                    direction,
                    args.activity,
                    args.shape,
                    (
                        route_name(candidate["destination"])
                        if candidate["destination"]
                        else None
                    ),
                    facts.urban_share,
                    (
                        candidate["destination"].place_id
                        if candidate["destination"]
                        else None
                    ),
                    candidate["destination"].kind if candidate["destination"] else None,
                    candidate["destination"].name if candidate["destination"] else None,
                    candidate["start_vertex"],
                    candidate["target_m"],
                    facts.distance_m,
                    facts.ascent_m,
                    facts.descent_m,
                    facts.sac_scale,
                    facts.sac_max,
                    facts.graded_share,
                    facts.mtb_rideable,
                    facts.mtb_scale,
                    facts.bike_blocked_m,
                    json.dumps(facts.surface),
                    facts.off_road_share,
                    facts.retrace_share,
                    candidate["score"],
                    candidate["seed"],
                    json.dumps(
                        {
                            "type": "LineString",
                            "coordinates": [[x, y] for x, y in facts.coords],
                        }
                    ),
                    run_id,
                ),
            )
            with (
                conn.cursor() as cur,
                cur.copy(
                    "COPY catalogue.route_edge (route_id, seq, edge_id, forward)"
                    " FROM STDIN"
                ) as copy,
            ):
                for seq, walked_edge in enumerate(candidate["sequence"]):
                    copy.write_row((rid, seq, walked_edge.edge_id, walked_edge.forward))
            inserted += 1

        counts = {
            "kept": len(kept_total),
            "routes": inserted,
            "starts": len(starts),
            "targets": targets,
        }
        conn.execute(
            "UPDATE provenance.build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (json.dumps(counts), run_id),
        )
        print(
            f"\n{inserted} distinct routes written "
            f"({len(kept_total) - inserted} were the same ground from another start) "
            f"in {time.monotonic() - started:.0f}s - layer: qa.v_draw"
        )
        print(f"run {run_id}")

    if args.emit:
        from draw.emit import emit_generated

        emit_generated()


if __name__ == "__main__":
    main()
