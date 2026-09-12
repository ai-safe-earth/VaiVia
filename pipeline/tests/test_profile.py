"""Ascent and descent from a profile. No database — pure functions."""

from __future__ import annotations

import pytest

from curate.profile import ascent_descent


def test_a_monotone_climb_is_all_ascent():
    assert ascent_descent([100.0, 110.0, 130.0]) == (30.0, 0.0)


def test_a_monotone_drop_is_all_descent():
    assert ascent_descent([130.0, 110.0, 100.0]) == (0.0, 30.0)


def test_undulation_is_counted_both_ways_not_netted():
    # The point of keeping both: a walk that climbs 50 and drops 50 is not the
    # same walk as one that stays level, even though the net is zero.
    assert ascent_descent([100.0, 150.0, 100.0]) == (50.0, 50.0)


def test_reversing_the_profile_swaps_the_two_numbers():
    profile = [100.0, 140.0, 120.0, 180.0]
    up, down = ascent_descent(profile)
    reversed_up, reversed_down = ascent_descent(list(reversed(profile)))

    assert (reversed_up, reversed_down) == (down, up)
    # Which is why an assembled route that reverses a piece must swap them,
    # exactly as it inverts oneway and incline.
    assert (up, down) == (100.0, 20.0)


def test_a_level_profile_has_no_climb():
    assert ascent_descent([200.0, 200.0, 200.0]) == (0.0, 0.0)


def test_one_missing_sample_makes_the_whole_edge_unknown():
    # Not "the climb of the covered part". 57 vertices sit outside the single
    # GLO-30 tile; an edge touching them has unknown climb, and reporting the
    # partial sum would understate it with nothing to say so.
    assert ascent_descent([100.0, None, 130.0]) is None
    assert ascent_descent([None, 100.0, 130.0]) is None
    assert ascent_descent([100.0, 130.0, None]) is None


def test_too_short_to_have_a_gradient():
    assert ascent_descent([100.0]) is None
    assert ascent_descent([]) is None
    assert ascent_descent(None) is None


def test_small_steps_are_kept_there_is_no_threshold():
    # Measured, not assumed: a bilinear profile's |dz| scales with point
    # spacing all the way down to 0.12 m at sub-2 m steps, so a 0.2 m rise
    # between two points 2 m apart is a 10% grade, not noise to discard.
    up, down = ascent_descent([100.0, 100.2, 100.4, 100.3])

    assert up == pytest.approx(0.4)
    assert down == pytest.approx(0.1)


def test_a_long_profile_accumulates_without_drift():
    # 1,000 alternating steps: the sums must be exact, not float-fuzzy, because
    # a route's ascent is the sum of hundreds of these.
    profile = [100.0]
    for _ in range(500):
        profile.extend([profile[-1] + 2.0, profile[-1] + 2.0 - 1.0])
    up, down = ascent_descent(profile)

    assert up == pytest.approx(1000.0)
    assert down == pytest.approx(500.0)
