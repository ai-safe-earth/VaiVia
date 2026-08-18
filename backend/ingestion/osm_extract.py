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

from core.comfort import comfort_cost_m
from core.geo import (
    LatLon,
    haversine_m,
    in_bbox,
    min_distance_to_polyline_m,
    polyline_length_m,
    polyline_midpoint,
)

POI_TAG_MAP: list[tuple[str, str, str]] = [
    # (tag key, tag value, poi type) — first match wins.
    #
    # Two roles, deliberately in one table. ANCHORS are where an outing can
    # start, because you can leave a car or step off a train there —
    # `parking` exists for that alone and is not something a user asks to walk
    # past. DESTINATIONS are places worth reaching, which is what turns a line
    # on a map into an outing. api.models.PoiType exposes only the latter to
    # the chat layer.
    ("natural", "water", "lake"),
    ("tourism", "alpine_hut", "hut"),
    ("tourism", "wilderness_hut", "hut"),
    ("tourism", "camp_site", "campsite"),
    ("tourism", "viewpoint", "viewpoint"),
    ("railway", "station", "station"),
    ("amenity", "swimming_area", "bathing_water"),
    ("leisure", "swimming_area", "bathing_water"),
    # Anchors
    ("amenity", "parking", "parking"),
    # Destinations
    ("natural", "peak", "peak"),
    ("natural", "saddle", "saddle"),
    ("natural", "beach", "beach"),
    ("natural", "spring", "spring"),
    ("natural", "cave_entrance", "cave"),
    ("waterway", "waterfall", "waterfall"),
    # An ermita/eremo is tagged inconsistently; all three forms are common in
    # Italy and Spain, so all three map to one type rather than three the user
    # would have to guess between.
    ("building", "chapel", "chapel"),
    ("historic", "wayside_shrine", "chapel"),
    ("historic", "wayside_cross", "chapel"),
    ("historic", "castle", "castle"),
    ("historic", "ruins", "ruins"),
    ("tourism", "picnic_site", "picnic_site"),
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


MAX_BOUNDARY_POINTS = 100


def area_boundary(element: dict[str, Any]) -> list[LatLon]:
    """The outline of an area POI, as (lat, lon).

    A closed way carries `geometry` directly. A multipolygon relation carries
    its rings as members, so the outer ones are concatenated — rough as a
    polygon, but exact enough to answer "does this path run along the shore".
    """
    geometry = element.get("geometry")
    if geometry:
        return [(g["lat"], g["lon"]) for g in geometry if "lat" in g]

    points: list[LatLon] = []
    for member in element.get("members") or []:
        if member.get("role") not in (None, "", "outer"):
            continue
        for g in member.get("geometry") or []:
            if "lat" in g:
                points.append((g["lat"], g["lon"]))
    return points


def sample_ring(points: list[LatLon], limit: int = MAX_BOUNDARY_POINTS) -> list[LatLon]:
    """Thin an outline to at most `limit` points, keeping its shape.

    Evenly spaced by index rather than by distance: cheap, and the error is far
    smaller than the matching threshold it feeds.
    """
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[int(i * step)] for i in range(limit)]


def poi_type_for(tags: dict[str, str]) -> str | None:
    for key, value, poi_type in POI_TAG_MAP:
        if tags.get(key) == value:
            return poi_type
    return None


def extract(overpass_json: dict[str, Any]) -> ExtractResult:
    elements = overpass_json.get("elements", [])
    # Routing ways are identified by their TAGS, not by their shape. Since
    # POIs are fetched with `out geom`, a lake or car park outline also
    # arrives as a way with geometry and a node list — and having no highway
    # tag it would fall through to the "path" default and become routable.
    # That briefly put lake shores in the routing graph.
    ways = [
        e
        for e in elements
        if e.get("type") == "way"
        and "geometry" in e
        and e.get("tags", {}).get("highway")
    ]
    nodes = [e for e in elements if e.get("type") == "node"]
    # Area POIs. A car park, a lake and a picnic site are mapped as closed ways
    # or multipolygons, not nodes, so Overpass returns them with `center`
    # instead of `geometry`. Routing ways are told apart by carrying geometry.
    # Without this the whole class is simply absent, which is why lake
    # proximity needed a 500 m radius: the only lake nodes are labels sitting
    # out on the water.
    # The mirror image: an area POI is a way or relation that is NOT a
    # routing way. `out geom` gives ways a node list too, so testing for its
    # absence would have excluded every POI way — which is why only the 12
    # relations got boundaries on the first attempt.
    area_pois = [
        e
        for e in elements
        if e.get("type") in ("way", "relation")
        and not e.get("tags", {}).get("highway")
        and (e.get("geometry") or e.get("members") or "center" in e)
    ]

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
                # A node has no extent; identical keys keep the write uniform.
                "boundary": [],
                "extent_m": 0.0,
                # Kept so scripts.enrich_pois_wiki can look the place up later.
                # Only ~12% of destinations carry either, but where they do the
                # prose is the best open description we have.
                "wikidata": tags.get("wikidata"),
                "wikipedia": tags.get("wikipedia"),
            }
        )

    for area in area_pois:
        tags = area.get("tags", {})
        poi_type = poi_type_for(tags)
        if poi_type is None:
            continue
        # Keep the OUTLINE, not just a centre. A centroid is fine for a car
        # park and useless for a lake: Lago di Como's sits 5.1 km out on the
        # water, so measuring to it reports every shoreline path as far away
        # and "a route around the lake" can never be answered.
        boundary = area_boundary(area)
        center = area.get("center") or {}
        if boundary:
            centre = polyline_midpoint(boundary)
        elif "lat" in center:
            centre = (center["lat"], center["lon"])
        else:
            continue
        # Prefixed so a way id and a node id of the same number cannot collide
        # on the MERGE key. Node POIs keep bare ids so existing data is stable.
        prefix = "w" if area["type"] == "way" else "r"
        result.pois.append(
            {
                "osm_id": f"{prefix}{area['id']}",
                "name": tags.get("name"),
                "type": poi_type,
                "lat": centre[0],
                "lon": centre[1],
                # Sampled: a 4,000-point lake outline must not become a
                # 4,000-point node property, and ~100 points still traces a
                # shore closely enough to say if a path runs along it.
                "boundary": [list(p) for p in sample_ring(boundary)],
                # How far the outline reaches from the centre, so the
                # map-back knows which POIs are worth measuring at all.
                "extent_m": max(
                    (haversine_m(centre, point) for point in boundary), default=0.0
                ),
                "wikidata": tags.get("wikidata"),
                "wikipedia": tags.get("wikipedia"),
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
            # What routing minimises, so trails beat roads. Distances shown to
            # users always come from distance_m — see core/comfort.py.
            "cost_m": comfort_cost_m(s.length_m, s.highway_type, s.surface),
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
