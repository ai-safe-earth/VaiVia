"""Favorites: the toggle, the hydrated list, and the honesty around ids.

The store under test is InMemoryFavorites (conftest wires it so the suite
never writes real rows); PostgresFavorites runs the same statements with the
ownership in the SQL, verified against the live stack rather than here.
"""

ROUTE_ID = "vv2-abc123def4567890-fwd"

ROW = {
    "id": ROUTE_ID,
    "activity": "hiking",
    "kind": "generated",
    "shape": "loop",
    "name": "To Corno dell'Arco",
    "distance_m": 11000.0,
}

USER = {"x-user-id": "9b2f9d1e-0000-4000-8000-000000000001"}


def test_favoriting_requires_the_gateway_identity(client, db):
    """No x-user-id means the request skipped the gateway's authenticate step;
    a user-scoped endpoint refuses rather than guessing."""
    assert (
        client.post(f"/routes/{ROUTE_ID}/favorite", json={"on": True}).status_code
        == 401
    )
    assert client.get("/routes/favorites").status_code == 401


def test_favoriting_an_unknown_route_is_an_honest_404(client, db):
    db.when("route_exists", [])
    response = client.post("/routes/nope/favorite", json={"on": True}, headers=USER)
    assert response.status_code == 404


def test_toggle_is_idempotent_and_the_list_hydrates(client, db):
    db.when("route_exists", [{"id": ROUTE_ID}])
    db.when("routes_by_ids", [ROW])

    for _ in range(2):  # favoriting twice is one favorite, not an error
        response = client.post(
            f"/routes/{ROUTE_ID}/favorite", json={"on": True}, headers=USER
        )
        assert response.status_code == 200
        assert response.json() == {"route_id": ROUTE_ID, "on": True}

    body = client.get("/routes/favorites", headers=USER).json()
    assert [r["id"] for r in body["routes"]] == [ROUTE_ID]
    assert body["routes"][0]["name"] == "To Corno dell'Arco"
    assert body["missing"] == []
    # The graph was asked with exactly the saved ids.
    assert db.params_for("routes_by_ids")["route_ids"] == [ROUTE_ID]


def test_unfavoriting_works_even_when_the_route_is_gone(client, db):
    """A route that left the catalogue must still be removable — the 404
    check guards saving, never unsaving."""
    db.when("route_exists", [{"id": ROUTE_ID}])
    client.post(f"/routes/{ROUTE_ID}/favorite", json={"on": True}, headers=USER)

    db.when("route_exists", [])  # the catalogue moved on
    response = client.post(
        f"/routes/{ROUTE_ID}/favorite", json={"on": False}, headers=USER
    )
    assert response.status_code == 200

    db.when("routes_by_ids", [])
    assert client.get("/routes/favorites", headers=USER).json() == {
        "routes": [],
        "missing": [],
    }


def test_a_vanished_favorite_is_reported_missing_not_dropped(client, db):
    """:Route nodes are replaced wholesale per export; a favorite whose id no
    longer hydrates is named in `missing` so the client can say so."""
    db.when("route_exists", [{"id": ROUTE_ID}])
    client.post(f"/routes/{ROUTE_ID}/favorite", json={"on": True}, headers=USER)
    client.post("/routes/gone-route/favorite", json={"on": True}, headers=USER)

    db.when("routes_by_ids", [ROW])  # only one of the two hydrates
    body = client.get("/routes/favorites", headers=USER).json()
    assert [r["id"] for r in body["routes"]] == [ROUTE_ID]
    assert body["missing"] == ["gone-route"]


def test_favorites_are_per_user(client, db):
    db.when("route_exists", [{"id": ROUTE_ID}])
    db.when("routes_by_ids", [ROW])
    client.post(f"/routes/{ROUTE_ID}/favorite", json={"on": True}, headers=USER)

    other = {"x-user-id": "9b2f9d1e-0000-4000-8000-000000000002"}
    assert client.get("/routes/favorites", headers=other).json() == {
        "routes": [],
        "missing": [],
    }


def test_the_list_is_newest_first(client, db):
    first, second = ROUTE_ID, "vv2-fedcba9876543210"
    db.when("route_exists", [{"id": first}])
    client.post(f"/routes/{first}/favorite", json={"on": True}, headers=USER)
    db.when("route_exists", [{"id": second}])
    client.post(f"/routes/{second}/favorite", json={"on": True}, headers=USER)

    db.when("routes_by_ids", [ROW, {**ROW, "id": second, "name": "Second"}])
    body = client.get("/routes/favorites", headers=USER).json()
    assert [r["id"] for r in body["routes"]] == [second, first]


# ── keeping a DRAWN route persists it (Phase 12 R4) ──────────────────────────


def _drawn_document(route_id: str = "vv2-feedfacecafebeef") -> dict:
    from chat.pack_state import load_planner
    from chat.planner import plan_outing
    from tests.test_planner import ANCHOR, FIXTURE, constraints

    state = load_planner(str(FIXTURE))
    result = plan_outing(state, constraints(shape="loop", max_hours=2), ANCHOR)
    return result.routes[0]["document"]


def test_favoriting_a_drawn_route_writes_document_and_node(
    client, db, tmp_path, monkeypatch
):
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "route_documents_dir", str(tmp_path))
    document = _drawn_document()
    rid = document["id"]
    client.app.state.outing_docs = {"c1": [document]}
    db.when("route_exists", [])  # not in the catalogue: it was drawn

    response = client.post(
        f"/routes/{rid}/favorite", json={"on": True}, headers={"X-User-Id": "u1"}
    )
    assert response.status_code == 200
    # the document landed where the geometry endpoints serve from
    assert (tmp_path / f"{rid}.json").exists()
    # and the graph got its writes: node, places, passes, start, starts_at
    assert len(db.writes) >= 3
    assert any("MERGE (r:Route" in q for q, _p in db.writes)
    node_params = next(p for q, p in db.writes if "MERGE (r:Route" in q)
    assert node_params["route_id"] == rid
    assert node_params["props"]["kind"] == "generated"


def test_favoriting_an_unknown_drawn_id_is_a_404(client, db):
    client.app.state.outing_docs = {}
    db.when("route_exists", [])
    response = client.post(
        "/routes/vv2-0000000000000000/favorite",
        json={"on": True},
        headers={"X-User-Id": "u1"},
    )
    assert response.status_code == 404


def test_favoriting_a_drawn_route_without_a_store_is_a_503(client, db, monkeypatch):
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "route_documents_dir", None)
    document = _drawn_document()
    client.app.state.outing_docs = {"c1": [document]}
    db.when("route_exists", [])
    response = client.post(
        f"/routes/{document['id']}/favorite",
        json={"on": True},
        headers={"X-User-Id": "u1"},
    )
    assert response.status_code == 503
