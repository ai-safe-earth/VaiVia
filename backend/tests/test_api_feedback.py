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


def _body(
    vote: int,
    comment: str | None = None,
    expected: str | None = None,
    route_id: str = "",
) -> dict:
    return {
        "message_id": MESSAGE_ID,
        "conversation_id": CONVERSATION_ID,
        "vote": vote,
        "comment": comment,
        "expected": expected,
        "route_id": route_id,
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
    assert store._votes[(USER["x-user-id"], MESSAGE_ID, "")] == (
        -1,
        "named the wrong lake",
        "the one by Lecco",
    )


def test_a_route_vote_is_its_own_row(client):
    """Two routes offered by one answer are judged separately, and neither
    touches the answer-level vote — the key is (user, message, route)."""
    assert client.post("/feedback", json=_body(1), headers=USER).status_code == 200
    for route, vote in (("vv2-aaaa-fwd", -1), ("vv2-bbbb-fwd", 1)):
        posted = client.post(
            "/feedback",
            json=_body(vote, "too steep" if vote == -1 else None, route_id=route),
            headers=USER,
        )
        assert posted.status_code == 200

    votes = client.app.state.feedback._votes
    user = USER["x-user-id"]
    assert votes[(user, MESSAGE_ID, "")] == (1, None, None)
    assert votes[(user, MESSAGE_ID, "vv2-aaaa-fwd")] == (-1, "too steep", None)
    assert votes[(user, MESSAGE_ID, "vv2-bbbb-fwd")] == (1, None, None)


def test_a_route_id_is_bounded(client):
    """Free text from the client, so it is length-checked at the boundary."""
    body = _body(-1, route_id="x" * 129)
    assert client.post("/feedback", json=body, headers=USER).status_code == 422


def test_a_message_outside_this_users_conversations_is_an_honest_404(client):
    """The Postgres store returns False when the ownership join finds no row;
    the route must turn that into a 404, not a silent 200."""

    class RefusingStore:
        async def set(self, *args) -> bool:
            return False

    client.app.state.feedback = RefusingStore()
    assert client.post("/feedback", json=_body(1), headers=USER).status_code == 404
