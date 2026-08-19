"""Repair the topology failures QA found. Reviewable, reversible, per rule.

This is the second half of the loop `topology/qa.py` opens. It deliberately
repairs FINDINGS, not a fresh scan: you fix what you looked at in QGIS, at the
tolerance you chose from the histogram, and the two cannot drift apart inside
one pass. Re-detect afterwards to see what is left.

Every change writes a `qa.fix` row carrying the geometry before and after, so a
pass is reviewable after the fact and a wrong tolerance is undoable —
`qa.v_fix` is the QGIS layer for exactly that. `--dry-run` reports what would
change and writes nothing.

The geometry arithmetic is Python, not SQL, and that follows the division of
labour in docs/metadata-rules.md: a weld is per-feature judgement that has to be
unit-tested, not one statement over a table. Lengths are recomputed with the
same geodesic `line_length_m` the network was built with, so a repaired edge is
measured the way its neighbours were.

What each rule does:

  gap_dangle_pair       Weld two loose ends: the higher vertex_id moves onto the
                        lower, its edge's endpoint follows, and the moved vertex
                        is deleted. One geometry changes, by at most the
                        tolerance.
  gap_dangle_junction   Weld a loose end onto an existing junction. The junction
                        already carries two or more edges and is therefore the
                        real position; only the dangle moves.
  gap_dangle_edge       Split the target edge at the point closest to the loose
                        end and make the loose end itself the shared vertex. The
                        undershoot closes without inventing a vertex.
  degenerate            Two classes, two repairs: a self-loop is SPLIT at its
                        midpoint (real geometry routing cannot enter), a
                        sub-metre edge is COLLAPSED (no ground, but a real
                        connection). Only a zero-length ring is deleted. Runs
                        LAST: a weld can shorten an edge into this class.

Not repaired here, deliberately:

  island                Not a defect. A component too small to hold a route is
                        usually a genuinely isolated fragment at the bbox edge —
                        a coverage fact to see, not to weld to something.
  overlap               Needs judgement per case: the same ground mapped twice
                        can be a duplicate, a bridge, or two ways that
                        legitimately share a stretch. Deleting one automatically
                        would throw away tags the survivor does not carry.

Run from pipeline/:
    uv run python -m topology.repair --dry-run
    uv run python -m topology.repair
    uv run python -m topology.repair --rule gap_dangle_junction
"""

from __future__ import annotations

import argparse
import json
import uuid

from shapely import wkb as shapely_wkb
from shapely.geometry import LineString, Point
from shapely.ops import substring

from core import connect
from load.osm import ewkb4326
from topology.build_network import line_length_m
from topology.split import Coord

# Findings to act on: the latest QA run's, so the review and the repair see the
# same set. qa.latest_run is the same view the QGIS layers use.
FINDINGS = """
SELECT f.finding_id, f.note
FROM qa.finding f, qa.latest_run r
WHERE f.rule = %(rule)s AND f.run_id = r.run_id
ORDER BY f.finding_id
"""

# Recomputed after any repair: welds and drops both change connectivity, and a
# stale component_id is what seeds a route on an island.
COMPONENTS = """
UPDATE curated.vertex v
SET component_id = c.component
FROM pgr_connectedComponents(
    'SELECT edge_id AS id, source, target, length_m AS cost FROM curated.edge'
) c
WHERE v.vertex_id = c.node
"""


# ── Pure geometry, pinned by tests ────────────────────────────────────────────


def snap_endpoint(
    coords: list[Coord], moving: Coord, destination: Coord
) -> list[Coord]:
    """Move whichever end of a line sits on `moving` to `destination`.

    Only an ENDPOINT moves. An interior coordinate equal to `moving` is left
    alone: the vertex being welded is by definition an end of this edge, and
    touching the interior would deform the line somewhere nobody looked.
    Coordinates come back unchanged when neither end matches, which tells the
    caller the finding no longer describes the network.
    """
    if not coords:
        return coords
    out = list(coords)
    if out[0] == moving:
        out[0] = destination
    if out[-1] == moving:
        out[-1] = destination
    return out


def collapses(coords: list[Coord]) -> bool:
    """True when a line has no two distinct points left.

    A weld can pull an edge's ends together; saying so here lets the caller
    leave it for the degenerate rule rather than write a zero-length geometry
    that PostGIS accepts and route assembly later divides by.
    """
    return len(coords) < 2 or all(point == coords[0] for point in coords[1:])


