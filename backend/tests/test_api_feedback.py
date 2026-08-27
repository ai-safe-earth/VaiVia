"""Feedback: the upsert, the identity requirement, and the honest 404.

The store under test is InMemoryFeedback (conftest wires it so the suite
never writes real rows); PostgresFeedback runs one INSERT..SELECT whose
ownership check lives in the SQL, verified against the live stack rather
than here — the in-memory double deliberately has no messages table to
check against.
"""

MESSAGE_ID = "5f0c9d1e-0000-4000-8000-0000000000aa"
CONVERSATION_ID = "5f0c9d1e-0000-4000-8000-0000000000bb"

USER = {"x-user-id": "9b2f9d1e-0000-4000-8000-000000000001"}


def _body(vote: int, comment: str | None = None, expected: str | None = None) -> dict:
    return {
        "message_id": MESSAGE_ID,
        "conversation_id": CONVERSATION_ID,
        "vote": vote,
        "comment": comment,
        "expected": expected,
    }


def test_feedback_requires_the_gateway_identity(client):
    assert client.post("/feedback", json=_body(1)).status_code == 401


def test_vote_must_be_a_thumb(client):
    """0 (and anything else outside {-1, 1}) is refused at validation."""
    assert client.post("/feedback", json=_body(0), headers=USER).status_code == 422


def test_a_revote_flips_and_a_comment_updates(client):
    up = client.post("/feedback", json=_body(1), headers=USER)
    assert up.status_code == 200
    assert up.json() == {"message_id": MESSAGE_ID, "vote": 1}

    down = client.post(
        "/feedback",
        json=_body(-1, "named the wrong lake", "the one by Lecco"),
        headers=USER,
    )
    assert down.status_code == 200
    assert down.json() == {"message_id": MESSAGE_ID, "vote": -1}

    store = client.app.state.feedback
    assert store._votes[(USER["x-user-id"], MESSAGE_ID)] == (
        -1,
        "named the wrong lake",
        "the one by Lecco",
    )


def test_a_message_outside_this_users_conversations_is_an_honest_404(client):
    """The Postgres store returns False when the ownership join finds no row;
    the route must turn that into a 404, not a silent 200."""

    class RefusingStore:
        async def set(self, *args) -> bool:
            return False

    client.app.state.feedback = RefusingStore()
    assert client.post("/feedback", json=_body(1), headers=USER).status_code == 404
