"""Route document -> the rows the Neo4j catalogue loader UNWINDs.

Shared by the pipeline's bulk loader (export/neo4j_load.py) and the
backend's favourite/share save path (chat/save_route.py), because a route
a user keeps must land in the graph EXACTLY as a catalogue route would —
one mapping, two writers (docs/route-design.md, "Ask time" step 5).
"""

from __future__ import annotations

from typing import Any

from vaivia_routes.document import SAC_ORDER


def sac_rank(grade: str | None) -> int | None:
    """T1..T6 as 1..6, because Cypher cannot order the strings."""
    return SAC_ORDER.index(grade) + 1 if grade in SAC_ORDER else None


def mtb_rank(grade: str | None) -> int | None:
    """mtb:scale '0'..'6' as ints, same reason."""
    return int(grade) if grade is not None and grade.isdigit() else None


def document_rows(document: dict) -> dict[str, Any]:
    """One document -> the rows the loader UNWINDs. Pure, tested.

    Selection properties only. `None` values are kept (SET writes null, which
    in Neo4j removes the property — absent is not zero, in graph form).
    """
    identity = document["identity"]
    measures = document["measures"]
    difficulty = document["difficulty"]
    generation = document.get("provenance", {}).get("generation") or {}
    quality = document["quality"]

    route = {
        "route_id": document["id"],
        "props": {
            "kind": document["kind"],
            # Schema 1.2 carries shape top-level (measured for OSM routes,
            # constructed for generated ones). The fallback chain serves
            # legacy 1.1 documents only: generation shape, then 'named'.
            "shape": document.get("shape", generation.get("shape", "named")),
            "activity": identity.get("activity"),
            "name": identity.get("name"),
            "ref": identity.get("ref"),
            "network": identity.get("network"),
            "waymark": identity.get("waymark"),
            "from": identity.get("from"),
            "to": identity.get("to"),
            "operator": identity.get("operator"),
            "regions": identity.get("regions") or [],
            "osm_relation_id": identity.get("osm_relation_id"),
            "destination_name": (generation.get("destination") or {}).get("name"),
            "destination_kind": (generation.get("destination") or {}).get("kind"),
            "distance_m": measures["distance_m"],
            "ascent_m": measures["ascent_m"],
            "descent_m": measures["descent_m"],
            "lowest_m": measures.get("lowest_m"),
            "highest_m": measures.get("highest_m"),
            "sac_scale": difficulty["sac_scale"],
            "sac_scale_rank": sac_rank(difficulty["sac_scale"]),
            "sac_max": difficulty["sac_max"],
            "sac_max_rank": sac_rank(difficulty["sac_max"]),
            "graded_share": difficulty["graded_share"],
            "surface_dominant": document["surface"]["dominant"],
            "continuous": document["continuity"]["continuous"],
            "pieces": document["continuity"]["pieces"],
            "mtb_rideable": generation.get("mtb_rideable"),
            "mtb_scale": generation.get("mtb_scale"),
            "mtb_scale_rank": mtb_rank(generation.get("mtb_scale")),
            "bike_blocked_m": generation.get("bike_blocked_m"),
            "off_road_share": generation.get("off_road_share"),
            "urban_share": generation.get("urban_share"),
            "retrace_share": generation.get("retrace_share"),
            "score": generation.get("score"),
            "matched_fraction": quality.get("matched_fraction"),
            "warnings": len(quality["warnings"]),
            "places": len(document["places"]),
            "bbox": document["bbox"],
            # The cross-layer contract fields (docs/route-document.md): the
            # API compares these against the document it serves, so a :Route
            # from export N wearing a file from export N-1 fails visibly
            # instead of serving another build's shape under this id.
            "schema_version": document["schema_version"],
            "doc_run_id": document.get("provenance", {}).get("run_id"),
            # Schema 2.0 selection fields: which direction this document
            # walks, its sibling, the corridor it shares with siblings from
            # its terminal, why a broken route is broken, and the class
            # twins the cards and legends style by.
            "direction": document.get("direction"),
            "reverse_of": document.get("reverse_of"),
            # 2.1: of a direction pair, suggest the steep-up walk.
            "recommended": document.get("recommended"),
            "continuity_reason": (document.get("continuity") or {}).get("reason"),
            "divergence_vertex": (document.get("divergence") or {}).get("vertex_id"),
            "approach_m": (document.get("divergence") or {}).get("approach_m"),
            "terminals_reachable": sum(
                1 for term in document.get("terminals") or [] if term.get("reachable")
            ),
            "terminals_total": len(document.get("terminals") or []),
            "distance_class": (document.get("categories") or {}).get("distance_class"),
            "climb_class": (document.get("categories") or {}).get("climb_class"),
            "difficulty_class": (document.get("categories") or {}).get(
                "difficulty_class"
            ),
            "surface_class": (document.get("categories") or {}).get("surface_class"),
        },
    }

    places = []
    passes = []
    for seq, place in enumerate(document["places"]):
        if place.get("lat") is None or place.get("lon") is None:
            continue  # spike-era documents carried no coordinates; current do
        places.append(
            {
                "place_id": place["id"],
                "kind": place["kind"],
                "name": place.get("name"),
                "ele_m": place.get("ele_m"),
                "lon": place["lon"],
                "lat": place["lat"],
            }
        )
        passes.append(
            {
                "route_id": document["id"],
                "place_id": place["id"],
                "seq": seq,
                "offset_m": place["offset_m"],
                "distance_along_m": place.get("distance_along_m"),
                "is_start": bool(place.get("is_start")),
            }
        )

    # Schema 2.0: `start` became `terminals` (1-2 per route, each carrying
    # its own reachability and seasons). The selection surface anchors on the
    # FIRST terminal — the one a loop leaves from, a traverse's A end — and
    # carries the second's reachability as properties, not as a second
    # :Start, until a query needs it.
    terminals = document.get("terminals") or []
    # Anchor on the first REACHABLE terminal: 106 live traverses had an
    # unreachable A end and a reachable B end, and anchoring on [0] blindly
    # gave them a nameless, seasonless :Start while the real trailhead — the
    # one "routes starting near <village>" must match — went unindexed.
    start = next(
        (term for term in terminals if term.get("reachable")),
        terminals[0] if terminals else None,
    )
    start_row = None
    start_link = None
    if start and start.get("vertex_id") is not None:
        lon, lat = start["point"]["coordinates"]
        start_row = {
            "vertex_id": start["vertex_id"],
            "car_free": start["car_free"],
            "names": start.get("names") or [],
            "start_classes": start.get("start_classes") or [],
            "reachable_spring": start["seasons"]["spring"],
            "reachable_summer": start["seasons"]["summer"],
            "reachable_autumn": start["seasons"]["autumn"],
            "reachable_winter": start["seasons"]["winter"],
            "seasons_unverified": start["seasons"]["unverified"],
            "lon": lon,
            "lat": lat,
        }
        start_link = {
            "route_id": document["id"],
            "vertex_id": start["vertex_id"],
            "nearest_m": start["nearest_start_m"],
        }

    return {
        "route": route,
        "places": places,
        "passes": passes,
        "start": start_row,
        "start_link": start_link,
    }
