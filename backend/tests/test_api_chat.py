"""SSE endpoint contract: identity, event framing, and error surfacing."""

from collections.abc import AsyncIterator

import pytest

from chat.intents import PlanEnvelope
from chat.llm import PlanResult, Usage
from chat.store import InMemoryStore


class StubLLM:
    def __init__(self, intent: dict) -> None:
        self.intent = intent

    async def extract_plan(self, message, history):
        return PlanResult(
            envelope=PlanEnvelope.model_validate({"subqueries": [self.intent]}),
            usage=Usage(10, 5),
        )

    async def stream_answer(self, message, results_json, history) -> AsyncIterator[str]:
        yield "Lago "
        yield "Loop."

    def last_answer_usage(self) -> Usage:
        return Usage(20, 10)


@pytest.fixture
def chat_client(client, db):
    client.app.state.llm = StubLLM(
        {"kind": "trail_search", "activity": "mtb", "max_distance_m": 20000}
    )
    client.app.state.store = InMemoryStore()
    db.when("search_trails", [])
    return client


def test_chat_requires_user_identity_from_gateway(chat_client):
    response = chat_client.post("/chat", json={"message": "find trails"})
    assert response.status_code == 401


def test_chat_streams_sse_events_in_order(chat_client):
    response = chat_client.post(
        "/chat", json={"message": "find trails"}, headers={"X-User-Id": "u1"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert events[:3] == ["conversation", "intent", "results"]
    assert events[-1] == "done"
    assert "Lago " in response.text


def test_empty_message_is_rejected(chat_client):
    response = chat_client.post(
        "/chat", json={"message": ""}, headers={"X-User-Id": "u1"}
    )
    assert response.status_code == 422


def test_overlong_message_is_rejected(chat_client):
    response = chat_client.post(
        "/chat", json={"message": "x" * 3000}, headers={"X-User-Id": "u1"}
    )
    assert response.status_code == 422


def test_quota_exhaustion_surfaces_as_an_sse_error_event(chat_client):
    from core.config import get_settings

    store: InMemoryStore = chat_client.app.state.store
    limit = get_settings().daily_token_quota_per_user
    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        store.record_usage("u1", None, "m", limit, 0)
    )

    response = chat_client.post(
        "/chat", json={"message": "find trails"}, headers={"X-User-Id": "u1"}
    )
    assert response.status_code == 200  # headers already sent; error is in-stream
    assert "event: error" in response.text
    assert "quota_exceeded" in response.text


def test_internal_failure_surfaces_as_an_sse_error_event(chat_client, db):
    db.fail_with = RuntimeError("neo4j unreachable")
    response = chat_client.post(
        "/chat", json={"message": "find trails"}, headers={"X-User-Id": "u1"}
    )
    assert "event: error" in response.text
    assert "internal_error" in response.text
