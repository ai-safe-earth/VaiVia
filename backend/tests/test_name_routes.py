"""Naming and duration for catalogue routes."""

import pytest

from scripts.name_routes import (
    DEFAULT_MTB_LEVEL,
    NAME_PRIORITY,
    durations,
    mtb_level,
    route_name,
)


def poi(name, poi_type):
    return {"name": name, "type": poi_type}


def test_a_peak_wins_over_anything_else_it_passes():
    """A summit is what someone remembers and asks for again."""
    name, source = route_name(
        [poi("Fontana Vecchia", "spring"), poi("Monte Ocone", "peak")], None
    )
    assert (name, source) == ("Monte Ocone", "peak")


def test_priority_order_is_respected_end_to_end():
    pois = [poi(f"n-{t}", t) for t in reversed(NAME_PRIORITY)]
    name, source = route_name(pois, None)
    assert source == NAME_PRIORITY[0]
    assert name == f"n-{NAME_PRIORITY[0]}"


def test_a_type_outside_the_priority_list_never_names_a_route():
    """A spring beside the path is not what the outing is called, even when it
    is the only named thing on it."""
    assert route_name([poi("Sorgente del Fumlacc", "spring")], None) == (None, "none")


def test_falls_back_to_the_trailhead_then_to_nothing():
    assert route_name([], "Piani Resinelli") == ("Piani Resinelli", "trailhead")
    assert route_name([], None) == (None, "none")


def test_an_unnamed_poi_of_the_right_type_is_skipped():
    """POIs often have a type and no name; that cannot become the route's."""
    name, source = route_name(
        [poi(None, "peak"), poi("Bocchetta Alta", "saddle")], None
    )
    assert (name, source) == ("Bocchetta Alta", "saddle")


def test_no_name_is_invented_from_an_id_or_a_coordinate():
    name, _ = route_name([], None)
    assert name is None


@pytest.mark.parametrize(
    ("rating", "level"),
    [(0, 1), (1, 1), (2, 2), (3, 3), (4, 3), (5, 4), (6, 4)],
)
def test_mtb_rating_maps_onto_our_difficulty_levels(rating, level):
    """mtb:scale is 0-6 and our difficulty is 1-4, so the mapping is lossy;
    this pins where the boundaries sit."""
    assert mtb_level(rating) == level


def test_an_unrated_route_gets_the_middle_level_not_the_easiest():
    """Unknown is not easy. Defaulting to 1 would make an unrated route look
    like the quickest option in a list."""
    assert mtb_level(None) == DEFAULT_MTB_LEVEL
    assert mtb_level(99) == DEFAULT_MTB_LEVEL


def test_descent_is_costed_as_well_as_ascent():
    """A loop returns to its start, so it descends everything it climbed, and
    DIN 33466 charges for both. Passing descent as 0 would understate a
    2,000 m day by hours."""
    row = {"distance_m": 12000, "ascent_m": 1600, "mtb_rating": 2}
    hike_min, _ = durations(row)
    flat = {"distance_m": 12000, "ascent_m": 0, "mtb_rating": 2}
    assert hike_min > durations(flat)[0]


def test_both_durations_are_stored_whatever_the_activity():
    """The frontend's primaryDuration() picks by activity and expects both
    fields present, so a hike route still needs an mtb figure."""
    hike_min, mtb_min = durations(
        {"distance_m": 10000, "ascent_m": 800, "mtb_rating": None}
    )
    assert hike_min > 0
    assert mtb_min > 0


def test_missing_elevation_does_not_crash_the_estimate():
    """Routes generated before elevation existed carry a null ascent."""
    hike_min, mtb_min = durations(
        {"distance_m": 10000, "ascent_m": None, "mtb_rating": None}
    )
    assert hike_min > 0 and mtb_min > 0
