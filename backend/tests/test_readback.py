"""How I read it: the executed plan, in the walker's own words.

Each of these pins a decision the composer makes that is invisible everywhere
else on screen. That is the whole reason the block exists — not to echo the
question back, but to show where the system did something to it.
"""

from chat.composer import compose
from chat.intents import (
    LoopSearchIntent,
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


def test_the_widened_band_is_what_is_shown_not_the_number_asked_for():
    """ "a 15 km loop" arrives as min=max=15000, which matches nothing, so the
    composer widens it. The band that RAN is what the reading must show —
    otherwise the one number the user gave looks unmodified while the search
    used another."""
    rows = rows_for(LoopSearchIntent(min_distance_m=15000, max_distance_m=15000))
    assert rows["distance"] == "12 km to 18 km"


def test_a_dropped_duration_says_so_in_full():
    """The catalogue carries no duration until DIN 33466 is calibrated, so the
    filter is dropped. Silently dropping it is the exact failure the rule was
    written against, so the reading names it."""
    rows = rows_for(LoopSearchIntent(max_duration_min=120, max_distance_m=12000))
    assert "2 h" in rows["time"]
    assert "not filtered" in rows["time"]


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
    both = rows_for(TrailSearchIntent(activity="hike", max_distance_m=16000))
    assert both["looked in"] == "named trails and our route catalogue"

    # A season cannot be checked on the catalogue, so that ask is trails-only
    # — and says why, rather than quietly returning less.
    trails_only = rows_for(
        TrailSearchIntent(activity="hike", max_distance_m=16000, season="winter")
    )
    assert "named trails only" in trails_only["looked in"]

    # A theme has no embeddings on the catalogue either.
    themed = rows_for(
        TrailSearchIntent(activity="hike", max_distance_m=16000),
        SemanticThemeIntent(text="shady forest"),
    )
    assert themed["looked in"] == "named trails, matched by description"
    assert themed["described as"] == "shady forest"

    # A loop ask is the catalogue by construction.
    loops = rows_for(LoopSearchIntent(max_distance_m=12000))
    assert loops["looked in"] == "our route catalogue"


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