def split_ring(
    coords: list[Coord],
) -> tuple[list[Coord], list[Coord], Coord] | None:
    """Cut a closed way in half by arc length, returning both halves and the
    new midpoint they share.

    A self-loop is a real thing on the ground — a loop trail, a roundabout, a
    path around a tarn — mapped as one closed way. It is only degenerate as a
    ROUTING edge, because source and target are the same vertex and no shortest
    path can enter it. Splitting it at the midpoint makes it traversable and
    keeps every metre. Deleting it, which is what an earlier version of this
    module did, threw away 26.3 km of real network on the first run.

    None when either half would collapse: a self-loop too small to halve is a
    mapping artefact, and the short-edge rule takes it instead.
    """
    if len(coords) < 3:
        return None
    line = LineString(coords)
    first = [(x, y) for x, y in substring(line, 0, 0.5, normalized=True).coords]
    second = [(x, y) for x, y in substring(line, 0.5, 1, normalized=True).coords]
    if not first or not second or collapses(first) or collapses(second):
        return None
    midpoint = first[-1]
    second[0] = midpoint
    return first, second, midpoint


def split_at_point(
    coords: list[Coord], point: Coord, min_piece_m: float = 1.0
) -> tuple[list[Coord], list[Coord]] | None:
    """Cut a line where `point` projects onto it, and join both halves there.

    The point itself becomes the shared coordinate rather than the projection,
    so the loose end closes onto the line instead of a new vertex appearing
    beside it. Returns None when the projection lands on an end (that is the
    pair or junction case, which another rule owns) or when either half would
    collapse — refusing is always better than emitting a zero-length piece.

    `min_piece_m` refuses a cut so close to an end that it would manufacture a
    degenerate edge: the first version had no such guard, and a projection at
    0.1% along a long way left five sub-metre pieces behind for the degenerate
    rule to clean up after it. A dangle that near an endpoint is the pair or
    junction case in all but name.
    """
    if len(coords) < 2:
        return None
    line = LineString(coords)
    fraction = line.project(Point(point), normalized=True)
    if not 0 < fraction < 1:
        return None
    first = [(x, y) for x, y in substring(line, 0, fraction, normalized=True).coords]
    second = [(x, y) for x, y in substring(line, fraction, 1, normalized=True).coords]
    if not first or not second:
        return None
    first[-1] = point
    second[0] = point
    if collapses(first) or collapses(second):
        return None
    if min(line_length_m(first), line_length_m(second)) < min_piece_m:
        return None
    return first, second


# ── Database plumbing ─────────────────────────────────────────────────────────


def _fix(conn, run_id, rule, target, before, after, note: dict) -> None:
    conn.execute(
        "INSERT INTO qa.fix (run_id, rule, target, geom_before, geom_after, note)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (run_id, rule, target, before, after, json.dumps(note)),
    )


def _coords(conn, edge_id: int) -> tuple[list[Coord], bytes]:
    geom = conn.execute(
        "SELECT ST_AsBinary(geom) FROM curated.edge WHERE edge_id = %s", (edge_id,)
    ).fetchone()[0]
    line = shapely_wkb.loads(bytes(geom))
    return [(x, y) for x, y in line.coords], geom


def _vertex(conn, vertex_id: int) -> Coord | None:
    row = conn.execute(
        "SELECT ST_X(geom), ST_Y(geom) FROM curated.vertex WHERE vertex_id = %s",
        (vertex_id,),
    ).fetchone()
    return (float(row[0]), float(row[1])) if row else None


def _write_geom(conn, edge_id: int, coords: list[Coord]) -> None:
    conn.execute(
        "UPDATE curated.edge SET geom = %s, length_m = %s WHERE edge_id = %s",
        (ewkb4326(LineString(coords).wkb_hex), line_length_m(coords), edge_id),
    )


