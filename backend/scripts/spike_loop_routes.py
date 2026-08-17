"""Spike: can we BUILD a loop of a requested length, instead of finding a track?

The open product question is whether VaiVia can answer "a 15 km loop from here,
moderate, past a hut" — which neither a trail catalogue nor shortest-path
routing can do. Dijkstra from A back to A is zero-length, so a loop has to be
constructed.

Approach here (seed-and-stitch, the cheapest thing that could work):

  1. Snap the start to an Intersection.
  2. A loop of perimeter L approximates a circle of radius L/(2*pi). Draw
     candidate waypoints from a ring at that radius (intersections_in_ring).
  3. Bucket them by bearing and pick pairs roughly 120 degrees apart, so the
     three legs form a triangle rather than an out-and-back.
  4. Route start -> A -> B -> start with Dijkstra over one GDS projection.
  5. Score: how close to the target length, and how much of the loop retraces
     itself (an out-and-back scores badly and should be rejected).

What this measures: whether the OSM network in a real region is connected and
dense enough that constrained loop construction is tractable. It is a spike,
not a design — no endpoint, no intent, nothing wired into chat.

Run from backend/ with Neo4j up and Lecco ingested:
    uv run python -m scripts.spike_loop_routes
    uv run python -m scripts.spike_loop_routes --targets 8000,15000 --out loop.json
"""

import argparse
import asyncio
import json
import logging
import math
from collections import defaultdict
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo4j.exceptions import Neo4jError

from core.config import get_settings
from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# Lecco waterfront, the same anchor the frontend map opens on.
START_LAT, START_LON = 45.856, 9.393

RING_TOLERANCE = 0.35  # accept waypoints at radius * (1 +/- this)
BEARING_BUCKETS = 12  # 30-degree sectors
LENGTH_TOLERANCE = 0.25  # a loop counts as "on target" within +/- this
MAX_PAIRS_PER_TARGET = 10


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing in degrees, 0-360."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        dlambda
    )
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def undirected_edges(node_ids: list[str]) -> set[frozenset[str]]:
    """Edge set of a node sequence, direction-insensitive.

    Retracing a leg reuses the same physical path in reverse, so overlap has to
    be measured on unordered pairs or an out-and-back looks like a clean loop.
    """
    return {frozenset((node_ids[i], node_ids[i + 1])) for i in range(len(node_ids) - 1)}


async def route_leg(
    db: Neo4jClient, graph_name: str, start: str, end: str
) -> dict[str, Any] | None:
    if start == end:
        return None
    rows = await db.run_named(
        "route_gds_dijkstra", graph_name=graph_name, start_node=start, end_node=end
    )
    return rows[0] if rows else None


async def build_loop(
    db: Neo4jClient, graph_name: str, start_node: str, waypoints: tuple[str, str]
) -> dict[str, Any] | None:
    """Route start -> A -> B -> start; None if any leg is unreachable."""
    a, b = waypoints
    legs = []
    for leg_start, leg_end in ((start_node, a), (a, b), (b, start_node)):
        leg = await route_leg(db, graph_name, leg_start, leg_end)
        if leg is None:
            return None
        legs.append(leg)

    total_m = sum(leg["total_m"] for leg in legs)
    coordinates: list[list[float]] = []
    node_ids: list[str] = []
    for index, leg in enumerate(legs):
        # Drop the shared node between consecutive legs.
        coordinates.extend(leg["coordinates"] if index == 0 else leg["coordinates"][1:])
        node_ids.extend(leg["node_ids"] if index == 0 else leg["node_ids"][1:])

    edges = [undirected_edges(leg["node_ids"]) for leg in legs]
    all_edges: set[frozenset[str]] = set().union(*edges)
    total_edge_count = sum(len(e) for e in edges)
    # 0.0 = every edge walked once; 0.5 = half the loop is retraced.
    overlap = 1 - (len(all_edges) / total_edge_count) if total_edge_count else 1.0

    return {
        "total_m": total_m,
        "overlap": overlap,
        "coordinates": coordinates,
        "node_ids": node_ids,
    }


