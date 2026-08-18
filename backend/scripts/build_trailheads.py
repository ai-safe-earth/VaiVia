"""Derive (:Trailhead) nodes: where an outing can actually start.

A route is only useful if a person can reach its start, so the route pipeline
(docs/route-pipeline.md) is anchored on trailheads rather than on arbitrary
intersections. A trailhead is a point on the routing network that is:

  * within `--snap-m` of somewhere you can leave a car or step off a train
    (POI type `parking` or `station`),
  * inside the largest connected component, so a route from it can exist at all
    — seeding on an island is what returned 0/10 loops before
    docs/fragilities.md #9 was found,
  * distinct from other trailheads: Lecco has 1,511 car parks and nothing like
    1,511 places to start, because a row of parking areas along one road serves
    one trailhead. Clustering keeps the catalogue reviewable.

Each trailhead is scored by how much off-road ground is reachable near it, which
separates a mountain trailhead from a supermarket car park that happens to touch
the network. The score is descriptive, not a filter: what counts as "enough
trail" is a product decision, and dropping candidates here would hide it.

Derived data, so it lives on its own nodes rather than as labels on
(:Intersection) — re-running OSM ingestion must not clobber it, and a trailhead
carries its own properties.

Idempotent: MERGE on trailhead_id, which is the anchor intersection's node id.

Run from backend/ with Neo4j up, GDS loaded and a region ingested:
    uv run python -m scripts.build_trailheads
    uv run python -m scripts.build_trailheads --cluster-m 500 --dry-run
"""

import argparse
import asyncio
import logging
import math
from contextlib import suppress
from typing import Any
from uuid import uuid4

from neo4j.exceptions import Neo4jError

from core.config import get_settings
from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

ANCHOR_TYPES = ["parking", "station"]
OFF_ROAD = ["path", "track", "bridleway", "footway", "steps", "cycleway"]

# Writing the component id back means "can a route exist from here" becomes a
# property check instead of a graph algorithm per candidate.
WRITE_COMPONENTS = """
CALL gds.wcc.write($graph_name, {writeProperty: 'component_id'})
YIELD componentCount, nodePropertiesWritten
RETURN componentCount, nodePropertiesWritten
"""

LARGEST_COMPONENT = """
MATCH (i:Intersection) WHERE i.component_id IS NOT NULL
RETURN i.component_id AS component_id, count(*) AS size
ORDER BY size DESC LIMIT 1
"""

# One statement rather than a query per POI: the point index makes the inner
# nearest-neighbour cheap, and 1,500 round trips would not be.
SNAP_ANCHORS = """
MATCH (p:POI)
WHERE p.type IN $anchor_types
CALL (p) {
  MATCH (i:Intersection)
  WHERE point.distance(p.location, i.location) <= $snap_m
    AND i.component_id = $component_id
  RETURN i AS i, point.distance(p.location, i.location) AS d
  ORDER BY d ASC LIMIT 1
}
RETURN p.osm_id AS poi_id, p.name AS poi_name, p.type AS poi_type,
       i.osm_node_id AS node_id,
       i.location.latitude AS lat, i.location.longitude AS lon, d AS snap_m
"""

# Off-road share of the network around each candidate: what tells a mountain
# trailhead from a supermarket car park.
SCORE_ACCESS = """
UNWIND $rows AS row
MATCH (i:Intersection {osm_node_id: row.node_id})
CALL (i) {
  MATCH (a:Intersection)-[c:CONNECTS_TO]->(:Intersection)
  WHERE point.distance(a.location, i.location) <= $radius_m
  RETURN sum(CASE WHEN c.highway_type IN $off_road THEN c.distance_m ELSE 0.0 END)
           AS off_m,
         sum(c.distance_m) AS total_m
}
RETURN row.node_id AS node_id, off_m, total_m
"""

