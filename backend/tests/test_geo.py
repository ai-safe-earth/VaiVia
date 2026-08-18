import pytest

from core.geo import (
    haversine_m,
    in_bbox,
    min_distance_to_polyline_m,
    polyline_length_m,
    polyline_midpoint,
)

# ~1 degree of latitude is ~111.2 km everywhere
MILAN = (45.464, 9.190)
ONE_DEG_NORTH = (46.464, 9.190)


def test_haversine_one_degree_latitude():
    assert abs(haversine_m(MILAN, ONE_DEG_NORTH) - 111_195) < 500


def test_haversine_zero():
    assert haversine_m(MILAN, MILAN) == 0.0


def test_polyline_length_is_sum_of_legs():
    a, b, c = (45.0, 9.0), (45.001, 9.0), (45.002, 9.0)
    assert abs(polyline_length_m([a, b, c]) - 2 * haversine_m(a, b)) < 0.01


def test_polyline_midpoint_of_uniform_line():
    points = [(45.0, 9.0), (45.001, 9.0), (45.002, 9.0), (45.003, 9.0), (45.004, 9.0)]
    assert polyline_midpoint(points) == (45.002, 9.0)


def test_min_distance_to_polyline():
    polyline = [(45.0, 9.0), (45.01, 9.0)]
    near = (45.005, 9.0001)  # ~8 m east of the 45.005 vertex... but vertex-based:
    # nearest vertex is (45.0 or 45.01, 9.0); distance dominated by lat offset
    assert min_distance_to_polyline_m((45.0, 9.0), polyline) == 0.0
    assert min_distance_to_polyline_m(near, polyline) > 0


def test_in_bbox():
    bbox = (45.8, 9.3, 46.0, 9.6)
    assert in_bbox((45.9, 9.4), bbox)
    assert not in_bbox((45.7, 9.4), bbox)
    assert not in_bbox((45.9, 9.7), bbox)


def test_perpendicular_distance_beats_vertex_distance_on_long_edges():
    """A routing engine can return a straight kilometre as two points. Vertex
    distance then reports a POI beside its middle as half a kilometre away,
    which would silently drop it from the route map-back."""
    from core.geo import distance_to_polyline_m, min_distance_to_polyline_m

    line = [(45.856, 9.393), (45.866, 9.393)]
    beside_the_middle = (45.861, 9.3931)

    assert min_distance_to_polyline_m(beside_the_middle, line) > 500
    assert distance_to_polyline_m(beside_the_middle, line) < 10


def test_perpendicular_distance_clamps_past_the_ends():
    """A point beyond a segment's end measures to the endpoint, not to an
    infinite line — otherwise anything near the line's bearing looks close."""
    from core.geo import distance_to_polyline_m, haversine_m

    line = [(45.856, 9.393), (45.857, 9.393)]
    beyond = (45.867, 9.393)
    assert distance_to_polyline_m(beyond, line) == pytest.approx(
        haversine_m(beyond, line[-1]), rel=0.01
    )
