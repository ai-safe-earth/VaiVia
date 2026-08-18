"""Build the route catalogue: generate, score, dedup, enrich, persist.

Stages 2-5 of docs/route-pipeline.md. Neo4j stops being the routing engine and
becomes the enriched catalogue a chat turn selects from, so this runs offline
and slowly, and the runtime becomes a filter plus a vector search.

Bounded generation, which is what makes "draw all the possible paths" a
well-defined job rather than an infinite one:

    trailheads x target distances x seeds -> generate
                                          -> score, dedup, keep the best few
                                          -> map back to POIs passed
                                          -> MERGE (:Route)

Catalogue size is therefore predictable and coverage is auditable: the summary
at the end names the trailheads that produced nothing, instead of leaving a hole
to be discovered by a user asking for a walk there.

Idempotent: routes MERGE on a deterministic id of trailhead + target distance +
rank, so re-running replaces a trailhead's routes rather than accumulating them.

Run from backend/ with Neo4j up, GDS loaded, a region ingested and trailheads
built:
    uv run python -m scripts.build_routes --limit 5 --dry-run
    uv run python -m scripts.build_routes --min-off-road 0.6
"""

import argparse
import asyncio
import logging
from contextlib import suppress
from typing import Any
from uuid import uuid4

from neo4j.exceptions import Neo4jError

from core.config import get_settings
from graph.graphhopper import GraphHopperClient
from graph.neo4j_client import Neo4jClient
from graph.route_context import pois_along_route, summarize_pois
from graph.route_generation import generate_loops
from graph.route_scoring import select

logger = logging.getLogger(__name__)

DEFAULT_TARGETS_M = [5_000, 10_000, 15_000, 20_000]

FETCH_TRAILHEADS = """
MATCH (t:Trailhead)-[:STARTS_AT]->(i:Intersection)
WHERE t.off_road_share >= $min_off_road
RETURN t.trailhead_id AS trailhead_id, t.name AS name,
       t.off_road_share AS off_road_share,
       i.osm_node_id AS node_id, i.component_id AS component_id,
       i.location.latitude AS lat, i.location.longitude AS lon
ORDER BY t.off_road_share DESC
"""

# Replace rather than accumulate: a second run with different parameters must
# not leave the previous run's routes behind pretending to be current.
# Scoped to the activity being rebuilt: regenerating the hike catalogue must
# not delete the mtb one.
#
# Written AFTER the new routes, not before. Clearing first left the catalogue
# briefly empty, and a live query landing in that window honestly answered that
# there are no loops -- the worst possible failure, because it looks like data
# rather than a race. Route ids are deterministic, so MERGE updates a surviving
# route in place and only what the new run did not produce is deleted: at every
# instant the catalogue is complete, either the old one or the new one. That
# needs no transaction and no downtime.
DELETE_STALE_ROUTES = """
UNWIND $trailhead_ids AS tid
MATCH (r:Route {trailhead_id: tid, activity: $activity})
WHERE NOT r.route_id IN $keep_ids
DETACH DELETE r
"""

MERGE_ROUTES = """
UNWIND $rows AS row
MERGE (r:Route {route_id: row.route_id})
SET r.trailhead_id = row.trailhead_id,
    r.activity = row.activity,
    r.hike_rating = row.hike_rating,
    r.mtb_rating = row.mtb_rating,
    r.target_m = row.target_m,
    r.distance_m = row.distance_m,
    r.ascent_m = row.ascent_m,
    r.off_road_share = row.off_road_share,
    r.retrace = row.retrace,
    r.score = row.score,
    r.score_parts = row.score_parts,
    r.source = row.source,
    r.poi_count = row.poi_count,
    r.named_pois = row.named_pois,
    r.geometry = [c IN row.coordinates |
                  point({latitude: c[0], longitude: c[1]})]
WITH r, row
MATCH (t:Trailhead {trailhead_id: row.trailhead_id})
MERGE (r)-[:STARTS_FROM]->(t)
WITH r, row
UNWIND row.poi_ids AS poi_id
MATCH (p:POI {osm_id: poi_id})
MERGE (r)-[:PASSES]->(p)
"""