MERGE_TRAILHEADS = """
UNWIND $rows AS row
MERGE (t:Trailhead {trailhead_id: row.node_id})
SET t.name = row.name,
    t.location = point({latitude: row.lat, longitude: row.lon}),
    t.anchor_count = row.anchor_count,
    t.anchor_types = row.anchor_types,
    t.off_road_share = row.off_road_share,
    t.network_m = row.network_m
WITH t, row
MATCH (i:Intersection {osm_node_id: row.node_id})
MERGE (t)-[:STARTS_AT]->(i)
WITH t, row
UNWIND row.poi_ids AS poi_id
MATCH (p:POI {osm_id: poi_id})
MERGE (t)-[:SERVED_BY]->(p)
"""


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def cluster(anchors: list[dict[str, Any]], cluster_m: float) -> list[dict[str, Any]]:
    """Greedy single-link clustering, densest candidate first.

    Anchors are processed in descending order of how many other anchors sit
    within the radius, so a cluster forms around the busiest point rather than
    around whichever row the database happened to return first — otherwise the
    representative intersection is an artefact of ordering.
    """
    remaining = list(anchors)
    neighbours: dict[str, int] = {}
    for a in remaining:
        neighbours[a["node_id"]] = sum(
            1
            for b in remaining
            if haversine_m((a["lat"], a["lon"]), (b["lat"], b["lon"])) <= cluster_m
        )
    remaining.sort(key=lambda a: -neighbours[a["node_id"]])

    clusters: list[dict[str, Any]] = []
    claimed: set[str] = set()
    for seed in remaining:
        if seed["poi_id"] in claimed:
            continue
        members = [
            a
            for a in remaining
            if a["poi_id"] not in claimed
            and haversine_m((seed["lat"], seed["lon"]), (a["lat"], a["lon"]))
            <= cluster_m
        ]
        for m in members:
            claimed.add(m["poi_id"])
        names = [m["poi_name"] for m in members if m["poi_name"]]
        clusters.append(
            {
                "node_id": seed["node_id"],
                "lat": seed["lat"],
                "lon": seed["lon"],
                # Most car parks are unnamed; a named one in the cluster is the
                # best label we have, and None is honest when there is none.
                "name": names[0] if names else None,
                "anchor_count": len(members),
                "anchor_types": sorted({m["poi_type"] for m in members}),
                "poi_ids": [m["poi_id"] for m in members],
            }
        )
    return clusters


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snap-m", type=float, default=200.0)
    parser.add_argument("--cluster-m", type=float, default=400.0)
    parser.add_argument("--access-radius-m", type=float, default=750.0)
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    settings = get_settings()
    graph_name = f"trailheads_{uuid4().hex[:12]}"

    async with Neo4jClient() as db:
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
            written = await db.run(WRITE_COMPONENTS, graph_name=graph_name)
            print(
                f"components: {written[0]['componentCount']}, "
                f"ids written to {written[0]['nodePropertiesWritten']} intersections"
            )
        finally:
            with suppress(Neo4jError):
                await db.run_named("graph_drop_routing", graph_name=graph_name)

        largest = await db.run(LARGEST_COMPONENT)
        component_id = largest[0]["component_id"]
        print(f"largest component: {largest[0]['size']} intersections\n")

        anchors = await db.run(
            SNAP_ANCHORS,
            anchor_types=ANCHOR_TYPES,
            snap_m=args.snap_m,
            component_id=component_id,
        )
        print(f"anchors snapped to the network: {len(anchors)}")

        clusters = cluster(anchors, args.cluster_m)
        print(f"clustered into {len(clusters)} distinct trailheads\n")

        scores = await db.run(
            SCORE_ACCESS,
            rows=[{"node_id": c["node_id"]} for c in clusters],
            radius_m=args.access_radius_m,
            off_road=OFF_ROAD,
        )
        by_node = {s["node_id"]: s for s in scores}
        for c in clusters:
            s = by_node.get(c["node_id"], {})
            total = s.get("total_m") or 0.0
            c["off_road_share"] = (s.get("off_m") or 0.0) / total if total else 0.0
            c["network_m"] = total

        clusters.sort(key=lambda c: -c["off_road_share"])
        buckets = {"trail (>60%)": 0, "mixed (30-60%)": 0, "urban (<30%)": 0}
        for c in clusters:
            share = c["off_road_share"]
            key = (
                "trail (>60%)"
                if share > 0.6
                else "mixed (30-60%)" if share >= 0.3 else "urban (<30%)"
            )
            buckets[key] += 1
        print(f"off-road share of the network within {args.access_radius_m:.0f} m:")
        for k, v in buckets.items():
            print(f"  {k:<16} {v:>4}")

        print("\nbest trailheads by trail access:")
        for c in clusters[:8]:
            name = c["name"] or "(unnamed)"
            print(
                f"  {c['off_road_share']:5.0%}  {name[:38]:<38} "
                f"{c['anchor_count']:>3} anchors  "
                f"{c['network_m'] / 1000:5.1f} km nearby"
            )

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return
        await db.run_batched(MERGE_TRAILHEADS, clusters, batch_size=200)
        print(f"\nwrote {len(clusters)} (:Trailhead) nodes")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
