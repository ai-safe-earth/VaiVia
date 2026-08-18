"""Give catalogue routes a name and a duration.

A generated route's only identifier is `1461822581:hike:15000:0`, which is not
something a person can be offered. Naming it after the trailhead does not
rescue that — only 37 of 266 trailheads have a name — but every route already
carries `-[:PASSES]->(:POI)` edges holding both a name and a type, so the route
can be named after the best thing it passes. "Monte Ocone" beats any id and any
car park.

Duration was never computed for routes either, though `core/durations.py` has
implemented DIN 33466 all along and only ever needed ascent. A loop returns to
where it started, so descent equals ascent — which makes both durations
computable from what is already stored.

Deriving rather than regenerating is the point: the geometry, POIs and ratings
are unchanged, so this reads and writes properties instead of spending hours of
GraphHopper calls.

Idempotent: recomputes from the graph every run and overwrites, so it is safe
to re-run after ingestion or after a catalogue rebuild.

Run from backend/ with Neo4j up:
    uv run python -m scripts.name_routes
    uv run python -m scripts.name_routes --dry-run
"""

import argparse
import asyncio
import logging
from typing import Any

from core.durations import hike_duration_min, mtb_duration_min
from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# What makes a route worth naming after, best first. A summit or a pass is what
# someone remembers and asks for again; a spring beside the path is not. Types
# absent here are never used as a name even when they are the only POI.
NAME_PRIORITY = (
    "peak",
    "saddle",
    "lake",
    "castle",
    "waterfall",
    "chapel",
    "hut",
    "viewpoint",
    "beach",
    "cave",
    "ruins",
)

# Inverse of MTB_RATING_BY_LEVEL in chat/orchestrator.py. mtb:scale is 0-6 and
# our difficulty is 1-4, so the mapping is lossy in both directions; this is
# the reading that keeps the boundaries in the same places.
MTB_LEVEL_BY_RATING = {0: 1, 1: 1, 2: 2, 3: 3, 4: 3, 5: 4, 6: 4}
DEFAULT_MTB_LEVEL = 2

FETCH_ROUTES = """
MATCH (r:Route)
OPTIONAL MATCH (r)-[:PASSES]->(p:POI)
WHERE p.name IS NOT NULL
WITH r, collect({name: p.name, type: p.type}) AS pois
OPTIONAL MATCH (r)-[:STARTS_FROM]->(th:Trailhead)
RETURN r.route_id AS route_id,
       r.activity AS activity,
       r.distance_m AS distance_m,
       r.ascent_m AS ascent_m,
       r.mtb_rating AS mtb_rating,
       th.name AS trailhead_name,
       pois AS pois
"""

STORE = """
UNWIND $rows AS row
MATCH (r:Route {route_id: row.route_id})
SET r.name = row.name,
    r.duration_hike_min = row.duration_hike_min,
    r.duration_mtb_min = row.duration_mtb_min
"""


def route_name(
    pois: list[dict[str, Any]], trailhead_name: str | None
) -> tuple[str | None, str]:
    """Best available name, and where it came from.

    Returns None rather than inventing one. A route with no named feature and an
    unnamed trailhead genuinely has no name, and the card renders its distance
    instead — which is honest, where "Route 4312828180" is not.
    """
    for wanted in NAME_PRIORITY:
        for poi in pois:
            if poi.get("type") == wanted and poi.get("name"):
                return poi["name"], wanted
    if trailhead_name:
        return trailhead_name, "trailhead"
    return None, "none"


def mtb_level(mtb_rating: int | None) -> int:
    if mtb_rating is None:
        return DEFAULT_MTB_LEVEL
    return MTB_LEVEL_BY_RATING.get(mtb_rating, DEFAULT_MTB_LEVEL)


def durations(row: dict[str, Any]) -> tuple[int, int]:
    """Both durations, regardless of activity.

    Storing both mirrors (:Trail) and lets the frontend's existing
    primaryDuration() helper work unchanged — it picks by activity and expects
    both fields present.

    Descent is passed as ascent: the route is a loop, so it returns to its
    starting elevation, and DIN 33466 costs descent separately from ascent.
    """
    distance_m = row["distance_m"] or 0.0
    ascent_m = row["ascent_m"]
    return (
        hike_duration_min(distance_m, ascent_m, ascent_m),
        mtb_duration_min(distance_m, ascent_m, mtb_level(row["mtb_rating"])),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    async with Neo4jClient() as db:
        routes = await db.run(FETCH_ROUTES)
        print(f"routes: {len(routes)}")
        if not routes:
            return

        rows: list[dict[str, Any]] = []
        by_source: dict[str, int] = {}
        for row in routes:
            name, source = route_name(row["pois"] or [], row["trailhead_name"])
            by_source[source] = by_source.get(source, 0) + 1
            hike_min, mtb_min = durations(row)
            rows.append(
                {
                    "route_id": row["route_id"],
                    "name": name,
                    "duration_hike_min": hike_min,
                    "duration_mtb_min": mtb_min,
                }
            )

        named = sum(1 for r in rows if r["name"])
        print(f"named: {named}/{len(rows)} ({named / len(rows):.0%})\n")
        print("name taken from:")
        for source, count in sorted(by_source.items(), key=lambda kv: -kv[1]):
            print(f"  {source:<12} {count:>5}")

        sample = [r for r in rows if r["name"]][:8]
        print("\nsample:")
        for row in sample:
            print(
                f"  {row['name'][:38]:<38} "
                f"hike {row['duration_hike_min']:>4} min  "
                f"mtb {row['duration_mtb_min']:>4} min"
            )

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return
        await db.run_batched(STORE, rows, batch_size=500)
        print(f"\nwrote name and durations to {len(rows)} routes")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
