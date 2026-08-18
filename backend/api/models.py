"""Request/response models. These are the backend's contract with the gateway.

Distances are metres and durations minutes throughout (schema.cypher
conventions); the frontend converts for display.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

Activity = Literal["mtb", "hike", "mixed"]
# What a user can ask to pass or reach. Deliberately a subset of the POI types
# in the graph: `parking` is ingested as a route ANCHOR (somewhere to leave the
# car) and nobody asks for a walk past a car park, so it is not offered here.
# See ingestion/osm_extract.py POI_TAG_MAP and docs/route-pipeline.md.
PoiType = Literal[
    "lake",
    "hut",
    "campsite",
    "station",
    "bathing_water",
    "viewpoint",
    "peak",
    "saddle",
    "beach",
    "spring",
    "cave",
    "waterfall",
    "chapel",
    "castle",
    "ruins",
    "picnic_site",
]
Season = Literal["spring", "summer", "autumn", "winter"]


class PoiRef(BaseModel):
    name: str | None = None
    type: str


class TrailSummary(BaseModel):
    id: str
    name: str
    activity: str
    difficulty: str
    difficulty_level: int
    difficulty_notes: str | None = None
    landscape_description: str | None = None
    total_distance_m: float
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    duration_hike_min: int | None = None
    duration_mtb_min: int | None = None
    best_seasons: list[str] = Field(default_factory=list)
    seasonal_hazards: list[str] = Field(default_factory=list)
    trailforks_url: str | None = None
    pois: list[PoiRef] = Field(default_factory=list)


class TrailDetail(TrailSummary):
    description: str | None = None


class TrailSearchRequest(BaseModel):
    """Mirrors the Phase 4 TrailSearchIntent: the chat layer fills this in."""

    activity: Activity | None = None
    min_difficulty_level: Annotated[int, Field(ge=1, le=4)] | None = None
    max_difficulty_level: Annotated[int, Field(ge=1, le=4)] | None = None
    min_distance_m: Annotated[float, Field(ge=0)] | None = None
    max_distance_m: Annotated[float, Field(ge=0)] | None = None
    min_elevation_gain_m: Annotated[float, Field(ge=0)] | None = None
    max_elevation_gain_m: Annotated[float, Field(ge=0)] | None = None
    poi_types: list[PoiType] = Field(default_factory=list)
    surface_exclusions: list[str] = Field(default_factory=list)
    season: Season | None = None
    exclude_hazards: list[str] = Field(default_factory=list)
    region: str | None = None
    limit: Annotated[int, Field(ge=1, le=50)] = 10


class TrailSearchResponse(BaseModel):
    count: int
    trails: list[TrailSummary]


class SemanticSearchRequest(BaseModel):
    query: Annotated[str, Field(min_length=3, max_length=500)]
    limit: Annotated[int, Field(ge=1, le=20)] = 5


class ScoredTrail(TrailSummary):
    score: float


class SemanticSearchResponse(BaseModel):
    count: int
    trails: list[ScoredTrail]


class GeoJsonGeometry(BaseModel):
    type: Literal["MultiLineString"] = "MultiLineString"
    coordinates: list[list[list[float]]]  # [[[lon, lat], ...], ...]


class TrailGeoJson(BaseModel):
    """GeoJSON Feature — the map payload for one trail."""

    type: Literal["Feature"] = "Feature"
    geometry: GeoJsonGeometry
    properties: dict[str, object]


class RouteRequest(BaseModel):
    start: str = Field(description="POI name to start from")
    end: str = Field(description="POI name to finish at")
    max_distance_m: Annotated[float, Field(gt=0)] | None = None


class RouteResponse(BaseModel):
    total_distance_m: float
    elevation_gain_m: float | None = None
    start_poi: PoiRef
    end_poi: PoiRef
    geometry: dict[str, object]  # GeoJSON LineString
    surfaces: list[str | None] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["up", "down"]
