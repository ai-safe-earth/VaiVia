"""Trail search and detail endpoints.

Every query runs a named template from graph/queries.cypher with parameters —
no Cypher is built from user input here or anywhere downstream.
"""

import logging

from fastapi import APIRouter, HTTPException

from api.deps import DbDep
from api.models import (
    GeoJsonGeometry,
    TrailDetail,
    TrailGeoJson,
    TrailSearchRequest,
    TrailSearchResponse,
    TrailSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trails", tags=["trails"])


@router.post("/search", response_model=TrailSearchResponse)
async def search_trails(request: TrailSearchRequest, db: DbDep) -> TrailSearchResponse:
    """Structured trail search. Phase 4's TrailSearchIntent maps 1:1 onto this."""
    rows = await db.run_named(
        "search_trails",
        activity=request.activity,
        min_difficulty_level=request.min_difficulty_level,
        max_difficulty_level=request.max_difficulty_level,
        min_distance_m=request.min_distance_m,
        max_distance_m=request.max_distance_m,
        max_elevation_gain_m=request.max_elevation_gain_m,
        poi_types=list(request.poi_types),
        surface_exclusions=list(request.surface_exclusions),
        season=request.season,
        exclude_hazards=list(request.exclude_hazards),
        region=request.region,
        limit=request.limit,
    )
    trails = [TrailSummary.model_validate(row) for row in rows]
    logger.info("trail search", extra={"results": len(trails)})
    return TrailSearchResponse(count=len(trails), trails=trails)


@router.get("/{trail_id}", response_model=TrailDetail)
async def get_trail(trail_id: str, db: DbDep) -> TrailDetail:
    rows = await db.run_named("trail_by_id", trail_id=trail_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"trail {trail_id!r} not found")
    row = dict(rows[0])
    # OPTIONAL MATCH yields a [{name: null, type: null}] placeholder when a trail
    # passes no POI; drop those so the contract stays clean.
    row["pois"] = [p for p in row.get("pois") or [] if p.get("type")]
    return TrailDetail.model_validate(row)


@router.get("/{trail_id}/geojson", response_model=TrailGeoJson)
async def get_trail_geojson(trail_id: str, db: DbDep) -> TrailGeoJson:
    """Map payload: one MultiLineString of the trail's segments, in trail order."""
    rows = await db.run_named("trail_geometry", trail_id=trail_id)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"trail {trail_id!r} has no matched segments",
        )
    lines = [row["coordinates"] for row in rows if row.get("coordinates")]
    confidences = [
        row["match_confidence"]
        for row in rows
        if row.get("match_confidence") is not None
    ]
    return TrailGeoJson(
        geometry=GeoJsonGeometry(coordinates=lines),
        properties={
            "trail_id": trail_id,
            "segment_count": len(lines),
            "total_length_m": sum(row.get("length_m") or 0 for row in rows),
            "min_match_confidence": min(confidences) if confidences else None,
        },
    )
