"""The WKB surgery: pyosmium emits no SRID, the typed columns demand one."""

from shapely.geometry import LineString, Point

from load.osm import ewkb4326, regions_for, regions_for_bounds


def test_ewkb_stamp_point() -> None:
    plain = Point(9.4, 45.9).wkb_hex
    stamped = ewkb4326(plain)
    # EWKB: type word gains the 0x20000000 flag, then 4326 little-endian.
    assert stamped[:10].lower() == "0101000020"
    assert stamped[10:18].lower() == "e6100000"
    # the coordinate payload is untouched
    assert stamped[18:] == plain[10:]


def test_ewkb_stamp_linestring() -> None:
    stamped = ewkb4326(LineString([(9.4, 45.9), (9.5, 45.91)]).wkb_hex)
    assert stamped[:10].lower() == "0102000020"
    assert stamped[10:18].lower() == "e6100000"


def test_region_membership() -> None:
    assert regions_for(45.85, 9.4) == ["Lecco"]
    assert regions_for(45.7, 9.7) == ["Bergamo"]
    assert regions_for(44.0, 8.0) == []
    # A feature spanning the boundary belongs to both, not to neither.
    assert regions_for_bounds(45.75, 9.5, 45.85, 9.6) == ["Lecco", "Bergamo"]
