"""Which snapped features can begin a walk. No database — pure functions."""

from __future__ import annotations

import pytest

from curate.anchors import (
    DESTINATION_NOT_START,
    STARTING_POI,
    poi_verdict,
    settlement_verdict,
    stop_verdict,
)


@pytest.mark.parametrize("poi_type", sorted(STARTING_POI))
def test_the_starting_classes_start(poi_type):
    verdict = poi_verdict(poi_type)

    assert verdict.is_start
    assert verdict.note is None


@pytest.mark.parametrize("poi_type", sorted(DESTINATION_NOT_START))
def test_a_destination_does_not_start_a_walk(poi_type):
    # A hut is two hours above the nearest road. Routes beginning there are
    # routes nobody can start.
    verdict = poi_verdict(poi_type)

    assert not verdict.is_start
    assert verdict.note, "a rejection must say why — that is the whole point"


def test_the_two_sets_do_not_overlap():
    assert not (set(STARTING_POI) & set(DESTINATION_NOT_START))


def test_an_unclassified_poi_is_rejected_by_name_not_silently():
    verdict = poi_verdict("helipad")

    assert not verdict.is_start
    assert "helipad" in verdict.note


def test_settlements_that_name_a_place_start():
    for kind in ("city", "town", "village", "hamlet"):
        assert settlement_verdict(kind).is_start


def test_a_residential_polygon_does_not_start_a_walk():
    # Its "nearest vertex" is whichever street corner the polygon reaches
    # first — a real coordinate standing for nothing in particular.
    verdict = settlement_verdict("residential")

    assert not verdict.is_start
    assert "polygon" in verdict.note


def test_an_isolated_dwelling_does_not_start_a_walk():
    verdict = settlement_verdict("isolated_dwelling")

    assert not verdict.is_start
    assert verdict.note


def test_a_stop_with_service_starts_and_one_without_does_not():
    # staging.gtfs_stop was built around this rule ("a stop with no trips is a
    # sign, not a way home"); this is where it decides something.
    assert stop_verdict(120).is_start
    assert not stop_verdict(0).is_start
    assert not stop_verdict(None).is_start


def test_a_stop_with_no_service_says_which_of_the_two_reasons_it_was():
    assert "no service data" in stop_verdict(None).note
    assert "sign" in stop_verdict(0).note


def test_a_negative_trip_count_is_treated_as_no_service():
    # Nothing should produce one, but a verdict that says "start" on nonsense
    # is worse than one that says "no service".
    assert not stop_verdict(-1).is_start
