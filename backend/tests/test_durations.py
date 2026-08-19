import pytest

from core.durations import difficulty_level, hike_duration_min, mtb_duration_min


def test_difficulty_levels():
    assert difficulty_level("Easy") == 1
    assert difficulty_level("Pro") == 4
    with pytest.raises(ValueError):
        difficulty_level("Extreme")


def test_hike_flat_is_unchanged_at_4kmh():
    # 12 km flat: 3 h horizontal, no vertical -> 180 min. The calibration moved
    # the vertical rates only; flat walking was never the part that was wrong.
    assert hike_duration_min(12_000, 0, 0) == 180


def test_hike_with_climb():
    # 8 km + 600 m up + 600 m down: horizontal 2 h;
    # vertical 600/450 + 600/600 = 2.33 h; max + min/2 = 2.33 + 1 = 3.33 h
    assert hike_duration_min(8_000, 600, 600) == 200


def test_the_grigna_reference_case_lands_in_the_guidebook_band():
    """The case the vertical rates are calibrated against.

    The classic Grigna ascent is 12 km with 1,600 m of climb, and a loop
    returns to its start so descent equals ascent. Guidebooks put it at 6-8
    hours. Unmodified DIN 33466 gives 10.0, which is what made the catalogue
    read 15+ hours and cost the numbers their credibility. Moving the rates in
    core.durations must be a deliberate act, so this pins the consequence.
    """
    minutes = hike_duration_min(12_000, 1_600, 1_600)
    assert 6 * 60 <= minutes <= 8 * 60, f"{minutes / 60:.1f} h is outside 6-8 h"


def test_hike_null_elevation_degrades_to_flat():
    assert hike_duration_min(12_000, None, None) == 180


def test_mtb_speed_by_level():
    # 30 km flat Easy at 15 km/h = 120 min; Pro at 8 km/h = 225 min
    assert mtb_duration_min(30_000, 0, 1) == 120
    assert mtb_duration_min(30_000, 0, 4) == 225


def test_mtb_climbing_penalty():
    # 13 km Intermediate at 13 km/h = 60 min, +800 m gain = +60 min
    assert mtb_duration_min(13_000, 800, 2) == 120
