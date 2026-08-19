"""POI classification and the defensive ele parse."""

from load.poi_types import parse_ele_m, poi_type_for, settlement_kind


def test_first_match_wins() -> None:
    # natural=water before amenity=parking: a flooded car park is a lake.
    assert poi_type_for({"natural": "water", "amenity": "parking"}) == "lake"


def test_hut_matches_both_tag_forms() -> None:
    assert poi_type_for({"tourism": "alpine_hut"}) == "hut"
    assert poi_type_for({"tourism": "wilderness_hut"}) == "hut"


def test_ermita_three_taggings_one_type() -> None:
    for tags in (
        {"building": "chapel"},
        {"historic": "wayside_shrine"},
        {"historic": "wayside_cross"},
    ):
        assert poi_type_for(tags) == "chapel"


def test_unmatched_is_none() -> None:
    assert poi_type_for({"highway": "path"}) is None
    assert poi_type_for({}) is None


def test_ele_parses_the_junk_osm_actually_contains() -> None:
    assert parse_ele_m({"ele": "1875"}) == 1875.0
    assert parse_ele_m({"ele": "1875 m"}) == 1875.0
    # "1,875" is ambiguous (Italian decimal comma): 1.875 m fails the
    # plausibility band, so the honest answer is None, not a guess.
    assert parse_ele_m({"ele": "1,875"}) is None
    assert parse_ele_m({"ele": "circa 1800"}) is None
    assert parse_ele_m({}) is None


def test_ele_rejects_impossible_values() -> None:
    # Below Como's lake level or above Mont Blanc is a typo, not a height.
    assert parse_ele_m({"ele": "12"}) is None
    assert parse_ele_m({"ele": "18750"}) is None


def test_settlement_kinds() -> None:
    assert settlement_kind({"place": "village"}) == "village"
    assert settlement_kind({"landuse": "residential"}) == "residential"
    assert settlement_kind({"place": "locality"}) is None
