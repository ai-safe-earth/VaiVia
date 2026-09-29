"""Stored route documents: hydration, geometry and detail for saved routes.

POI-to-POI routing used to live here too, over a per-request GDS projection.
It went with R7 (chore/retire-catalogue): nothing called `POST /routes`, and
the A-to-B ask that IS reachable answers through /chat's RouteIntent, which
walks CONNECTS_TO with shortestPath and never touches GDS.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.deps import DbDep
from api.models import (
    GeoJsonLineString,
    RouteDetail,
    RouteGeoJson,
)
from core.config import get_settings
from core.geo import polyline_length_m

logger = logging.getLogger(__name__)

router = APIRouter(tags=["routing"])


#: Document schema versions this reader understands. An unknown version is
#: refused visibly rather than served on the guess that the fields line up.
#: 2.0 is the id cutover (vv2- digests, terminals, categories); the 1.x
#: store was re-emitted whole, so nothing older is ever legitimate here.
#: 2.1 adds `recommended` (direction pairs suggest the steep-up walk) —
#: additive, so both are served. Extend deliberately, with the reader.
SUPPORTED_SCHEMA_VERSIONS = {"2.0", "2.1"}


def _verify_document(document: dict, route_id: str, row: dict) -> None:
    """The contract checks between the (:Route) row and the file it names.

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
            f"{document_id!r} — the store and the graph disagree",
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
    node_run = row.get("doc_run_id")
    # Null means the node predates the field: nothing to compare, so nothing
    # to refuse.
    if node_run is not None and document_run != node_run:
        raise HTTPException(
            status_code=503,
            detail=f"build_mismatch: route {route_id!r} was saved from build "
            f"{node_run!r} but the document on disk is from "
            f"{document_run!r}; the pair was not written together",
        )


async def _load_route_document(route_id: str, db: DbDep) -> dict:
    """The 404/503 ladder every document-backed endpoint shares.

    The graph answers only "does this route exist", so an unknown id 404s
    before the filesystem is touched; a saved route whose document store
    is missing or unconfigured is a 503, never an empty answer — the
    semantic-search degradation rule. What it finds is then VERIFIED against
    the (:Route) row before anything is served (_verify_document).
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
            detail=f"route {route_id!r} is in the graph but its document is "
            "missing from the store — check ROUTE_DOCUMENTS_DIR",
        ) from None
    document = _read_document(str(document_path), stat.st_mtime_ns, stat.st_size)
    _verify_document(document, route_id, rows[0])
    return document


#: How many parsed route documents to hold. Selecting one card asks for its
#: geometry AND its detail, which was the same file read and json.loads'd
#: twice; a fold of cards multiplies that by ten. Documents are static between
#: writes, so the parse is worth keeping.
DOCUMENT_CACHE_SIZE = 32


@lru_cache(maxsize=DOCUMENT_CACHE_SIZE)
def _read_document(path: str, mtime_ns: int, size: int) -> dict:
    """One parsed route document. Callers READ it; nobody may mutate it.

    Keyed by mtime and size as well as path, so a rewrite invalidates the
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


@router.get("/routes/by-ids")
async def routes_by_ids(ids: str, db: DbDep) -> dict:
    """Hydrate route ids back into cards, in the order given.

    The resume path: the client stores only ids per conversation turn
    (messages.result_refs) and asks for the cards again here. Same contract
    as /routes/favorites — routes_by_ids ends in the route_card fragment, so
    the client renders the cards it renders for a live answer; an id with no
    saved :Route comes back in `missing`, never silently dropped.
    The literal path registers before the /routes/{route_id} patterns.
    """
    wanted = [part.strip() for part in ids.split(",") if part.strip()][:100]
    rows = await db.run_named("routes_by_ids", route_ids=wanted) if wanted else []
    by_id = {row["id"]: row for row in rows}
    return {
        "routes": [by_id[i] for i in wanted if i in by_id],
        "missing": [i for i in wanted if i not in by_id],
    }


@router.get("/routes/{route_id}/geojson", response_model=RouteGeoJson)
async def get_route_geojson(route_id: str, db: DbDep) -> RouteGeoJson:
    """Map payload for one saved route, read from its ROUTE DOCUMENT.

    The graph deliberately carries no geometry (a second home for it is how
    two truths start — graph/save_route.cypher carries none); the document is
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
        kind=document.get("kind"),
        shape=document.get("shape"),
        profile=profile,
        profile_quality=profile_quality,
        measures=document["measures"],
        continuity=document["continuity"],
        surface=document["surface"],
        difficulty=document.get("difficulty"),
        quality=document.get("quality"),
        places=document.get("places", []),
        attribution=_attribution(document),
    )
