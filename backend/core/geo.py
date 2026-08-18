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


def distance_to_polyline_m(point: LatLon, polyline: list[LatLon]) -> float:
    """Perpendicular distance from a point to a polyline, in metres.

    Unlike min_distance_to_polyline_m, this projects onto each SEGMENT rather
    than measuring to vertices. The difference only matters when edges are
    long — which is exactly the case for a routing engine's output, where a
    straight kilometre may be returned as two points. Vertex distance then
    reports a POI 8 m off the line as 556 m away, and the map-back silently
    loses it.

    Uses a local equirectangular projection: metres per degree of latitude are
    near-constant, and longitude is scaled by cos(lat) about the query point.
    Error is negligible at the hundreds of metres this is used over, and it
    keeps the module dependency-free.
    """
    if not polyline:
        return float("inf")
    if len(polyline) == 1:
        return haversine_m(point, polyline[0])

    lat_scale = 111_320.0
    lon_scale = 111_320.0 * math.cos(math.radians(point[0]))

    def to_local(p: LatLon) -> tuple[float, float]:
        return ((p[1] - point[1]) * lon_scale, (p[0] - point[0]) * lat_scale)

    best = float("inf")
    for start, end in zip(polyline, polyline[1:], strict=False):
        ax, ay = to_local(start)
        bx, by = to_local(end)
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        if length_sq == 0.0:
            best = min(best, math.hypot(ax, ay))
            continue
        # Projection of the origin (the query point) onto the segment, clamped
        # to its ends so a perpendicular that falls outside still measures to
        # the nearer endpoint.
        t_clamped = max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
        best = min(best, math.hypot(ax + t_clamped * dx, ay + t_clamped * dy))
    return best


def bounds_of(points: list[LatLon]) -> tuple[float, float, float, float]:
    """(min_lat, min_lon, max_lat, max_lon) of a point list."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return (min(lats), min(lons), max(lats), max(lons))


def distance_to_bounds_m(
    point: LatLon, bounds: tuple[float, float, float, float]
) -> float:
    """A cheap LOWER bound on the distance from a point to anything inside a box.

    Nothing in the box can be nearer than this, which is what makes it useful:
    one hypot against the box rules out a candidate that would otherwise cost a
    full scan of a polyline. Zero inside the box.

    Deliberately an underestimate. Longitude is scaled by the cosine of the
    latitude furthest from the equator in play, which is where a degree of
    longitude is shortest, so the result can only be too small — never too
    large, which would make it prune something real.
    """
    lat, lon = point
    min_lat, min_lon, max_lat, max_lon = bounds
    dlat = max(min_lat - lat, 0.0, lat - max_lat)
    dlon = max(min_lon - lon, 0.0, lon - max_lon)
    if dlat == 0.0 and dlon == 0.0:
        return 0.0
    worst_lat = max(abs(lat), abs(min_lat), abs(max_lat))
    return math.hypot(
        dlat * 111_320.0, dlon * 111_320.0 * math.cos(math.radians(worst_lat))
    )


def nearest_vertex_index(point: LatLon, polyline: list[LatLon]) -> int:
    return min(range(len(polyline)), key=lambda i: haversine_m(point, polyline[i]))


def in_bbox(point: LatLon, bbox: tuple[float, float, float, float]) -> bool:
    lat, lon = point
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
