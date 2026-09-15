"""The on-demand path end to end, offline: an outing intent from the stub
LLM is drawn over the fixture pack, a chip redraws with no model call, and
a coverage clarify poisons the turn before anything runs."""

import json

import pytest

from chat.orchestrator import ChatOrchestrator
from chat.store import InMemoryStore
from tests.test_chat_orchestrator import StubLLM, collect, results_of
from tests.test_planner import FIXTURE


@pytest.fixture(scope="module")
def planner():
    from chat.pack_state import load_planner

    return load_planner(str(FIXTURE))


OUTING = {
    "kind": "outing",
    "activity": "hike",
    "shape": "loop",
    "max_hours": 2,
    "start": {"mode": "here"},
}

NEAR = (45.855, 9.40)


def build(db, plan, planner, store=None, docs=None):
    llm = StubLLM([plan] if isinstance(plan, dict) else plan, "I drew routes.")
    store = store or InMemoryStore()
    orchestrator = ChatOrchestrator(
        db=db,
        llm=llm,
        store=store,
        planner=planner,
        outing_docs=docs if docs is not None else {},
    )
    return orchestrator, llm, store


async def test_an_outing_is_drawn_not_searched(db, planner):
    orchestrator, llm, _ = build(db, OUTING, planner)
    events = await collect(
        orchestrator, user_id="u1", message="a two hour loop from here", near=NEAR
    )
    results = results_of(events)
    assert results["drawn"] is True
    assert 1 <= len(results["loops"]) <= 3
    card = results["loops"][0]
    assert card["id"].startswith("vv2-")
    assert card["ordinal"] == 1
    assert card["geometry"]["type"] == "LineString"
    assert results["assumptions"]
    assert "counts" in results
    # the catalogue was never asked: the pack answered
    assert not [c for c in db.calls if c[0] == "search_loops"]
    # the answer model saw facts, never geometry
    _message, results_json = llm.answer_calls[0]
    assert "geometry" not in json.loads(results_json)["loops"][0]


async def test_a_chip_redraws_with_no_model_call(db, planner):
    docs: dict = {}
    store = InMemoryStore()
    orchestrator, llm, _ = build(db, OUTING, planner, store=store, docs=docs)
    first = await collect(
        orchestrator, user_id="u1", message="a two hour loop from here", near=NEAR
    )
    conversation_id = first[0].data["conversation_id"]

    orchestrator2, llm2, _ = build(db, OUTING, planner, store=store, docs=docs)
    events = await collect(
        orchestrator2,
        user_id="u1",
        message="shorter",
        conversation_id=conversation_id,
        near=NEAR,
        chip={"max_hours": 1.0},
    )
    results = results_of(events)
    assert llm2.extract_calls == []  # no plan model
    assert llm2.answer_calls == []  # no answer model
    assert results.get("drawn") is True
    # ordinals continue across the conversation
    ordinals = [c["ordinal"] for c in results["loops"]]
    assert min(ordinals) == len(first and docs[conversation_id]) - len(ordinals) + 1
    # zero tokens billed for the chip turn
    done = [e for e in events if e.event == "done"][0]
    assert done.data["usage"] == {"input_tokens": 0, "output_tokens": 0}


async def test_a_chip_with_no_standing_outing_clarifies(db, planner):
    orchestrator, llm, _ = build(db, OUTING, planner)
    events = await collect(
        orchestrator,
        user_id="u1",
        message="shorter",
        chip={"max_hours": 1.0},
    )
    results = results_of(events)
    assert "clarification" in results
    assert llm.extract_calls == []


async def test_uncovered_area_clarifies_before_anything_runs(db, planner):
    outing = dict(OUTING, area="Tuscany")
    orchestrator, _llm, store = build(db, outing, planner)
    events = await collect(
        orchestrator, user_id="u1", message="a loop in Tuscany", near=NEAR
    )
    results = results_of(events)
    assert "Tuscany" in results["clarification"]
    assert db.calls == []  # nothing ran beside the refusal
    # the answer streamed is the clarify question, no model call
    tokens = [e.data["delta"] for e in events if e.event == "token"]
    assert "Tuscany" in "".join(tokens)


async def test_without_a_pack_the_catalogue_still_answers(db):
    db.when("search_loops", [])
    orchestrator, _llm, _ = build(db, OUTING, planner=None)
    events = await collect(
        orchestrator, user_id="u1", message="a two hour loop from here", near=NEAR
    )
    results = results_of(events)
    assert "drawn" not in results
    assert [c for c in db.calls if c[0] == "search_loops"]