def _weld(conn, run_id: str, rule: str, moving_id: int, fixed_id: int) -> bool:
    """Move vertex `moving_id` onto `fixed_id`, dragging its edges' ends with it.

    False when the weld no longer applies — an earlier finding in the same pass
    may already have welded one of the two away, and a finding set is a snapshot
    of a network the pass is changing underneath it.
    """
    moving = _vertex(conn, moving_id)
    fixed = _vertex(conn, fixed_id)
    if moving is None or fixed is None or moving == fixed:
        return False

    edges = conn.execute(
        "SELECT edge_id FROM curated.edge WHERE source = %s OR target = %s",
        (moving_id, moving_id),
    ).fetchall()

    for (edge_id,) in edges:
        coords, before = _coords(conn, edge_id)
        snapped = snap_endpoint(coords, moving, fixed)
        if snapped == coords:
            continue
        _write_geom(conn, edge_id, snapped)
        conn.execute(
            "UPDATE curated.edge"
            "   SET source = CASE WHEN source = %(moving)s THEN %(fixed)s ELSE source END,"
            "       target = CASE WHEN target = %(moving)s THEN %(fixed)s ELSE target END"
            " WHERE edge_id = %(edge)s",
            {"moving": moving_id, "fixed": fixed_id, "edge": edge_id},
        )
        after = conn.execute(
            "SELECT ST_AsBinary(geom) FROM curated.edge WHERE edge_id = %s", (edge_id,)
        ).fetchone()[0]
        _fix(
            conn,
            run_id,
            rule,
            f"edge:{edge_id}",
            before,
            after,
            {"welded_vertex": moving_id, "onto_vertex": fixed_id},
        )

    conn.execute("DELETE FROM curated.vertex WHERE vertex_id = %s", (moving_id,))
    return True


# ── One function per rule ─────────────────────────────────────────────────────


def repair_pair(conn, run_id: str, dry_run: bool) -> int:
    """Weld two loose ends. The lower vertex_id stays put — arbitrary, but
    deterministic: the two ends are equally real, so nothing in the data prefers
    one, and a stable rule makes a rerun reproducible."""
    rows = conn.execute(FINDINGS, {"rule": "gap_dangle_pair"}).fetchall()
    if dry_run:
        return len(rows)
    done = 0
    for _, note in rows:
        payload = json.loads(note)
        a, b = int(payload["a"]), int(payload["b"])
        moving, fixed = (a, b) if a > b else (b, a)
        done += _weld(conn, run_id, "gap_dangle_pair", moving, fixed)
    return done


def repair_junction(conn, run_id: str, dry_run: bool) -> int:
    """Weld a loose end onto an existing junction. The junction never moves."""
    rows = conn.execute(FINDINGS, {"rule": "gap_dangle_junction"}).fetchall()
    if dry_run:
        return len(rows)
    done = 0
    for _, note in rows:
        payload = json.loads(note)
        done += _weld(
            conn,
            run_id,
            "gap_dangle_junction",
            int(payload["vertex"]),
            int(payload["junction"]),
        )
    return done


def repair_edge(conn, run_id: str, dry_run: bool) -> int:
    """Split the target edge at the loose end and join them there.

    The second half takes the next free piece_index for its way rather than
    shifting its siblings: piece_index is provenance — which piece of which way
    — and UNIQUE (way_id, piece_index) is what it has to keep. Order along the
    way is read from geometry, never from this number.
    """
    rows = conn.execute(FINDINGS, {"rule": "gap_dangle_edge"}).fetchall()
    if dry_run:
        return len(rows)
    done = 0
    for _, note in rows:
        payload = json.loads(note)
        vertex_id, edge_id = int(payload["vertex"]), int(payload["edge"])
        dangle = _vertex(conn, vertex_id)
        target_row = conn.execute(
            "SELECT way_id, target, tags, routable_foot, routable_bike, regions, run_id"
            " FROM curated.edge WHERE edge_id = %s",
            (edge_id,),
        ).fetchone()
        if dangle is None or target_row is None:
            continue  # an earlier repair moved or removed one of them
        way_id, old_target, tags, foot, bike, regions, edge_run = target_row

        coords, before = _coords(conn, edge_id)
        halves = split_at_point(coords, dangle)
        if halves is None:
            continue
        first, second = halves

        next_index = conn.execute(
            "SELECT coalesce(max(piece_index), 0) + 1 FROM curated.edge"
            " WHERE way_id = %s",
            (way_id,),
        ).fetchone()[0]

        _write_geom(conn, edge_id, first)
        conn.execute(
            "UPDATE curated.edge SET target = %s WHERE edge_id = %s",
            (vertex_id, edge_id),
        )
        new_id = conn.execute(
            "INSERT INTO curated.edge (way_id, piece_index, source, target, geom,"
            " length_m, tags, routable_foot, routable_bike, regions, run_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING edge_id",
            (
                way_id,
                next_index,
                vertex_id,
                old_target,
                ewkb4326(LineString(second).wkb_hex),
                line_length_m(second),
                json.dumps(tags),
                foot,
                bike,
                regions,
                edge_run,
            ),
        ).fetchone()[0]

        after = conn.execute(
            "SELECT ST_AsBinary(geom) FROM curated.edge WHERE edge_id = %s", (edge_id,)
        ).fetchone()[0]
        _fix(
            conn,
            run_id,
            "gap_dangle_edge",
            f"edge:{edge_id}",
            before,
            after,
            {"split_at_vertex": vertex_id, "new_edge": new_id, "half": "first"},
        )
        _fix(
            conn,
            run_id,
            "gap_dangle_edge",
            f"edge:{new_id}",
            before,
            ewkb4326(LineString(second).wkb_hex),
            {"split_at_vertex": vertex_id, "from_edge": edge_id, "half": "second"},
        )
        done += 1
    return done


