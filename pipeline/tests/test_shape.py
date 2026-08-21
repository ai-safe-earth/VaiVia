"""The shape classifier: circular or linear, measured, mapper's word first.

The threshold these pin was calibrated 2026-08-21 on the 621 single-line OSM
relations: every true ring closes at gap/length <= 0.0005 and everything else
jumps to >= 0.14, so GAP_RATIO = 0.01 sits an order of magnitude clear of both
sides (export/shape.py's docstring carries the distribution).
"""

from export.shape import GAP_RATIO, classify_osm_shape


def test_a_closed_ring_is_circular():
    assert classify_osm_shape(0.0, 13122.0, None) == "circular"


def test_a_near_closed_ring_is_circular():
    # The measured case: 5.1 m over 10,759 m (rel 14981695).
    assert classify_osm_shape(5.1, 10759.0, None) == "circular"


def test_a_short_stub_with_a_comparable_gap_is_linear():
    # The degenerate case an absolute threshold would get wrong: a 10 m scrap
    # whose endpoints are 9.7 m apart is not a ring (rel 9700801).
    assert classify_osm_shape(9.7, 10.0, None) == "linear"


def test_the_ratio_boundary_is_inclusive():
    assert classify_osm_shape(GAP_RATIO * 1000.0, 1000.0, None) == "circular"
    assert classify_osm_shape(GAP_RATIO * 1000.0 + 0.1, 1000.0, None) == "linear"


def test_the_mappers_roundtrip_tag_beats_geometry():
    # A ring our coverage clips measures open but is still a ring
    # (Giro del Pizzo di Cusio: 650 m gap on 3.2 km, tagged yes)...
    assert classify_osm_shape(650.0, 3158.0, "yes") == "circular"
    # ...and a closed line the mapper says is not a roundtrip is not one.
    assert classify_osm_shape(0.0, 5000.0, "no") == "linear"


def test_a_route_in_pieces_is_linear_unless_tagged():
    # Closure cannot be measured across gaps; the conservative error is the
    # safe one. The tag still rescues the clipped rings (Giro del Monte Bue).
    assert classify_osm_shape(None, 7461.0, None) == "linear"
    assert classify_osm_shape(None, 7461.0, "yes") == "circular"


def test_degenerate_lengths_are_linear():
    assert classify_osm_shape(0.0, 0.0, None) == "linear"
    assert classify_osm_shape(0.0, None, None) == "linear"
