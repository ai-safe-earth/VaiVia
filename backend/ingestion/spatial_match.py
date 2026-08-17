"""Spatial proximity matching: Trailforks trail polylines -> OSM segments.

Produces ordered (:Trail)-[:COMPOSED_OF {seq, match_confidence}]->(:Segment)
rows. Pure functions — no I/O — so precision is unit-testable on synthetic
fixtures (docs/fragilities.md #1 and #2).

Rules (owner-ratified, see docs/plan.md):
  * A segment matches a trail when the MEAN distance of its vertices to the
    trail polyline is <= SPATIAL_MATCH_THRESHOLD_M and the segment's
    highway_type is compatible with the trail's activity.
  * match_confidence = 1 - mean_distance / threshold  (in (0, 1]).
  * seq orders matched segments by the position of their midpoint along the
    trail polyline (nearest-vertex projection).
"""

from dataclasses import dataclass

from core.geo import LatLon, min_distance_to_polyline_m, nearest_vertex_index

# highway types a trail of a given activity can plausibly run on
COMPATIBLE_HIGHWAYS = {
    "mtb": {"path", "track", "cycleway"},
    "hike": {"path", "track", "footway"},
    "mixed": {"path", "track", "cycleway", "footway"},
}


@dataclass
class MatchCandidate:
    """The segment fields the matcher needs (a projection of SegmentRow)."""

    osm_way_id: str
    highway_type: str
    coordinates: list[LatLon]
    location: LatLon


@dataclass
class Match:
    osm_way_id: str
    seq: int
    match_confidence: float


def is_compatible(highway_type: str, activity: str) -> bool:
    return highway_type in COMPATIBLE_HIGHWAYS.get(
        activity, COMPATIBLE_HIGHWAYS["mixed"]
    )


def mean_distance_m(
    segment_coords: list[LatLon], trail_polyline: list[LatLon]
) -> float:
    return sum(
        min_distance_to_polyline_m(p, trail_polyline) for p in segment_coords
    ) / len(segment_coords)


def match_trail(
    trail_polyline: list[LatLon],
    activity: str,
    candidates: list[MatchCandidate],
    threshold_m: float,
) -> list[Match]:
    """Match a trail against candidate segments; returns seq-ordered matches."""
    scored: list[tuple[int, float, str]] = []  # (position along trail, confidence, id)
    for c in candidates:
        if not is_compatible(c.highway_type, activity):
            continue
        distance = mean_distance_m(c.coordinates, trail_polyline)
        if distance > threshold_m:
            continue
        position = nearest_vertex_index(c.location, trail_polyline)
        confidence = 1.0 - distance / threshold_m if threshold_m > 0 else 1.0
        scored.append((position, confidence, c.osm_way_id))

    scored.sort(key=lambda item: (item[0], item[2]))
    return [
        Match(osm_way_id=way_id, seq=seq, match_confidence=round(conf, 3))
        for seq, (_, conf, way_id) in enumerate(scored)
    ]
