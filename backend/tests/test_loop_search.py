"""loop_search: the chat layer selecting from the precomputed catalogue."""

import pytest

from chat.composer import compose, merge_loops
from chat.intents import (
    ClarifyIntent,
    LoopSearchIntent,
    SemanticThemeIntent,
    TrailSearchIntent,
)


def test_a_loop_ask_alone_is_actionable():
    """A loop with only a distance is a real request; it must not clarify the
    way a bare trail_search does."""
    plan = compose([LoopSearchIntent(max_distance_m=15000)])
    assert not plan.is_clarify
    assert plan.loop is not None
    assert plan.loop.max_distance_m == 15000


def test_an_empty_loop_is_still_actionable():
    """'a loop' with no constraints is answerable from the catalogue -- ranked
    by score -- unlike a bare trail_search, which has nothing to filter on."""
    plan = compose([LoopSearchIntent()])
    assert not plan.is_clarify


def test_loops_merge_tightest_wins():
    merged = merge_loops(
        [
            LoopSearchIntent(max_distance_m=20000, min_distance_m=5000),
            LoopSearchIntent(max_distance_m=15000, min_distance_m=8000),
        ]
    )
    assert merged.max_distance_m == 15000
    assert merged.min_distance_m == 8000


def test_loop_merge_unions_features_and_ors_avoid_roads():
    merged = merge_loops(
        [
            LoopSearchIntent(poi_types=["hut"], avoid_roads=True),
            LoopSearchIntent(poi_types=["hut", "lake"], near="Lecco"),
        ]
    )
    assert merged.poi_types == ["hut", "lake"]
    assert merged.avoid_roads is True
    assert merged.near == "Lecco"


def test_zero_bounds_are_dropped_as_vacuous():
    """Same trap as trail_search: under strict structured outputs the model
    writes 0 for 'no limit', and a 0-metre max matches nothing."""
    merged = merge_loops([LoopSearchIntent(max_distance_m=0.0, min_distance_m=0.0)])
    assert merged.max_distance_m is None
    assert merged.min_distance_m is None


def test_a_clarify_still_poisons_a_plan_containing_a_loop():
    """The containment property must hold for the new intent too: one
    adversarial subquery stops the whole turn, loop included."""
    plan = compose(
        [
            LoopSearchIntent(max_distance_m=10000),
            ClarifyIntent(question="?", suggestions=[]),
        ]
    )
    assert plan.is_clarify
    assert plan.loop is None


def test_a_loop_coexists_with_a_theme_and_a_trail_search():
    plan = compose(
        [
            LoopSearchIntent(max_distance_m=12000),
            SemanticThemeIntent(text="panoramic ridge"),
            TrailSearchIntent(activity="hike"),
        ]
    )
    assert plan.loop is not None
    assert plan.theme == "panoramic ridge"
    assert plan.search is not None


def test_the_intent_carries_nothing_a_query_could_be_steered_with():
    """The boundary rule: no field may name a template, an id, or Cypher.

    Pinned so adding one is a deliberate act. Every field here is either a
    bounded number, a closed enum, or a place name resolved server-side against
    known POIs — none can carry a query, a template, or a database identifier.
    """
    assert set(LoopSearchIntent.model_fields) == {
        "kind",
        "activity",
        "min_distance_m",
        "max_distance_m",
        "max_ascent_m",
        "max_difficulty_level",
        "poi_types",
        "near",
        "avoid_roads",
    }


def test_activity_is_a_closed_enum_not_free_text():
    """It selects which catalogue is searched, so an arbitrary string would
    reach the query as a value the model chose."""
    import pydantic

    assert LoopSearchIntent(activity="mtb").activity == "mtb"
    with pytest.raises(pydantic.ValidationError):
        LoopSearchIntent(activity="; MATCH (n) DETACH DELETE n")


def test_difficulty_maps_onto_the_scale_matching_the_activity():
    """sac_scale and mtb:scale are different scales. Applying a hiking ceiling
    to a bike search would constrain it by a number that means something else
    there."""
    from chat.orchestrator import HIKE_RATING_BY_LEVEL, MTB_RATING_BY_LEVEL

    assert HIKE_RATING_BY_LEVEL[1] < HIKE_RATING_BY_LEVEL[4]
    assert MTB_RATING_BY_LEVEL[1] < MTB_RATING_BY_LEVEL[4]
    assert set(HIKE_RATING_BY_LEVEL) == {1, 2, 3, 4}
    assert set(MTB_RATING_BY_LEVEL) == {1, 2, 3, 4}


