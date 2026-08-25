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
from functools import lru_cache
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
from core.geo import bounds_of, expand_bounds_m, polyline_length_m
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


async def _endpoint_bounds(
    db: DbDep, start_node: str, end_node: str, margin_m: float
) -> tuple[float, float, float, float] | None:
    """The bbox to project for this route: both endpoints, grown by margin_m.

    None when either endpoint has no location -- there is nothing to project
    around, and the caller falls back rather than routing over a box built
    from half the query.
    """
    rows = await db.run_named(
        "intersection_locations", osm_node_ids=[start_node, end_node]
    )
    found = {row["osm_node_id"]: (row["lat"], row["lon"]) for row in rows}
    points = [found.get(start_node), found.get(end_node)]
    if any(point is None for point in points):
        return None
    return expand_bounds_m(bounds_of([p for p in points if p]), margin_m)


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
    # The projection bbox is the QUERY's, not the app's. It used to be
    # settings.default_bbox -- one Lecco-shaped box, holding 31,514 of the
    # graph's 84,137 intersections once Bergamo was ingested. Endpoints outside
    # it are simply absent from the in-memory graph, so Dijkstra raises
    # ("sourceNode nodes do not exist in the in-memory graph"), the except below
    # catches it, and 63% of the network silently got hop-count routing while
    # the comfort weighting it was measured against never ran.
    #
    # max_distance_m makes the right box exact rather than guessed: a route
    # under that cap cannot leave a margin of it around its own endpoints.
    bounds = await _endpoint_bounds(db, start_node, end_node, max_distance_m)
    if bounds is None:
        return None
    min_lat, min_lon, max_lat, max_lon = bounds
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


#: Document schema versions this reader understands. An unknown version is
#: refused visibly rather than served on the guess that the fields line up.
#: 2.0 is the id cutover (vv2- digests, terminals, categories); the 1.x
#: store was re-emitted whole, so nothing older is ever legitimate here.
#: Extend deliberately, with the reader.
SUPPORTED_SCHEMA_VERSIONS = {"2.0"}


def _verify_document(document: dict, route_id: str, row: dict) -> None:
    """The contract checks between the catalogue row and the file it names.

    The filename used to be the whole contract: the API opened
    `{route_id}.json` and trusted every field in it, so a stale, renamed or
    hand-edited file was served verbatim under the requested id — the
    card's name from one build, the line from another. Mismatched builds
    must fail visibly, never display another route.
    """
    document_id = document.get("id")
    if document_id != route_id:
        raise HTTPException(
            status_code=503,
            detail=f"document_mismatch: the file for {route_id!r} carries id "
            f"{document_id!r} — the store and the catalogue disagree; "
            "re-emit the documents and reload the catalogue",
        )
    version = document.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise HTTPException(
            status_code=503,
            detail=f"unsupported schema_version {version!r} on route "
            f"{route_id!r}; this reader understands "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}",
        )
    document_run = document.get("provenance", {}).get("run_id")
    catalogue_run = row.get("doc_run_id")
    # Null means the graph predates the field (loaded before the loader
    # stamped it): nothing to compare, so nothing to refuse. The audit
    # script reports that state; a reload closes it.
    if catalogue_run is not None and document_run != catalogue_run:
        raise HTTPException(
            status_code=503,
            detail=f"build_mismatch: route {route_id!r} was catalogued from "
            f"export {catalogue_run!r} but the document on disk is from "
            f"{document_run!r}; re-emit and reload together",
        )


async def _load_route_document(route_id: str, db: DbDep) -> dict:
    """The 404/503 ladder every document-backed endpoint shares.

    The graph answers only "does this route exist", so an unknown id 404s
    before the filesystem is touched; a catalogue route whose document store
    is missing or unconfigured is a 503, never an empty answer — the
    semantic-search degradation rule. What it finds is then VERIFIED against
    the catalogue row before anything is served (_verify_document).
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
    try:
        stat = document_path.stat()
    except OSError:
        raise HTTPException(
            status_code=503,
            detail=f"route {route_id!r} is in the catalogue but its document is "
            "missing from the store — re-emit the documents",
        ) from None
    document = _read_document(str(document_path), stat.st_mtime_ns, stat.st_size)
    _verify_document(document, route_id, rows[0])
    return document


#: How many parsed route documents to hold. Selecting one card asks for its
#: geometry AND its detail, which was the same file read and json.loads'd
#: twice; a fold of cards multiplies that by ten. Documents are static between
#: exports, so the parse is worth keeping.
DOCUMENT_CACHE_SIZE = 32


@lru_cache(maxsize=DOCUMENT_CACHE_SIZE)
def _read_document(path: str, mtime_ns: int, size: int) -> dict:
    """One parsed route document. Callers READ it; nobody may mutate it.

    Keyed by mtime and size as well as path, so a re-export invalidates the
    entry by not matching it rather than by anyone remembering to clear a
    cache — route ids are geometry-derived and survive a rebuild, which is
    exactly why the path alone would not be enough.
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _piece_length_m(coordinates: list[list[float]]) -> float:
    """Geodesic length of one [lon, lat] piece of a MultiLineString."""
    return polyline_length_m([(point[1], point[0]) for point in coordinates])


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
        # and say so — the document remains the honest source. Longest in
        # METRES: sorting by point count served the densest-vertex piece,
        # which on OSM data measures mapper enthusiasm, not length.
        parts = sorted(geometry["coordinates"], key=_piece_length_m, reverse=True)
        coordinates = parts[0]
        pieces_total = len(parts)
        note = f"multi-part route: longest of {len(parts)} pieces shown"
    else:
        coordinates = geometry["coordinates"]
        pieces_total = 1
        note = None
    properties = {
        "route_id": route_id,
        # What the client is looking at, said on the payload: without kind
        # and shape here, a holder of a route_id could not tell a mapped
        # relation from a generated loop, and a truncated multi-piece line
        # read as the whole route.
        "kind": document.get("kind"),
        "shape": document.get("shape"),
        "pieces_total": pieces_total,
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
        if not route_m:
            # Nothing to compare the profile against — a clipped fragment with
            # a 0 or absent measured length. `off_by = 0.0` here read as a
            # PERFECT agreement and shipped 'ok', which is the quiet lie
            # PROFILE_TOLERANCE exists to stop: no basis means no claim of
            # accuracy, so the chart carries its caveat.
            profile_quality = "approximate"
        else:
            off_by = abs(profile_end - route_m) / route_m
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
