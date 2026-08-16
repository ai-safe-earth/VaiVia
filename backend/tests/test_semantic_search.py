"""Semantic search: the 503-until-populated rule, and the embedding plumbing."""

from core.embeddings import embedding_input, input_sha
from scripts.embed_trails import plan
from tests.conftest import TRAIL_ROW


def scored_row(**overrides):
    row = {**TRAIL_ROW, "score": 0.91}
    row.update(overrides)
    return row


def test_unpopulated_index_returns_503_not_empty(client, db):
    db.when("count_embedded_trails", [{"trails": 3, "embedded": 0}])
    response = client.post("/trails/semantic-search", json={"query": "lake views"})
    assert response.status_code == 503
    assert "unpopulated" in response.json()["detail"]
    # The refusal must happen before any embedding is spent.
    assert all(name != "semantic_search_trails" for name, _ in db.calls)


def test_search_embeds_the_query_and_returns_scored_trails(client, db, embedder):
    db.when("count_embedded_trails", [{"trails": 3, "embedded": 3}])
    db.when("semantic_search_trails", [scored_row()])

    response = client.post(
        "/trails/semantic-search", json={"query": "quiet forest ride", "limit": 3}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["trails"][0]["id"] == "tf_001"
    assert body["trails"][0]["score"] == 0.91

    # The user's text was embedded, and the vector — not the text — hit the db.
    assert embedder.calls == [["quiet forest ride"]]
    params = db.params_for("semantic_search_trails")
    assert params["limit"] == 3
    assert isinstance(params["embedding"], list) and len(params["embedding"]) == 1536


def test_query_shorter_than_three_chars_is_rejected(client):
    response = client.post("/trails/semantic-search", json={"query": "ok"})
    assert response.status_code == 422


def test_limit_is_capped(client):
    response = client.post(
        "/trails/semantic-search", json={"query": "anything", "limit": 100}
    )
    assert response.status_code == 422


# ── Embedding job planning (pure functions, no db) ────────────────────────────


def _trail(**overrides):
    base = {
        "id": "tf_001",
        "description": "A scenic loop.",
        "landscape_description": "Lakeside gravel.",
        "difficulty_notes": "Two rock gardens.",
        "embedded_sha": None,
    }
    base.update(overrides)
    return base


def test_embedding_input_is_the_ratified_concatenation():
    text = embedding_input("A loop.", "Gravel.", "Rocky.")
    assert text == "A loop.\nGravel.\nRocky."
    # Missing parts drop out rather than injecting empty lines.
    assert embedding_input("A loop.", None, "  ") == "A loop."


def test_plan_embeds_new_and_changed_skips_unchanged_and_empty():
    unchanged_text = embedding_input(
        "A scenic loop.", "Lakeside gravel.", "Two rock gardens."
    )
    trails = [
        _trail(id="new"),
        _trail(id="same", embedded_sha=input_sha(unchanged_text)),
        _trail(id="edited", embedded_sha="stale-sha"),
        _trail(
            id="blank",
            description=None,
            landscape_description=None,
            difficulty_notes=None,
        ),
    ]
    to_embed, unchanged, empty = plan(trails)
    assert [t["id"] for t in to_embed] == ["new", "edited"]
    assert unchanged == 1
    assert empty == 1
    # Each planned row carries the text to embed and the sha to store.
    assert all(t["text"] and t["sha"] == input_sha(t["text"]) for t in to_embed)
