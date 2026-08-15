import pytest

from core.durations import difficulty_level, hike_duration_min, mtb_duration_min


def test_difficulty_levels():
    assert difficulty_level("Easy") == 1
    assert difficulty_level("Pro") == 4
    with pytest.raises(ValueError):
        difficulty_level("Extreme")


def test_hike_flat_din33466():
    # 12 km flat: 3 h horizontal, no vertical -> 180 min
    assert hike_duration_min(12_000, 0, 0) == 180


def test_hike_with_climb():
    # 8 km + 600 m up + 600 m down: horizontal 2 h; vertical 600/300 + 600/500 = 3.2 h
    # DIN: max(3.2, 2) + min/2 = 3.2 + 1 = 4.2 h = 252 min
    assert hike_duration_min(8_000, 600, 600) == 252


def test_hike_null_elevation_degrades_to_flat():
    assert hike_duration_min(12_000, None, None) == 180


def test_mtb_speed_by_level():
    # 30 km flat Easy at 15 km/h = 120 min; Pro at 8 km/h = 225 min
    assert mtb_duration_min(30_000, 0, 1) == 120
    assert mtb_duration_min(30_000, 0, 4) == 225


def test_mtb_climbing_penalty():
    # 13 km Intermediate at 13 km/h = 60 min, +800 m gain = +60 min
    assert mtb_duration_min(13_000, 800, 2) == 120