async def main() -> tuple[str | None, list[dict[str, Any]]]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--targets",
        default="10000,15000,20000",
        help="comma-separated target loop lengths in metres",
    )
    parser.add_argument("--out", help="write the best loop of each target as GeoJSON")
    parser.add_argument(
        "--start", help="start as 'lat,lon' (default: Lecco waterfront)"
    )
    args = parser.parse_args()
    targets = [float(t) for t in args.targets.split(",")]

    global START_LAT, START_LON
    if args.start:
        START_LAT, START_LON = (float(p) for p in args.start.split(","))

    settings = get_settings()
    db = Neo4jClient()
    await db.connect()

    graph_name = f"loopspike_{uuid4().hex[:12]}"
    features: list[dict[str, Any]] = []

    try:
        snapped = await db.run_named(
            "nearest_intersection",
            lat=START_LAT,
            lon=START_LON,
            radius_m=settings.snap_radius_m,
        )
        if not snapped:
            print(f"No intersection within {settings.snap_radius_m} m of the start.")
            return args.out, features
        start_node = snapped[0]["osm_node_id"]
        print(f"start intersection: {start_node} ({START_LAT}, {START_LON})\n")

        min_lat, min_lon, max_lat, max_lon = settings.bbox
        projected = await db.run_named(
            "graph_project_routing",
            graph_name=graph_name,
            min_lat=min_lat,
            min_lon=min_lon,
            max_lat=max_lat,
            max_lon=max_lon,
        )
        if not projected or not projected[0].get("nodes"):
            print("GDS projection empty — is the region ingested and GDS loaded?")
            return args.out, features
        print(
            f"projected {projected[0]['nodes']} intersections / "
            f"{projected[0]['rels']} edges\n"
        )

        for target_m in targets:
            radius = target_m / (2 * math.pi)
            print(
                f"=== target {target_m / 1000:.0f} km (ring radius {radius:.0f} m) ==="
            )

            ring = await db.run_named(
                "intersections_in_ring",
                lat=START_LAT,
                lon=START_LON,
                min_m=radius * (1 - RING_TOLERANCE),
                max_m=radius * (1 + RING_TOLERANCE),
                limit=4000,
            )
            if not ring:
                print("  no candidate waypoints in the ring\n")
                continue

            buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for node in ring:
                b = bearing(START_LAT, START_LON, node["lat"], node["lon"])
                buckets[int(b // (360 / BEARING_BUCKETS))].append(node)
            print(
                f"  {len(ring)} candidates in {len(buckets)}/{BEARING_BUCKETS} "
                "bearing sectors"
            )

            # Pair sectors ~120 degrees apart so the legs form a triangle.
            offset = BEARING_BUCKETS // 3
            pairs = [
                (buckets[i][0], buckets[(i + offset) % BEARING_BUCKETS][0])
                for i in sorted(buckets)
                if buckets.get((i + offset) % BEARING_BUCKETS)
            ][:MAX_PAIRS_PER_TARGET]

            loops = []
            for wp_a, wp_b in pairs:
                loop = await build_loop(
                    db,
                    graph_name,
                    start_node,
                    (wp_a["osm_node_id"], wp_b["osm_node_id"]),
                )
                if loop:
                    loops.append(loop)

            on_target = [
                loop
                for loop in loops
                if abs(loop["total_m"] - target_m) / target_m <= LENGTH_TOLERANCE
            ]
            print(f"  routed {len(loops)}/{len(pairs)} candidate loops")
            print(f"  within +/-{LENGTH_TOLERANCE:.0%} of target: {len(on_target)}")

            if not loops:
                print()
                continue

            loops.sort(
                key=lambda x: (abs(x["total_m"] - target_m) / target_m, x["overlap"])
            )
            for loop in loops[:5]:
                flag = "  <- best" if loop is loops[0] else ""
                print(
                    f"    {loop['total_m'] / 1000:6.1f} km  "
                    f"retraced {loop['overlap']:5.1%}  "
                    f"{len(loop['node_ids']):>4} nodes{flag}"
                )

            best = loops[0]
            details = await db.run_named(
                "route_edge_details", node_ids=best["node_ids"]
            )
            if details:
                gain = sum(d["gain_m"] for d in details)
                surfaces = defaultdict(int)
                for d in details:
                    surfaces[d["surface"] or "unknown"] += 1
                top = ", ".join(
                    f"{k}={v}"
                    for k, v in sorted(surfaces.items(), key=lambda kv: -kv[1])[:4]
                )
                print(f"  best loop climb: {gain:.0f} m; surfaces: {top}")

            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "target_km": target_m / 1000,
                        "actual_km": round(best["total_m"] / 1000, 2),
                        "retraced": round(best["overlap"], 3),
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": best["coordinates"],
                    },
                }
            )
            print()

    finally:
        with suppress(Neo4jError):
            await db.run_named("graph_drop_routing", graph_name=graph_name)
        await db.close()

    # Returned rather than written here: the file write is blocking, and the
    # caller is sync, so it belongs outside the event loop.
    return args.out, features


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    out_path, loops = asyncio.run(main())
    if out_path and loops:
        Path(out_path).write_text(
            json.dumps({"type": "FeatureCollection", "features": loops}, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {len(loops)} loops to {out_path}")