@pytest.mark.asyncio
async def test_a_hiking_ask_does_not_apply_an_mtb_ceiling(db):
    from chat.orchestrator import ChatOrchestrator

    db.when("search_loops", [])
    orchestrator = ChatOrchestrator(db=db, llm=None, store=None, embedder=None)
    await orchestrator._loops(  # noqa: SLF001
        LoopSearchIntent(activity="hike", max_difficulty_level=2)
    )
    _, params = next(c for c in db.calls if c[0] == "search_loops")
    assert params["activity"] == "hike"
    assert params["max_hike_rating"] is not None
    assert params["max_mtb_rating"] is None


@pytest.mark.asyncio
async def test_an_unstated_activity_searches_every_catalogue(db):
    """Null means no preference, the same rule the activity fix established for
    trail_search."""
    from chat.orchestrator import ChatOrchestrator

    db.when("search_loops", [])
    orchestrator = ChatOrchestrator(db=db, llm=None, store=None, embedder=None)
    await orchestrator._loops(LoopSearchIntent(max_distance_m=10000))  # noqa: SLF001
    _, params = next(c for c in db.calls if c[0] == "search_loops")
    assert params["activity"] is None


def test_a_single_stated_distance_becomes_a_band_not_an_equality():
    """ "a 15 km loop" arrives as min=max=15000. Real routes are 15,328 m, so an
    exact filter matches nothing and the user is told no such loop exists while
    500 of them sit in the catalogue."""
    merged = merge_loops([LoopSearchIntent(min_distance_m=15000, max_distance_m=15000)])
    assert merged.min_distance_m < 15000 < merged.max_distance_m
    assert merged.min_distance_m == pytest.approx(12000)
    assert merged.max_distance_m == pytest.approx(18000)


def test_a_genuine_range_is_left_alone():
    merged = merge_loops([LoopSearchIntent(min_distance_m=10000, max_distance_m=20000)])
    assert merged.min_distance_m == 10000
    assert merged.max_distance_m == 20000


def test_an_open_ended_bound_is_left_alone():
    merged = merge_loops([LoopSearchIntent(max_distance_m=15000)])
    assert merged.min_distance_m is None
    assert merged.max_distance_m == 15000


@pytest.mark.asyncio
async def test_loops_execute_against_the_catalogue(db):
    """Exercises the orchestrator path, not just the composer. The first version
    of this feature passed every composer test and still 500'd on every request,
    because _loops referenced a misnamed attribute."""
    from chat.orchestrator import ChatOrchestrator

    db.when(
        "search_loops",
        [{"id": "th1:15000:0", "distance_m": 15300.0, "score": 0.9}],
    )
    orchestrator = ChatOrchestrator(db=db, llm=None, store=None, embedder=None)
    rows = await orchestrator._loops(  # noqa: SLF001 — exercising the real path
        LoopSearchIntent(max_distance_m=16000, poi_types=["peak"])
    )
    assert [r["id"] for r in rows] == ["th1:15000:0"]
    name, params = next(c for c in db.calls if c[0] == "search_loops")
    assert params["max_distance_m"] == 16000
    assert params["poi_types"] == ["peak"]


@pytest.mark.asyncio
async def test_avoid_roads_sets_an_off_road_floor(db):
    from chat.orchestrator import ChatOrchestrator

    db.when("search_loops", [])
    orchestrator = ChatOrchestrator(db=db, llm=None, store=None, embedder=None)
    await orchestrator._loops(LoopSearchIntent(avoid_roads=True))  # noqa: SLF001
    _, params = next(c for c in db.calls if c[0] == "search_loops")
    assert params["min_off_road"] is not None and params["min_off_road"] > 0.5

    await orchestrator._loops(LoopSearchIntent(avoid_roads=False))  # noqa: SLF001
    _, params = [c for c in db.calls if c[0] == "search_loops"][-1]
    assert params["min_off_road"] is None


def test_a_loops_only_turn_is_labelled_loop_search():
    """The kind label is client-facing; calling a catalogue loop a trail_search
    makes the frontend render it as something it is not."""
    from chat.orchestrator import ChatOrchestrator

    plan = compose([LoopSearchIntent(max_distance_m=15000)])
    assert ChatOrchestrator._result_kind(plan) == "loop_search"  # noqa: SLF001


def test_a_mixed_turn_still_reads_as_a_trail_search():
    from chat.orchestrator import ChatOrchestrator

    plan = compose(
        [LoopSearchIntent(max_distance_m=15000), SemanticThemeIntent(text="ridge")]
    )
    assert ChatOrchestrator._result_kind(plan) == "trail_search"  # noqa: SLF001