def _delete_edge(conn, run_id: str, edge_id: int, why: str) -> None:
    """Delete an edge, recording enough to put it back.

    Geometry alone rebuilds nothing, so the note carries every column the row
    had: `qa.fix` is the undo log, and an undo log that loses the tags is a
    record of the damage rather than a way out of it.
    """
    row = conn.execute(
        "SELECT ST_AsBinary(geom), way_id, piece_index, source, target, tags,"
        "       routable_foot, routable_bike, regions, length_m"
        " FROM curated.edge WHERE edge_id = %s",
        (edge_id,),
    ).fetchone()
    if row is None:
        return
    geom, way_id, piece_index, source, target, tags, foot, bike, regions, length = row
    _fix(
        conn,
        run_id,
        "degenerate",
        f"edge:{edge_id}",
        geom,
        None,
        {
            "deleted": True,
            "why": why,
            "length_m": round(float(length), 3),
            "row": {
                "way_id": way_id,
                "piece_index": piece_index,
                "source": source,
                "target": target,
                "tags": tags,
                "routable_foot": foot,
                "routable_bike": bike,
                "regions": regions,
            },
        },
    )
    conn.execute("DELETE FROM curated.edge WHERE edge_id = %s", (edge_id,))


def _split_ring_edge(conn, run_id: str, edge_id: int) -> bool:
    """Halve a self-loop so routing can enter it. Keeps every metre."""
    row = conn.execute(
        "SELECT way_id, source, tags, routable_foot, routable_bike, regions, run_id"
        " FROM curated.edge WHERE edge_id = %s",
        (edge_id,),
    ).fetchone()
    if row is None:
        return False
    way_id, source, tags, foot, bike, regions, edge_run = row

    coords, before = _coords(conn, edge_id)
    halves = split_ring(coords)
    if halves is None:
        return False
    first, second, midpoint = halves

    # The unique index on vertex geometry is the dedup key for the whole
    # network, so a midpoint landing on an existing vertex must reuse it rather
    # than fail — ON CONFLICT DO UPDATE returns the row, DO NOTHING would not.
    mid_id = conn.execute(
        "INSERT INTO curated.vertex (geom, run_id) VALUES (%s, %s)"
        " ON CONFLICT (geom) DO UPDATE SET run_id = curated.vertex.run_id"
        " RETURNING vertex_id",
        (ewkb4326(Point(midpoint).wkb_hex), run_id),
    ).fetchone()[0]

    next_index = conn.execute(
        "SELECT coalesce(max(piece_index), 0) + 1 FROM curated.edge WHERE way_id = %s",
        (way_id,),
    ).fetchone()[0]

    _write_geom(conn, edge_id, first)
    conn.execute(
        "UPDATE curated.edge SET target = %s WHERE edge_id = %s", (mid_id, edge_id)
    )
    new_id = conn.execute(
        "INSERT INTO curated.edge (way_id, piece_index, source, target, geom,"
        " length_m, tags, routable_foot, routable_bike, regions, run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING edge_id",
        (
            way_id,
            next_index,
            mid_id,
            source,
            ewkb4326(LineString(second).wkb_hex),
            line_length_m(second),
            json.dumps(tags),
            foot,
            bike,
            regions,
            edge_run,
        ),
    ).fetchone()[0]

    after = conn.execute(
        "SELECT ST_AsBinary(geom) FROM curated.edge WHERE edge_id = %s", (edge_id,)
    ).fetchone()[0]
    _fix(
        conn,
        run_id,
        "degenerate",
        f"edge:{edge_id}",
        before,
        after,
        {"self_loop_split_at_vertex": mid_id, "new_edge": new_id, "half": "first"},
    )
    return True


