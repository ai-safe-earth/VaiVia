"""Pure transformation: Overpass JSON -> graph rows (no I/O, fully unit-testable).

Topology model (see graph/schema.cypher):
  * An OSM node used by >= 2 ways, or terminating a way, is an (:Intersection).
  * Each way is split at intersections into Segment pieces with deterministic id
    "<wayId>#<n>" — MERGE-stable across re-runs.
  * Each piece yields directed CONNECTS_TO edges between its two end
    intersections (both directions unless oneway=yes / oneway=-1).
  * Elevation gain/loss per edge direction stays None until the SRTM backfill
    (docs/fragilities.md #6) — never fabricated.
"""

from dataclasses import dataclass, field
from typing import Any

from core.geo import (
    LatLon,
    in_bbox,
    min_distance_to_polyline_m,
    polyline_length_m,
    polyline_midpoint,
)

POI_TAG_MAP: list[tuple[str, str, str]] = [
    # (tag key, tag value, poi type) — first match wins
    ("natural", "water", "lake"),
    ("tourism", "alpine_hut", "hut"),
    ("tourism", "wilderness_hut", "hut"),
    ("tourism", "camp_site", "campsite"),
    ("tourism", "viewpoint", "viewpoint"),
    ("railway", "station", "station"),
    ("amenity", "swimming_area", "bathing_water"),
    ("leisure", "swimming_area", "bathing_water"),
]


@dataclass
class SegmentRow:
    osm_way_id: str  # split-piece id "<wayId>#<n>"
    osm_parent_way_id: str
    length_m: float
    surface: str | None
    highway_type: str
    coordinates: list[LatLon]
    location: LatLon  # midpoint
    start_node: str  # osm node ids of end intersections
    end_node: str
    oneway: str | None  # raw OSM oneway tag


@dataclass
class ExtractResult:
    intersections: dict[str, LatLon] = field(
        default_factory=dict
    )  # osm_node_id -> location
    segments: list[SegmentRow] = field(default_factory=list)
    pois: list[dict[str, Any]] = field(default_factory=list)


def poi_type_for(tags: dict[str, str]) -> str | None:
    for key, value, poi_type in POI_TAG_MAP:
        if tags.get(key) == value:
            return poi_type
    return None


def extract(overpass_json: dict[str, Any]) -> ExtractResult:
    elements = overpass_json.get("elements", [])
    ways = [e for e in elements if e.get("type") == "way" and "geometry" in e]
    nodes = [e for e in elements if e.get("type") == "node"]

    # Count node usage across ways to find intersections.
    usage: dict[str, int] = {}
    for way in ways:
        for node_id in way.get("nodes", []):
            usage[str(node_id)] = usage.get(str(node_id), 0) + 1

    result = ExtractResult()

    for way in ways:
        way_id = str(way["id"])
        tags = way.get("tags", {})
        node_ids = [str(n) for n in way.get("nodes", [])]
        coords: list[LatLon] = [(g["lat"], g["lon"]) for g in way["geometry"]]
        if len(node_ids) != len(coords) or len(coords) < 2:
            continue  # malformed element; skip rather than corrupt topology

        # Split positions: endpoints always; interior nodes shared with other ways.
        cut_indexes = (
            [0]
            + [i for i in range(1, len(node_ids) - 1) if usage[node_ids[i]] >= 2]
            + [len(node_ids) - 1]
        )

        piece_bounds = zip(cut_indexes, cut_indexes[1:], strict=False)
        for piece_num, (a, b) in enumerate(piece_bounds):
            piece_coords = coords[a : b + 1]
            start_id, end_id = node_ids[a], node_ids[b]
            result.intersections[start_id] = coords[a]
            result.intersections[end_id] = coords[b]
            result.segments.append(
                SegmentRow(
                    osm_way_id=f"{way_id}#{piece_num}",
                    osm_parent_way_id=way_id,
                    length_m=polyline_length_m(piece_coords),
                    surface=tags.get("surface"),
                    highway_type=tags.get("highway", "path"),
                    coordinates=piece_coords,
                    location=polyline_midpoint(piece_coords),
                    start_node=start_id,
                    end_node=end_id,
                    oneway=tags.get("oneway"),
                )
            )

    for node in nodes:
        tags = node.get("tags", {})
        poi_type = poi_type_for(tags)
        if poi_type is None or "lat" not in node:
            continue
        result.pois.append(
            {
                "osm_id": str(node["id"]),
                "name": tags.get("name"),
                "type": poi_type,
                "lat": node["lat"],
                "lon": node["lon"],
            }
        )

    return result


def connects_to_rows(segments: list[SegmentRow]) -> list[dict[str, Any]]:
    """Directed CONNECTS_TO rows; both directions unless oneway."""
    rows: list[dict[str, Any]] = []
    for s in segments:
        forward = s.oneway != "-1"
        backward = s.oneway not in ("yes", "true", "1")
        base = {
            "distance_m": s.length_m,
            "osm_way_id": s.osm_way_id,
            "surface": s.surface,
            "highway_type": s.highway_type,
            "elevation_gain_m": None,  # SRTM backfill, Phase 2
            "elevation_loss_m": None,
        }
        if forward:
            rows.append({**base, "from": s.start_node, "to": s.end_node})
        if backward:
            rows.append({**base, "from": s.end_node, "to": s.start_node})
    return rows


def passes_by_rows(
    segments: list[SegmentRow], pois: list[dict[str, Any]], threshold_m: float
) -> list[dict[str, str]]:
    """(segment, poi) pairs where the segment polyline passes within threshold."""
    rows: list[dict[str, str]] = []
    for poi in pois:
        poi_point = (poi["lat"], poi["lon"])
        for s in segments:
            if min_distance_to_polyline_m(poi_point, s.coordinates) <= threshold_m:
                rows.append({"osm_way_id": s.osm_way_id, "poi_osm_id": poi["osm_id"]})
    return rows


def located_in_rows(
    result: ExtractResult, bbox: tuple[float, float, float, float], region: str
) -> dict[str, list[str]]:
    """Ids of intersections and POIs falling inside the region bbox."""
    return {
        "intersections": [
            nid for nid, loc in result.intersections.items() if in_bbox(loc, bbox)
        ],
        "pois": [
            p["osm_id"] for p in result.pois if in_bbox((p["lat"], p["lon"]), bbox)
        ],
    }
