"""Assembling a route document. No database — pure functions."""

from __future__ import annotations

import json

import pytest

from export.document import (
    SAC_ORDER,
    Span,
    build_document,
    dominant,
    quality_warnings,
    shares,
    significant_grade,
)


def test_shares_are_length_weighted_not_counted():
    # Two short paved stretches and one long unpaved one: the route is unpaved,
    # even though "paved" appears twice.
    result = shares([Span("paved", 50), Span("paved", 50), Span("unpaved", 900)])

    assert result == {"paved": 0.1, "unpaved": 0.9}


def test_untagged_length_is_reported_not_dropped():
    # A route 40% untagged must say so. Renormalising over the tagged 60% would
    # report a confident distribution of a minority of the route.
    result = shares([Span("unpaved", 600), Span(None, 400)])

    assert result == {"unpaved": 0.6, "unknown": 0.4}


def test_zero_and_negative_lengths_are_ignored():
    assert shares([Span("paved", 0), Span("unpaved", 100)]) == {"unpaved": 1.0}
    assert shares([]) == {}


def test_dominant_ignores_unknown():
    # "mostly unknown" is not a surface. If nothing is tagged, say nothing.
    assert dominant({"unknown": 0.9, "unpaved": 0.1}) == "unpaved"
    assert dominant({"unknown": 1.0}) is None
    assert dominant({}) is None


def test_dominant_breaks_ties_the_same_way_every_time():
    # A document that changes between identical runs is not a document.
    distribution = {"paved": 0.5, "unpaved": 0.5}

    assert dominant(distribution) == dominant(distribution) == "paved"


def test_a_short_scramble_does_not_make_a_valley_walk_alpine():
    # 30 m of alpine_hiking on a 20 km walk is an incident, not the character of
    # the route. This is the rule backend/graph/graphhopper.py proved.
    grade = significant_grade(
        [Span("hiking", 20_000), Span("alpine_hiking", 30)], SAC_ORDER
    )

    assert grade == "hiking"


def test_a_sustained_hard_section_does_set_the_grade():
    grade = significant_grade(
        [Span("hiking", 8_000), Span("alpine_hiking", 2_000)], SAC_ORDER
    )

    assert grade == "alpine_hiking"


def test_exactly_five_percent_counts():
    # The rule is "at least 5%", so the boundary belongs to the harder grade.
    grade = significant_grade(
        [Span("hiking", 9_500), Span("alpine_hiking", 500)], SAC_ORDER
    )

    assert grade == "alpine_hiking"


def test_the_hardest_significant_grade_wins_not_the_largest_share():
    grade = significant_grade(
        [
            Span("hiking", 5_000),
            Span("mountain_hiking", 3_000),
            Span("demanding_alpine_hiking", 2_000),
        ],
        SAC_ORDER,
    )

    assert grade == "demanding_alpine_hiking"


def test_junk_grades_are_ignored_not_guessed_at():
    # 12 edges in this network carry values outside the enum, one of them a
    # sentence about stone ruins.
    grade = significant_grade(
        [Span("a sentence about stone ruins", 5_000), Span("hiking", 5_000)],
        SAC_ORDER,
    )

    assert grade == "hiking"


def test_an_entirely_ungraded_route_has_no_grade():
    assert significant_grade([Span(None, 10_000)], SAC_ORDER) is None
    assert significant_grade([], SAC_ORDER) is None


def test_warnings_say_what_a_reader_needs_before_trusting_the_document():
    warnings = quality_warnings(
        pieces=3, edges_without_profile=2, matched_fraction=0.05, places=0
    )

    assert len(warnings) == 4
    assert any("3 disconnected pieces" in w for w in warnings)
    assert any("no altitude profile" in w for w in warnings)
    assert any("fragment" in w for w in warnings)
    assert any("no named place" in w for w in warnings)


def test_a_clean_route_carries_no_warnings():
    assert (
        quality_warnings(
            pieces=1, edges_without_profile=0, matched_fraction=0.97, places=6
        )
        == []
    )


def test_an_unmatched_fraction_of_none_is_not_treated_as_a_fragment():
    # A generated route has no relation to match against; absent is not zero.
    warnings = quality_warnings(
        pieces=1, edges_without_profile=0, matched_fraction=None, places=2
    )

    assert warnings == []


def sample_document(**overrides):
    base = {
        "route_id": "osm-relation-123",
        "kind": "osm_route",
        "identity": {"name": "Test", "ref": "33"},
        "geometry": {"type": "LineString", "coordinates": [[9.4, 45.9], [9.41, 45.91]]},
        "bbox": [9.4, 45.9, 9.41, 45.91],
        "distance_m": 1234.56,
        "ascent_m": 100.4,
        "descent_m": 20.1,
        "lowest_m": 600.0,
        "highest_m": 700.0,
        "profile": {"distance_m": [0.0, 1234.6], "elevation_m": [600.0, 700.0]},
        "surface_spans": [Span("unpaved", 1000), Span("paved", 234.56)],
        "sac_spans": [Span("mountain_hiking", 1234.56)],
        "pieces": 1,
        "edges_without_profile": 0,
        "matched_fraction": 0.95,
        "places": [{"name": "Rifugio", "kind": "hut", "distance_along_m": 500.0}],
        "start": {"vertex_id": 42, "car_free": False},
        "provenance": {"run_id": "curate-abc", "licence": "ODbL"},
    }
    base.update(overrides)
    return build_document(**base)


def test_a_document_is_json_serialisable_and_stable():
    first = json.dumps(sample_document(), sort_keys=False)
    second = json.dumps(sample_document(), sort_keys=False)

    assert first == second


def test_unknown_climb_stays_null_and_never_becomes_zero():
    # The failure this guards against: a route whose profile is missing
    # reporting 0 m of ascent, which reads as "flat" rather than "unknown".
    document = sample_document(ascent_m=None, descent_m=None, edges_without_profile=4)

    assert document["measures"]["ascent_m"] is None
    assert document["measures"]["descent_m"] is None
    assert any("no altitude profile" in w for w in document["quality"]["warnings"])


def test_duration_is_absent_rather_than_wrong():
    # DIN 33466 rates the classic Grigna ascent at 10 hours against a guidebook
    # 6-8. Shipping that figure would be worse than shipping none.
    assert "duration_min" not in sample_document()["measures"]


def test_the_document_carries_its_own_difficulty_rule():
    difficulty = sample_document()["difficulty"]

    assert difficulty["sac_scale"] == "mountain_hiking"
    assert "5%" in difficulty["rule"]


def test_surface_keeps_the_distribution_and_names_a_dominant():
    surface = sample_document()["surface"]

    assert surface["dominant"] == "unpaved"
    assert surface["distribution"]["unpaved"] == pytest.approx(0.81, abs=0.01)
    assert sum(surface["distribution"].values()) == pytest.approx(1.0)