def repair_degenerate(conn, run_id: str, dry_run: bool, min_length_m: float) -> int:
    """Make degenerate edges usable, and delete only what carries no ground.

    Two classes, two different repairs, and the difference is what the first
    version of this module got wrong by treating them alike:

      a self-loop        is real geometry that routing cannot enter. SPLIT it at
                         the midpoint. Deleting these cost 26.3 km of network on
                         the first run — the longest was a 640 m loop way.
      a sub-metre edge   carries no meaningful ground but does carry a
                         CONNECTION. COLLAPSE it: weld its ends together so the
                         edge disappears and its neighbours stay joined. Deleting
                         these severed the join and created 129 new loose ends.

    Only a zero-length ring — a self-loop too small to halve — is deleted, and
    it connects nothing to nothing by construction.

    Re-measured here rather than read from the findings: the welds above run
    first and can shorten an edge into this class, and a pass that ignored its
    own effects would need a second run to converge.
    """
    select = (
        "SELECT edge_id, length_m, source = target AS self_loop, source, target"
        " FROM curated.edge WHERE length_m < %s OR source = target ORDER BY edge_id"
    )
    if dry_run:
        return len(conn.execute(select, (min_length_m,)).fetchall())

    # Collapsing one edge can collapse another that was not in the first
    # snapshot — welding two vertices together takes every edge between them to
    # zero length at once. Re-select until a pass changes nothing; the bound is
    # a backstop, not an expectation, since each pass strictly removes edges.
    done = 0
    for _ in range(5):
        rows = conn.execute(select, (min_length_m,)).fetchall()
        if not rows:
            break
        changed = 0
        for edge_id, length_m, self_loop, source, target in rows:
            if self_loop and float(length_m) >= min_length_m:
                changed += _split_ring_edge(conn, run_id, edge_id)
            elif not self_loop:
                # Collapse: the lower vertex_id stays, matching the pair rule.
                moving, fixed = (
                    (source, target) if source > target else (target, source)
                )
                if _weld(conn, run_id, "degenerate", moving, fixed):
                    _delete_edge(conn, run_id, edge_id, "collapsed to a point")
                    changed += 1
            else:
                _delete_edge(conn, run_id, edge_id, "zero-length ring")
                changed += 1
        done += changed
        if changed == 0:
            break
    orphans = conn.execute(
        "DELETE FROM curated.vertex v WHERE NOT EXISTS ("
        "  SELECT 1 FROM curated.edge e"
        "   WHERE e.source = v.vertex_id OR e.target = v.vertex_id)"
        " RETURNING vertex_id"
    ).fetchall()
    if orphans:
        print(f"  {'orphan vertices':<22} {len(orphans):>7,} deleted")
    return done


REPAIRS = {
    "gap_dangle_pair": repair_pair,
    "gap_dangle_junction": repair_junction,
    "gap_dangle_edge": repair_edge,
}
ORDER = [*REPAIRS, "degenerate"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rule",
        action="append",
        choices=ORDER,
        help="repair only these rules (repeatable); default is all, in order",
    )
    parser.add_argument("--min-length-m", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    selected = args.rule or ORDER
    run_id = f"repair-{uuid.uuid4().hex[:8]}"

    with connect() as conn:
        latest = conn.execute("SELECT run_id FROM qa.latest_run").fetchone()
        if latest is None:
            raise SystemExit(
                "no QA run to repair from — run `python -m topology.qa` first"
            )
        print(f"repairing findings from {latest[0]}")

        if not args.dry_run:
            conn.execute(
                "INSERT INTO build_run (run_id, stage, parameters)"
                " VALUES (%s, 'topology', %s)",
                (
                    run_id,
                    json.dumps(
                        {
                            "repair": True,
                            "from_run": latest[0],
                            "rules": selected,
                            "min_length_m": args.min_length_m,
                        }
                    ),
                ),
            )

        counts: dict[str, int] = {}
        for rule in ORDER:
            if rule not in selected:
                continue
            if rule == "degenerate":
                n = repair_degenerate(conn, run_id, args.dry_run, args.min_length_m)
            else:
                n = REPAIRS[rule](conn, run_id, args.dry_run)
            counts[rule] = n
            print(
                f"  {rule:<22} {n:>7,} {'would repair' if args.dry_run else 'repaired'}"
            )

        if args.dry_run:
            print("\ndry run: nothing written")
            return

        print("refreshing vertex degree and connected components...")
        conn.execute("REFRESH MATERIALIZED VIEW curated.vertex_degree")
        conn.execute(COMPONENTS)
        conn.execute(
            "UPDATE build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (json.dumps(counts), run_id),
        )
        print(f"\nrun: {run_id}. Re-run topology.qa to see what is left.")


if __name__ == "__main__":
    main()
