"""POI-to-POI routing on the Intersection graph.

Flow: resolve both POIs by name -> snap each to its nearest intersection within
snap_radius_m (spatial index, never a full scan) -> bounded shortestPath over
CONNECTS_TO only. Semantic edges never enter the path expression.

The GDS Dijkstra path (route_gds_dijkstra + graph_project_routing in
queries.cypher) is the large-graph upgrade; it is wired in once we can verify it
against a live GDS instance (docs/plan.md Phase 2).
"""

import logging

from fastapi import APIRouter, HTTPException

from api.deps import DbDep
from api.models import PoiRef, RouteRequest, RouteResponse
from core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["routing"])


async def _resolve_poi(db: DbDep, name: str) -> dict:
    rows = await db.run_named("poi_by_name", name=name, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail=f"no POI matching {name!r}")
    return rows[0]


async def _snap(db: DbDep, poi: dict, radius_m: float) -> str:
    rows = await db.run_named(
        "nearest_intersection", lat=poi["lat"], lon=poi["lon"], radius_m=radius_m
    )
    if not rows:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{poi['name']!r} is not within {radius_m:.0f} m of the trail "
                "network — no route can start or end there"
            ),
        )
    return rows[0]["osm_node_id"]


@router.post("/routes", response_model=RouteResponse)
async def route_between_pois(request: RouteRequest, db: DbDep) -> RouteResponse:
    settings = get_settings()
    max_distance_m = min(
        request.max_distance_m or settings.max_route_distance_m,
        settings.max_route_distance_m,
    )

    start_poi = await _resolve_poi(db, request.start)
    end_poi = await _resolve_poi(db, request.end)
    start_node = await _snap(db, start_poi, settings.snap_radius_m)
    end_node = await _snap(db, end_poi, settings.snap_radius_m)

    rows = await db.run_named(
        "route_between_intersections",
        start_node=start_node,
        end_node=end_node,
        max_distance_m=max_distance_m,
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no route under {max_distance_m / 1000:.1f} km found between "
                f"{start_poi['name']!r} and {end_poi['name']!r}"
            ),
        )

    row = rows[0]
    logger.info(
        "route found",
        extra={"total_m": row["total_m"], "start": start_node, "end": end_node},
    )
    return RouteResponse(
        total_distance_m=row["total_m"],
        elevation_gain_m=row.get("gain_m"),
        start_poi=PoiRef(name=start_poi["name"], type=start_poi["type"]),
        end_poi=PoiRef(name=end_poi["name"], type=end_poi["type"]),
        geometry={"type": "LineString", "coordinates": row["coordinates"]},
        surfaces=row.get("surfaces") or [],
    )
