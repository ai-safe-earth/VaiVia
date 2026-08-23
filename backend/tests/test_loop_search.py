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
        "max_duration_min",
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
    # 'hike' resolves to the catalogue's activity vocabulary, and the ceiling
    # rides the SAC rank parameter (against sac_max, the exigent grade).
    assert params["activities"] == ["hiking", "foot"]
    assert params["mtb_only"] is None
    assert params["max_sac_rank"] is not None
    assert params["max_mtb_rank"] is None


@pytest.mark.asyncio
async def test_an_unstated_activity_searches_every_catalogue(db):
    """Null means no preference, the same rule the activity fix established for
    trail_search."""
    from chat.orchestrator import ChatOrchestrator

    db.when("search_loops", [])
    orchestrator = ChatOrchestrator(db=db, llm=None, store=None, embedder=None)
    await orchestrator._loops(LoopSearchIntent(max_distance_m=10000))  # noqa: SLF001
    _, params = next(c for c in db.calls if c[0] == "search_loops")
    assert params["activities"] is None
    assert params["mtb_only"] is None


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


@pytest.mark.asyncio
async def test_an_mtb_ask_filters_on_the_rideability_conjunction(db):
    """A bike question must only see bike-legal routes — and that is
    mtb_rideable (one forbidding segment forbids), not the activity label: a
    rideable OSM hiking relation answers a bike question too."""
    from chat.orchestrator import ChatOrchestrator

    db.when("search_loops", [])
    orchestrator = ChatOrchestrator(db=db, llm=None, store=None, embedder=None)
    await orchestrator._loops(LoopSearchIntent(activity="mtb"))  # noqa: SLF001
    _, params = next(c for c in db.calls if c[0] == "search_loops")
    assert params["mtb_only"] is True
    assert params["activities"] is None


@pytest.mark.asyncio
async def test_duration_is_deliberately_not_sent_to_the_query(db):
    """The pipeline catalogue carries no duration until DIN 33466 is calibrated
    (docs/route-document.md: the uncalibrated figure reads 10 h for a 6-8 h
    classic). Filtering on a wrong number would exclude the wrong routes
    silently, so the parameter must not reach the template at all."""
    from chat.orchestrator import ChatOrchestrator

    db.when("search_loops", [])
    orchestrator = ChatOrchestrator(db=db, llm=None, store=None, embedder=None)
    await orchestrator._loops(  # noqa: SLF001
        LoopSearchIntent(max_duration_min=180, activity="mtb")
    )
    _, params = next(c for c in db.calls if c[0] == "search_loops")
    assert "max_duration_min" not in params


def test_a_zero_duration_is_dropped_as_vacuous():
    """Same trap as every other bound: 0 means 'no limit' to the model and
    'nothing matches' to the query."""
    merged = merge_loops([LoopSearchIntent(max_duration_min=0)])
    assert merged.max_duration_min is None


# ── A trail ask answers with both kinds (owner rule 2026-08-21) ─────────────


LOOP_ROW = {"id": "cat_001", "name": "To Corno dell'Arco", "distance_m": 11000}


@pytest.mark.asyncio
async def test_a_trail_ask_also_selects_from_the_catalogue(db):
    """Named trails AND catalogue outings, one ask: both blocks come back,
    each under its own key so the screen can keep them distinguishable."""
    from tests.conftest import TRAIL_ROW
    from tests.test_chat_orchestrator import build, collect, results_of

    db.when("search_trails", [TRAIL_ROW])
    db.when("search_loops", [LOOP_ROW])
    orchestrator, _, store = build(
        db, {"kind": "trail_search", "activity": "hike", "max_distance_m": 16000}
    )
    events = await collect(orchestrator, user_id="u1", message="a hike under 16 km")

    results = results_of(events)
    assert results["trails"][0]["id"] == TRAIL_ROW["id"]
    assert results["loops"][0]["id"] == "cat_001"
    # The derived ask carries the same constraints into the catalogue.
    params = db.params_for("search_loops")
    assert params["activities"] == ["hiking", "foot"]
    assert params["max_distance_m"] == 16000