async def _candidates(
    db: Neo4jClient,
    graph_name: str,
    gh: GraphHopperClient | None,
    activity: str,
    trailhead: dict[str, Any],
    target_m: int,
    seeds: int,
) -> list[Any]:
    """Candidates from whichever source is configured.

    The two sources return the same RouteCandidate, so everything downstream —
    scoring, dedup, the POI map-back, persistence — is identical. That is what
    putting generation behind a seam bought.
    """
    if gh is None:
        return await generate_loops(
            db, graph_name, trailhead, float(target_m), max_candidates=seeds
        )
    start = (trailhead["lat"], trailhead["lon"])
    out = []
    for seed in range(seeds):
        candidate = await gh.round_trip(
            start, float(target_m), activity, seed, trailhead["trailhead_id"]
        )
        if candidate:
            out.append(candidate)
    return out


async def routes_for_trailhead(
    db: Neo4jClient,
    graph_name: str,
    trailhead: dict[str, Any],
    targets: list[int],
    seeds: int,
    keep: int,
    gh: GraphHopperClient | None = None,
    activity: str = "hike",
    min_length_fit: float = 0.0,
) -> tuple[list[dict[str, Any]], list[int]]:
    rows: list[dict[str, Any]] = []
    dropped: list[int] = []
    for target_m in targets:
        candidates = await _candidates(
            db, graph_name, gh, activity, trailhead, target_m, seeds
        )
        if not candidates:
            continue
        for rank, chosen in enumerate(select(candidates, keep=keep)):
            # Decline to STORE a route that answers a different question than
            # the one it was generated for. Scoring stays honest and ranks a
            # bad fit low, but the catalogue is data: filing a 21 km route as
            # the answer to "a 5 km loop" mislabels it, and a user asking for
            # 5 km would be handed it as though it fit.
            #
            # This is not the scorer filtering. Short loops genuinely do not
            # exist at some mountain trailheads -- the only paths out are long
            # -- and the honest response is to have no 5 km route there rather
            # than a 4x one. Drops are counted and reported per target.
            if chosen.scores.get("length", 0.0) < min_length_fit:
                dropped.append(target_m)
                continue
            pois = await pois_along_route(db, chosen.coordinates, radius_m=150)
            summary = summarize_pois(pois)
            rows.append(
                {
                    # Activity is part of the identity: the same trailhead
                    # and distance yield a different route on foot and on a
                    # bike, and one must not overwrite the other.
                    "route_id": f"{chosen.trailhead_id}:{activity}:{target_m}:{rank}",
                    "activity": activity,
                    "trailhead_id": chosen.trailhead_id,
                    "target_m": float(target_m),
                    "distance_m": round(chosen.distance_m, 1),
                    "ascent_m": chosen.ascent_m,
                    "off_road_share": round(chosen.off_road_share, 4),
                    "retrace": round(chosen.retrace, 4),
                    "score": chosen.score,
                    # Stored so a route's ranking can be explained later without
                    # re-running the generator.
                    "score_parts": [
                        f"{k}={v}" for k, v in sorted(chosen.scores.items())
                    ],
                    "source": chosen.source,
                    "hike_rating": chosen.ratings.get("hike_rating"),
                    "mtb_rating": chosen.ratings.get("mtb_rating"),
                    "poi_count": summary["count"],
                    "named_pois": [p["name"] for p in summary["named"]][:12],
                    "poi_ids": [p["osm_id"] for p in pois],
                    "coordinates": [[lat, lon] for lat, lon in chosen.coordinates],
                }
            )
    return rows, dropped


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="only the top N trailheads")
    parser.add_argument(
        "--min-off-road",
        type=float,
        default=0.3,
        help="skip trailheads below this off-road share (0.3 excludes urban)",
    )
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--targets", default="5000,10000,15000,20000")
    parser.add_argument(
        "--activity",
        default="hike",
        choices=["hike", "mtb"],
        help="which GraphHopper profile to generate for; also stored on the route",
    )
    parser.add_argument(
        "--source",
        default="graphhopper",
        choices=["graphhopper", "local"],
        help="graphhopper brings elevation and per-activity profiles; local is "
        "the fallback that works without the service",
    )
    parser.add_argument(
        "--min-length-fit",
        type=float,
        default=0.35,
        help="refuse to store a route whose length score is below this "
        "(0.35 is roughly within a third of the target); 0 stores everything",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    targets = [int(t) for t in args.targets.split(",")]

    settings = get_settings()
    graph_name = f"routes_{uuid4().hex[:12]}"

    gh: GraphHopperClient | None = None
    if args.source == "graphhopper":
        gh = GraphHopperClient(settings.graphhopper_url)
        try:
            profiles = await gh.available_profiles()
        except Exception as error:  # noqa: BLE001 — any failure means unusable
            print(
                f"GraphHopper unreachable at {settings.graphhopper_url}: "
                f"{str(error)[:120]}"
            )
            print(
                "Start it with: docker compose --env-file .env "
                "-f infra/docker-compose.yml up -d graphhopper"
            )
            return
        if args.activity not in profiles:
            # Fail rather than fall back: silently generating a foot catalogue
            # labelled mtb is worse than generating nothing.
            print(f"profile {args.activity!r} not served; available: {profiles}")
            return
        print(
            f"source: GraphHopper ({settings.graphhopper_url}), profile {args.activity}"
        )
    else:
        print(f"source: local routing, activity labelled {args.activity}")
    all_rows: list[dict[str, Any]] = []
    barren: list[str] = []
    dropped_by_target: dict[int, int] = {}

    async with Neo4jClient() as db:
        trailheads = await db.run(FETCH_TRAILHEADS, min_off_road=args.min_off_road)
        if args.limit:
            trailheads = trailheads[: args.limit]
        print(
            f"trailheads at or above {args.min_off_road:.0%} off-road: "
            f"{len(trailheads)}"
        )
        print(
            f"targets: {[t // 1000 for t in targets]} km, "
            f"{args.seeds} seeds, keeping {args.keep}\n"
        )
        if not trailheads:
            return

        min_lat, min_lon, max_lat, max_lon = settings.bbox
        try:
            projected = await db.run_named(
                "graph_project_routing",
                graph_name=graph_name,
                min_lat=min_lat,
                min_lon=min_lon,
                max_lat=max_lat,
                max_lon=max_lon,
            )
            if not projected or not projected[0].get("nodes"):
                print("Projection empty — is the region ingested and GDS loaded?")
                return

            for index, trailhead in enumerate(trailheads, start=1):
                rows, dropped = await routes_for_trailhead(
                    db,
                    graph_name,
                    trailhead,
                    targets,
                    args.seeds,
                    args.keep,
                    gh=gh,
                    activity=args.activity,
                    min_length_fit=args.min_length_fit,
                )
                for target_m in dropped:
                    dropped_by_target[target_m] = dropped_by_target.get(target_m, 0) + 1
                name = trailhead["name"] or f"({trailhead['trailhead_id']})"
                if rows:
                    best = max(r["score"] for r in rows)
                    print(
                        f"  [{index}/{len(trailheads)}] {name[:34]:<34} "
                        f"{len(rows):>2} routes  best score {best:.2f}"
                    )
                else:
                    barren.append(name)
                    print(f"  [{index}/{len(trailheads)}] {name[:34]:<34}  none")
                all_rows.extend(rows)
        finally:
            with suppress(Neo4jError):
                await db.run_named("graph_drop_routing", graph_name=graph_name)

        print(f"\ngenerated {len(all_rows)} routes")
        if dropped_by_target:
            # Named rather than swallowed: many drops at one target means loops
            # of that length do not exist at these trailheads, which is a real
            # coverage fact, not a tuning knob to hide.
            print("dropped for poor length fit (loops that size may not exist there):")
            for target_m, count in sorted(dropped_by_target.items()):
                print(f"  {target_m / 1000:>4.0f} km  {count:>4}")
        if barren:
            # Named, not swallowed: a trailhead that produces nothing is a
            # coverage hole, and finding it here is far cheaper than a user
            # finding it.
            print(f"trailheads that produced nothing: {len(barren)}")
            for name in barren[:8]:
                print(f"  - {name}")

        if all_rows:
            by_target: dict[float, int] = {}
            for row in all_rows:
                by_target[row["target_m"]] = by_target.get(row["target_m"], 0) + 1
            print("\nby target distance:")
            for target_m, count in sorted(by_target.items()):
                print(f"  {target_m / 1000:>4.0f} km  {count:>4}")
            top = sorted(all_rows, key=lambda r: -r["score"])[:5]
            print("\nbest routes:")
            for row in top:
                pois = ", ".join(row["named_pois"][:3]) or "(no named POIs)"
                print(
                    f"  {row['score']:.2f}  {row['distance_m'] / 1000:5.1f} km  "
                    f"off-road {row['off_road_share']:5.0%}  {pois[:52]}"
                )

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return

        await db.run_batched(MERGE_ROUTES, all_rows, batch_size=100)
        await db.run(
            DELETE_STALE_ROUTES,
            trailhead_ids=[t["trailhead_id"] for t in trailheads],
            activity=args.activity,
            keep_ids=[row["route_id"] for row in all_rows],
        )
        print(f"\nwrote {len(all_rows)} (:Route) nodes")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
