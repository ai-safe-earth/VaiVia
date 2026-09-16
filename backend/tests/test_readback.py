"""How I read it: the executed plan, in the walker's own words.

Each of these pins a decision the composer makes that is invisible everywhere
else on screen. That is the whole reason the block exists — not to echo the
question back, but to show where the system did something to it.
"""

from chat.composer import compose
from chat.intents import (
    RouteIntent,
    SemanticThemeIntent,
    TrailSearchIntent,
)
from chat.readback import describe, readback


def rows_for(*intents):
    return {r["key"]: r["value"] for r in describe(compose(list(intents)))}


def test_a_clarify_turn_reads_back_nothing():
    """Nothing was searched, so there is nothing to describe — and the
    fragment is absent rather than empty, so the client can tell the two
    apart."""
    from chat.intents import ClarifyIntent

    plan = compose([ClarifyIntent(question="Which one?", suggestions=[])])
    assert describe(plan) == []
    assert readback(plan) == {}


def test_features_are_read_back_as_the_conjunction_they_run_as():
    """ "a lake or a hut" runs as lake AND hut. Nothing else on screen reveals
    that, so a walker who asked for either cannot otherwise tell why so few
    routes came back."""
    rows = rows_for(TrailSearchIntent(poi_types=["lake", "hut"], max_distance_m=9000))
    assert rows["passes"] == "lake and hut"

    three = rows_for(
        TrailSearchIntent(poi_types=["lake", "hut", "peak"], max_distance_m=9000)
    )
    assert three["passes"] == "lake, hut and peak"


def test_family_friendly_is_read_back_as_the_cap_it_becomes():
    """The flag caps difficulty at 1 in the orchestrator; the reading shows
    the cap, because that is what filtered the results."""
    rows = rows_for(TrailSearchIntent(family_friendly=True, max_difficulty_level=3))
    assert rows["difficulty"] == "easy only, for children"


def test_it_says_which_store_was_searched():
    plain = rows_for(TrailSearchIntent(activity="hike", max_distance_m=16000))
    assert plain["looked in"] == "named trails"

    themed = rows_for(
        TrailSearchIntent(activity="hike", max_distance_m=16000),
        SemanticThemeIntent(text="shady forest"),
    )
    assert themed["looked in"] == "named trails, matched by description"
    assert themed["described as"] == "shady forest"


def test_climb_is_metres_and_distance_is_kilometres():
    rows = rows_for(TrailSearchIntent(min_elevation_gain_m=1000, max_distance_m=20000))
    assert rows["climb"] == "over 1000 m"
    assert rows["distance"] == "under 20 km"


def test_a_route_ask_reads_back_its_endpoints():
    rows = rows_for(RouteIntent(start="Lecco", end="Abbadia"))
    assert rows["route"] == "Lecco to Abbadia"


def test_every_value_is_a_string_a_walker_could_have_said():
    """No ids, no field names, no metres-as-raw-numbers leaking through."""
    plan = compose(
        [
            TrailSearchIntent(
                activity="mtb",
                max_distance_m=15000,
                max_difficulty_level=2,
                poi_types=["lake"],
                region="Bergamo",
                exclude_hazards=["snow_risk"],
                surface_exclusions=["asphalt"],
            )
        ]
    )
    for row in describe(plan):
        assert row["key"].islower()
        assert "_" not in row["value"], row
        assert row["value"] == row["value"].strip()


def test_a_duration_is_stated_plainly():
    # Trails really are post-filtered by duration, so the row claims no more
    # than the query did.
    rows = rows_for(TrailSearchIntent(activity="hike", max_duration_min=120))
    assert rows["looked in"] == "named trails"
    assert rows["time"] == "under 2 h"


def test_a_difficulty_floor_and_ceiling_are_both_shown():
    """Rendering only the ceiling hid a floor the query applied."""
    rows = rows_for(TrailSearchIntent(min_difficulty_level=2, max_difficulty_level=3))
    assert rows["difficulty"] == "intermediate to difficult"


def test_a_single_difficulty_still_reads_as_one_word():
    rows = rows_for(TrailSearchIntent(min_difficulty_level=3, max_difficulty_level=3))
    assert rows["difficulty"] == "difficult"


def test_the_stated_band_is_shown_as_it_ran():
    """Trail search runs the band the user stated, so the row is that band."""
    assert (
        rows_for(TrailSearchIntent(min_distance_m=10000, max_distance_m=20000))[
            "distance"
        ]
        == "10 km to 20 km"
    )
    assert (
        rows_for(TrailSearchIntent(min_distance_m=15000, max_distance_m=15000))[
            "distance"
        ]
        == "exactly 15 km"
    )