@pytest.mark.asyncio
async def test_a_constraint_the_catalogue_cannot_honour_keeps_it_out(db):
    """A season (or hazard, or surface) cannot be checked on the catalogue,
    so the catalogue must not answer at all -- returning routes that ignore
    a stated constraint would be worse than returning none."""
    from tests.test_chat_orchestrator import build, collect

    db.when("search_trails", [])
    orchestrator, _, _ = build(
        db, {"kind": "trail_search", "activity": "hike", "season": "winter"}
    )
    await collect(orchestrator, user_id="u1", message="a winter hike")

    assert "search_loops" not in [name for name, _ in db.calls]


@pytest.mark.asyncio
async def test_a_theme_turn_stays_trails_only(db):
    """The catalogue has no embeddings, so a semantic theme is a constraint
    it cannot honour -- same rule as the season."""
    from tests.conftest import FakeEmbedder
    from tests.test_chat_orchestrator import build, collect

    db.when("count_embedded_trails", [{"trails": 3, "embedded": 3}])
    db.when("semantic_search_trails_filtered", [])
    orchestrator, _, _ = build(
        db,
        [
            {"kind": "trail_search", "activity": "hike", "max_distance_m": 16000},
            {"kind": "semantic_theme", "text": "shady forest"},
        ],
        embedder=FakeEmbedder(),
    )
    await collect(orchestrator, user_id="u1", message="a shady forest hike")

    assert "search_loops" not in [name for name, _ in db.calls]


@pytest.mark.asyncio
async def test_an_empty_implicit_catalogue_block_is_omitted(db):
    """Nobody asked the catalogue by name: when it has nothing, the results
    carry no loops key at all, so the answer does not apologise for a list
    the user never requested."""
    from tests.conftest import TRAIL_ROW
    from tests.test_chat_orchestrator import build, collect, results_of

    db.when("search_trails", [TRAIL_ROW])
    db.when("search_loops", [])
    orchestrator, _, _ = build(
        db, {"kind": "trail_search", "activity": "hike", "max_distance_m": 16000}
    )
    events = await collect(orchestrator, user_id="u1", message="a hike under 16 km")

    assert "loops" not in results_of(events)


# ── The cards can show more than the prose narrates ─────────────────────────


@pytest.mark.asyncio
async def test_the_answer_sees_a_prefix_while_the_cards_get_the_rest(db):
    """CARD_RESULT_LIMIT rows travel in the results event (folded behind
    "show more" client-side); the answer model is handed only the first
    ANSWER_RESULT_LIMIT, or its prompt to cover every route would produce
    twenty sentences. A prefix, never a re-sort: the prose and the cards
    above the fold must be the same routes in the same order."""
    import json

    from chat.orchestrator import ANSWER_RESULT_LIMIT, CARD_RESULT_LIMIT
    from tests.test_chat_orchestrator import build, collect, results_of

    rows = [
        {"id": f"cat_{i:03}", "name": f"Route {i}", "distance_m": 1000.0 * i}
        for i in range(CARD_RESULT_LIMIT)
    ]
    db.when("search_loops", rows)
    orchestrator, llm, _ = build(db, {"kind": "loop_search", "max_distance_m": 25000})
    events = await collect(orchestrator, user_id="u1", message="a loop")

    results = results_of(events)
    assert len(results["loops"]) == CARD_RESULT_LIMIT
    assert results["answered_count"] == ANSWER_RESULT_LIMIT

    _, results_json = llm.answer_calls[0]
    answered = json.loads(results_json)["loops"]
    assert len(answered) == ANSWER_RESULT_LIMIT
    assert [r["id"] for r in answered] == [r["id"] for r in rows[:ANSWER_RESULT_LIMIT]]
