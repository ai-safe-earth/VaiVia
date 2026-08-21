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

import json
import logging
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from neo4j.exceptions import Neo4jError

from api.deps import DbDep
from api.models import (
    GeoJsonLineString,
    PoiRef,
    RouteDetail,
    RouteGeoJson,
    RouteRequest,
    RouteResponse,
)
from core.config import get_settings
from core.text import lucene_escape

logger = logging.getLogger(__name__)

router = APIRouter(tags=["routing"])


async def _resolve_poi(db: DbDep, name: str) -> dict:
    query = lucene_escape(name).strip()
    rows = (
        await db.run_named("poi_by_name_fulltext", query=query, limit=1)
        if query
        else []
    )
    if not rows:
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
    """Comfort-weighted route via GDS, or None to fall back to shortestPath.

    Dijkstra minimises `cost_m` (distance scaled by how unpleasant the way is —
    core/comfort.py), so its `total_cost` is a penalised figure in no real unit.
    The distance reported to the caller is summed from `distance_m` over the
    resolved edges instead; returning totalCost would quote inflated lengths.

    None covers "GDS unavailable", "no route", and "the comfortable route is
    longer than the caller allowed". That last case genuinely falls through to
    shortestPath rather than 404ing: since we no longer minimise distance, a
    shorter — if roadier — route can exist inside the cap, and a walker who
    asked for at most 5 km would rather have it than nothing.
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

    if not rows:
        return None

    row = rows[0]
    details = await db.run_named("route_edge_details", node_ids=row["node_ids"])
    if not details:
        return None

    total_m = sum(d["distance_m"] for d in details)
    if total_m > max_distance_m:
        return None

    return {
        "total_m": total_m,
        "gain_m": sum(d["gain_m"] for d in details),
        "coordinates": row["coordinates"],
        "osm_way_ids": [d["osm_way_id"] for d in details],
        "surfaces": [d["surface"] for d in details],
    }


async def _load_route_document(route_id: str, db: DbDep) -> dict:
    """The 404/503 ladder every document-backed endpoint shares.

    The graph answers only "does this route exist", so an unknown id 404s
    before the filesystem is touched; a catalogue route whose document store
    is missing or unconfigured is a 503, never an empty answer — the
    semantic-search degradation rule.
    """
    rows = await db.run_named("route_exists", route_id=route_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown route {route_id!r}")

    settings = get_settings()
    if not settings.route_documents_dir:
        raise HTTPException(
            status_code=503,
            detail="route documents are not mounted (ROUTE_DOCUMENTS_DIR unset); "
            "geometry is unavailable until they are",
        )
    document_path = Path(settings.route_documents_dir) / f"{route_id}.json"
    if not document_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"route {route_id!r} is in the catalogue but its document is "
            "missing from the store — re-emit the documents",
        )
    return json.loads(document_path.read_text(encoding="utf-8"))


def _attribution(document: dict) -> str:
    return "; ".join(
        source["attribution"]
        for source in document.get("provenance", {}).get("sources", [])
    )


@router.get("/routes/{route_id}/geojson", response_model=RouteGeoJson)
async def get_route_geojson(route_id: str, db: DbDep) -> RouteGeoJson:
    """Map payload for one catalogue route, read from its ROUTE DOCUMENT.

    The graph deliberately carries no geometry (a second home for it is how
    two truths start — pipeline/export/catalogue.cypher); the document is
    canonical and this endpoint is the API serving it.
    """
    document = await _load_route_document(route_id, db)
    geometry = document["geometry"]
    if geometry["type"] != "LineString":
        # A route held in pieces is a MultiLineString; the response model is a
        # LineString. Serve the longest piece rather than a line across gaps,
        # and say so — the document remains the honest source.
        parts = sorted(geometry["coordinates"], key=len, reverse=True)
        coordinates = parts[0]
        note = f"multi-part route: longest of {len(parts)} pieces shown"
    else:
        coordinates = geometry["coordinates"]
        note = None
    properties = {
        "route_id": route_id,
        "point_count": len(coordinates),
        "attribution": _attribution(document),
    }
    if note:
        properties["note"] = note
    return RouteGeoJson(
        geometry=GeoJsonLineString(coordinates=coordinates),
        properties=properties,
    )


# A profile whose cumulative distance disagrees with the measured route length
# by more than this share is served as 'approximate': multi-piece OSM profiles
# are a concatenation across gaps (measured 2026-08-21: 16 of 752 differ >1%),
# and a clean-looking chart over that would be a quiet lie.
PROFILE_TOLERANCE = 0.01


@router.get("/routes/{route_id}/detail", response_model=RouteDetail)
async def get_route_detail(route_id: str, db: DbDep) -> RouteDetail:
    """The expandable card's payload, read from the ROUTE DOCUMENT.

    Geometry stays on /geojson (a map payload); this serves the rest of what
    the document knows — the altitude profile the elevation panel exists to
    draw, the measures, continuity, surface and places — with the same 404/503
    honesty ladder. profile_quality says whether the profile can be trusted as
    an along-route measure ('ok') or is a concatenation across the gaps of a
    multi-piece route ('approximate').
    """
    document = await _load_route_document(route_id, db)

    profile = document.get("profile")
    profile_quality = None
    if profile and profile.get("distance_m"):
        route_m = document["measures"]["distance_m"]
        profile_end = profile["distance_m"][-1]
        off_by = abs(profile_end - route_m) / route_m if route_m else 0.0
        profile_quality = "ok" if off_by <= PROFILE_TOLERANCE else "approximate"

    return RouteDetail(
        route_id=route_id,
        shape=document.get("shape"),
        profile=profile,
        profile_quality=profile_quality,
        measures=document["measures"],
        continuity=document["continuity"],
        surface=document["surface"],
        places=document.get("places", []),
        attribution=_attribution(document),
    )


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
