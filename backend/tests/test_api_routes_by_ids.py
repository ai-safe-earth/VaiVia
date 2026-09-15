"""GET /routes/by-ids: the resume path's hydration.

The client stores only route ids per conversation turn (result_refs) and asks
for the cards again here — same contract as /routes/favorites, same template.
"""

ROW_A = {"id": "vv2-aaaa", "activity": "hiking", "distance_m": 11000.0}
ROW_B = {"id": "vv2-bbbb", "activity": "hiking", "distance_m": 8000.0}


def test_rows_come_back_in_the_order_asked(client, db):
    db.when("routes_by_ids", [ROW_B, ROW_A])  # graph order is not ref order
    body = client.get("/routes/by-ids", params={"ids": "vv2-aaaa,vv2-bbbb"}).json()
    assert [r["id"] for r in body["routes"]] == ["vv2-aaaa", "vv2-bbbb"]
    assert body["missing"] == []
    assert db.params_for("routes_by_ids")["route_ids"] == ["vv2-aaaa", "vv2-bbbb"]


def test_a_departed_route_is_reported_missing_never_dropped(client, db):
    db.when("routes_by_ids", [ROW_A])
    body = client.get("/routes/by-ids", params={"ids": "vv2-aaaa,vv2-gone"}).json()
    assert [r["id"] for r in body["routes"]] == ["vv2-aaaa"]
    assert body["missing"] == ["vv2-gone"]


def test_empty_and_whitespace_ids_ask_the_graph_nothing(client, db):
    body = client.get("/routes/by-ids", params={"ids": " , ,"}).json()
    assert body == {"routes": [], "missing": []}
    assert all(name != "routes_by_ids" for name, _ in db.calls)


def test_the_id_list_is_capped(client, db):
    db.when("routes_by_ids", [])
    ids = ",".join(f"vv2-{i:04d}" for i in range(150))
    client.get("/routes/by-ids", params={"ids": ids})
    assert len(db.params_for("routes_by_ids")["route_ids"]) == 100
