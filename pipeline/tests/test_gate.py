"""The publication gate's rules. Pure — no database, like every pipeline test."""

from __future__ import annotations

from curate.gate import MAX_URBAN_SHARE, judge, paved_share


def route(**overrides):
    """A route that passes everything, so each test states its one defect."""
    return {
        "urban_share": 0.1,
        "surface": {"ground": 0.7, "asphalt": 0.1, "unknown": 0.2},
        **overrides,
    }


def test_a_clean_route_passes_with_no_reasons():
    assert judge(route()) == ("pass", [])


def test_a_town_walk_fails_on_urban_share():
    verdict, reasons = judge(route(urban_share=MAX_URBAN_SHARE + 0.1))
    assert verdict == "fail"
    assert any("urban share" in r for r in reasons)


def test_the_urban_threshold_itself_passes():
    # A boundary is inclusive on the good side: the rule is "over", not "at".
    assert judge(route(urban_share=MAX_URBAN_SHARE))[0] == "pass"


def test_a_road_route_fails_on_paved_share():
    verdict, reasons = judge(
        route(surface={"asphalt": 0.3, "paving_stones": 0.2, "ground": 0.5})
    )
    assert verdict == "fail"
    assert any("paved share" in r for r in reasons)


def test_paved_share_sums_the_made_surfaces_and_ignores_unknown():
    assert paved_share({"asphalt": 0.3, "concrete": 0.1, "unknown": 0.6}) == 0.4
    assert paved_share({"ground": 1.0}) == 0.0


def test_every_broken_rule_is_reported_not_just_the_first():
    # The reasons ARE the calibration surface: stopping at the first would
    # hide which threshold is doing the work while they are being tuned.
    _, reasons = judge(route(urban_share=0.9, surface={"asphalt": 1.0}))
    assert len(reasons) == 2


def test_an_unmeasured_route_is_held_never_passed():
    # Absent is not zero: a NULL urban_share must not publish a town walk
    # by omission.
    verdict, reasons = judge(route(urban_share=None))
    assert verdict == "held"
    assert reasons == ["urban_share not measured"]


def test_held_beats_a_rule_that_would_have_passed():
    verdict, _ = judge(route(surface=None))
    assert verdict == "held"
