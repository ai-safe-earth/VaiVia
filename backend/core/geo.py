"""Pure geodesic helpers. Coordinates are (lat, lon) WGS84 tuples."""

import math

EARTH_RADIUS_M = 6_371_000.0

LatLon = tuple[float, float]


def haversine_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance between two points in metres."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def polyline_length_m(points: list[LatLon]) -> float:
    return sum(haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))


def polyline_midpoint(points: list[LatLon]) -> LatLon:
    """Point of the polyline nearest to half its length (a real vertex, good enough
    as a spatial anchor)."""
    if len(points) == 1:
        return points[0]
    half = polyline_length_m(points) / 2
    acc = 0.0
    for i in range(len(points) - 1):
        step = haversine_m(points[i], points[i + 1])
        if acc + step >= half:
            return points[i] if half - acc < step / 2 else points[i + 1]
        acc += step
    return points[-1]


def min_distance_to_polyline_m(point: LatLon, polyline: list[LatLon]) -> float:
    """Minimum vertex distance from a point to a polyline. Vertex-based (not
    perpendicular projection): adequate at OSM/Trailforks vertex densities and
    keeps the matcher dependency-free. Overestimates on long straight edges."""
    return min(haversine_m(point, p) for p in polyline)


def nearest_vertex_index(point: LatLon, polyline: list[LatLon]) -> int:
    return min(range(len(polyline)), key=lambda i: haversine_m(point, polyline[i]))


def in_bbox(point: LatLon, bbox: tuple[float, float, float, float]) -> bool:
    lat, lon = point
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
