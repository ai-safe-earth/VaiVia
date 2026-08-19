"""Generate candidate loops from a trailhead. Stage 2 of the route pipeline.

Deliberately dumb and prolific: generation is cheap offline, and quality comes
from scoring and dedup afterwards (graph/route_scoring.py). Ten mediocre
candidates that a scorer can rank are worth more than one clever one.

The method is seed-and-stitch. A loop of perimeter L approximates a circle of
radius L/2pi, so waypoints are drawn from a ring at that radius, bucketed by
bearing, and paired roughly 120 degrees apart so the three Dijkstra legs form a
triangle rather than an out-and-back.

`docs/routing-engine.md` decides in favour of GraphHopper's `round_trip` over
this — it produces 0-3% retrace against our ~20%, and knows about elevation.
This exists because it works against the graph we already have, and because the
pipeline is written to consume geometry from either. When GraphHopper is stood
up as a service, add a second source with this signature and the rest of the
pipeline does not change.
"""

import logging
import math
from collections import defaultdict
from typing import Any

from neo4j.exceptions import Neo4jError

from core.geo import LatLon
from graph.route_scoring import RouteCandidate, polyline_length_m, retrace_fraction

RING_TOLERANCE = 0.35
BEARING_BUCKETS = 12
OFF_ROAD_TYPES = {"path", "track", "bridleway", "footway", "steps", "cycleway"}

logger = logging.getLogger(__name__)


def bearing(origin: LatLon, point: LatLon) -> float:
    phi1, phi2 = math.radians(origin[0]), math.radians(point[0])
    dlambda = math.radians(point[1] - origin[1])
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        dlambda
    )
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def waypoint_pairs(
    ring: list[dict[str, Any]], origin: LatLon, limit: int
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pairs of ring candidates about 120 degrees apart, one per bearing sector.

    Without the bearing spread every leg leaves along whatever corridor happens
    to be shortest, and the three legs collapse onto the same ground.
    """
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for node in ring:
        sector = int(
            bearing(origin, (node["lat"], node["lon"])) // (360 / BEARING_BUCKETS)
        )
        buckets[sector].append(node)

    offset = BEARING_BUCKETS // 3
    pairs = []
    for sector in sorted(buckets):
        partner = buckets.get((sector + offset) % BEARING_BUCKETS)
        if partner:
            pairs.append((buckets[sector][0], partner[0]))
    return pairs[:limit]


async def _leg(db: Any, graph_name: str, start: str, end: str) -> dict[str, Any] | None:
    if start == end:
        return None
    try:
        rows = await db.run_named(
            "route_gds_dijkstra", graph_name=graph_name, start_node=start, end_node=end
        )
    except Neo4jError as error:
        # One unroutable waypoint costs this candidate, not the rest of the
        # run. A batch over hundreds of trailheads that aborts on the second
        # is worse than one that reports a few gaps at the end.
        logger.warning("leg %s -> %s failed: %s", start, end, str(error)[:120])
        return None
    return rows[0] if rows else None


async def build_loop(
    db: Any,
    graph_name: str,
    start_node: str,
    waypoints: tuple[str, str],
    trailhead_id: str,
    target_m: float,
) -> RouteCandidate | None:
    """Stitch start -> A -> B -> start into one candidate, or None if unreachable."""
    a, b = waypoints
    legs = []
    for leg_start, leg_end in ((start_node, a), (a, b), (b, start_node)):
        leg = await _leg(db, graph_name, leg_start, leg_end)
        if leg is None:
            return None
        legs.append(leg)

    coordinates: list[LatLon] = []
    node_ids: list[str] = []
    for index, leg in enumerate(legs):
        # Drop the node shared with the previous leg.
        pts = leg["coordinates"] if index == 0 else leg["coordinates"][1:]
        ids = leg["node_ids"] if index == 0 else leg["node_ids"][1:]
        # GDS returns [lon, lat]; the rest of the codebase is (lat, lon).
        coordinates.extend((p[1], p[0]) for p in pts)
        node_ids.extend(ids)

    details = await db.run_named("route_edge_details", node_ids=node_ids)
    if not details:
        return None

    # Real length comes from distance_m, never from Dijkstra's totalCost, which
    # is comfort-penalised and in no real unit (docs/fragilities.md #10).
    distance_m = sum(d["distance_m"] for d in details)
    if distance_m <= 0:
        return None
    off_road_m = sum(
        d["distance_m"] for d in details if d["highway_type"] in OFF_ROAD_TYPES
    )
    ascent = sum(d["gain_m"] for d in details)

    return RouteCandidate(
        trailhead_id=trailhead_id,
        target_m=target_m,
        coordinates=coordinates,
        distance_m=distance_m,
        off_road_share=off_road_m / distance_m,
        retrace=retrace_fraction(coordinates),
        # Elevation is not backfilled yet (fragility #6), so a flat 0 means
        # "unknown" rather than "flat". Passing None keeps the scorer neutral
        # instead of punishing every local route for missing instrumentation.
        ascent_m=ascent if ascent > 0 else None,
        source="local",
    )


async def generate_loops(
    db: Any,
    graph_name: str,
    trailhead: dict[str, Any],
    target_m: float,
    max_candidates: int = 8,
) -> list[RouteCandidate]:
    """Candidate loops of roughly `target_m` starting and ending at a trailhead."""
    origin: LatLon = (trailhead["lat"], trailhead["lon"])
    radius = target_m / (2 * math.pi)

    ring = await db.run_named(
        "intersections_in_ring",
        lat=origin[0],
        lon=origin[1],
        min_m=radius * (1 - RING_TOLERANCE),
        max_m=radius * (1 + RING_TOLERANCE),
        component_id=trailhead.get("component_id"),
        limit=2000,
    )
    if not ring:
        return []

    out: list[RouteCandidate] = []
    for wp_a, wp_b in waypoint_pairs(ring, origin, max_candidates):
        candidate = await build_loop(
            db,
            graph_name,
            trailhead["node_id"],
            (wp_a["osm_node_id"], wp_b["osm_node_id"]),
            trailhead["trailhead_id"],
            target_m,
        )
        if candidate:
            out.append(candidate)
    return out


def polyline_km(coordinates: list[LatLon]) -> float:
    return polyline_length_m(coordinates) / 1000.0
