"""POI-to-POI routing on the Intersection graph.

Flow: resolve both POIs by name -> snap each to its nearest intersection within
snap_radius_m (spatial index, never a full scan) -> route over CONNECTS_TO
only. Semantic edges never enter a path expression.

Routing prefers GDS Dijkstra (distance-weighted: verified live to find shorter
routes than hop-count shortestPath) over a per-request bbox projection that is
always dropped afterwards. shortestPath remains as the fallback because GDS is
genuinely absent sometimes — the plugin fetches its manifest over the network at
container start and silently skips installation when that fails, which we have
observed on cold Docker starts. A routing outage should not follow from that.
"""

import logging
from contextlib import suppress
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from neo4j.exceptions import Neo4jError

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


async def _route_via_gds(
    db: DbDep, start_node: str, end_node: str, max_distance_m: float
) -> dict | None:
    """Distance-weighted route via GDS, or None to fall back to shortestPath.

    None covers both "GDS unavailable" and "no route" — Dijkstra's total is the
    minimum possible distance, so when it finds nothing (or only something over
    the cap), the shortestPath fallback cannot do better and yields the same
    404, just via one redundant query. Folding the cases keeps the failure
    handling in one place.
    """
    settings = get_settings()
    min_lat, min_lon, max_lat, max_lon = settings.bbox
    graph_name = f"routing_{uuid4().hex[:12]}"  # per-request: concurrency-safe
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
            return None
        rows = await db.run_named(
            "route_gds_dijkstra",
            graph_name=graph_name,
            start_node=start_node,
            end_node=end_node,
        )
    except Neo4jError as error:
        logger.warning(
            "GDS routing unavailable, falling back to shortestPath",
            extra={"error": str(error)[:200]},
        )
        return None
    finally:
        with suppress(Neo4jError):
            await db.run_named("graph_drop_routing", graph_name=graph_name)

    if not rows or rows[0]["total_m"] > max_distance_m:
        return None

    row = rows[0]
    details = await db.run_named("route_edge_details", node_ids=row["node_ids"])
    return {
        "total_m": row["total_m"],
        "gain_m": sum(d["gain_m"] for d in details) if details else None,
        "coordinates": row["coordinates"],
        "osm_way_ids": [d["osm_way_id"] for d in details],
        "surfaces": [d["surface"] for d in details],
    }


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

    gds_row = await _route_via_gds(db, start_node, end_node, max_distance_m)
    rows = (
        [gds_row]
        if gds_row
        else await db.run_named(
            "route_between_intersections",
            start_node=start_node,
            end_node=end_node,
            max_distance_m=max_distance_m,
        )
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
