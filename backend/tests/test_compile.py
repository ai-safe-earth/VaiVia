"""compile.py owns every number the planner sees — each translation pinned.

The reference case is ask A (docs/route-design.md): ~3 h for kids on bikes
reads as 12–18 km. Moving a pace or a cap must move a test, deliberately.
"""

from chat.compile import (
    COVERAGE_ANSWER,
    SURFACE_COST_FACTOR,
    SURFACE_SHARE_CAP,
    Constraints,
    compile_outing,
)
from chat.intents import ClarifyIntent, OutingIntent, StartSpec, Waypoint


def outing(**kwargs) -> OutingIntent:
    return OutingIntent(activity=kwargs.pop("activity", "hike"), **kwargs)


def test_ask_a_kids_on_bikes_three_hours_is_12_to_18_km():
    c = compile_outing(
        outing(
            activity="bike",
            shape="loop",
            party="kids",
            min_hours=3,
            max_hours=3,
            surface_exclusions=["asphalt"],
            start=StartSpec(mode="here", max_drive_min=60),
        )
    )
    assert isinstance(c, Constraints)
    assert c.activity == "mtb"  # the bike cost layer
    low, high = c.distance_band_m
    assert round(low / 1000) == 12 and round(high / 1000) == 18
    assert c.surface_cost_factors == {"asphalt": SURFACE_COST_FACTOR}
    assert c.surface_share_caps == {"asphalt": SURFACE_SHARE_CAP}
    assert c.start_mode == "here" and c.max_drive_min == 60
    assert any("12–18 km" in a for a in c.assumptions)


def test_a_stated_distance_cap_beats_the_hours_estimate():
    c = compile_outing(outing(max_hours=3, max_distance_km=10))
    assert c.distance_band_m == (0.0, 10000.0)
    assert not any("km" in a and "read" in a for a in c.assumptions)


def test_hours_cap_the_climb_and_a_stated_cap_wins():
    estimated = compile_outing(outing(max_hours=4))
    # 4 h × half the time climbing × 450 m/h = 900 m
    assert estimated.max_ascent_m == 900
    stated = compile_outing(outing(max_hours=4, max_ascent_m=500))
    assert stated.max_ascent_m == 500.0


def test_party_caps_the_technical_grade():
    kids = compile_outing(outing(party="kids"))
    assert kids.sac_cap == "hiking" and kids.mtb_cap == "1"
    small = compile_outing(outing(party="small_kids"))
    assert small.mtb_cap == "0"
    adults = compile_outing(outing(party="adults"))
    assert adults.sac_cap is None and adults.mtb_cap is None


def test_setting_is_a_cap_or_a_floor_never_both():
    nature = compile_outing(outing(setting="nature"))
    assert nature.urban_share_max is not None and nature.urban_share_min is None
    town = compile_outing(outing(setting="town"))
    assert town.urban_share_min is not None and town.urban_share_max is None


def test_roles_expand_to_kind_sets_and_a_stated_kind_narrows():
    c = compile_outing(
        outing(
            waypoints=[
                Waypoint(role="bathe"),
                Waypoint(kind="lake", role="end"),
            ]
        )
    )
    assert set(c.waypoints[0].kinds) == {
        "lake",
        "beach",
        "river_access",
        "bathing_water",
    }
    assert c.waypoints[1].kinds == ("lake",)


def test_multi_day_brings_sleep_kinds():
    c = compile_outing(outing(days=3, sleep="hut"))
    assert c.sleep_kinds == ("hut",)
    any_sleep = compile_outing(outing(days=3))
    assert set(any_sleep.sleep_kinds) == {"hut", "campsite", "agriturismo"}
    one_day = compile_outing(outing(days=1, sleep="hut"))
    assert one_day.sleep_kinds == ()


def test_ask_e_uncovered_area_is_a_python_clarify_naming_coverage():
    c = compile_outing(outing(activity="bike", days=7, area="Tuscany"))
    assert isinstance(c, ClarifyIntent)
    assert "Tuscany" in c.question
    assert COVERAGE_ANSWER in c.question
    # a covered massif compiles, leading articles and case notwithstanding
    assert isinstance(compile_outing(outing(area="Orobie")), Constraints)
    assert isinstance(compile_outing(outing(area="  lecco ")), Constraints)
    assert isinstance(compile_outing(outing(area="the Orobie")), Constraints)
    assert isinstance(compile_outing(outing(area="le Grigne")), Constraints)
    assert isinstance(compile_outing(outing(area="la Grigna")), Constraints)
