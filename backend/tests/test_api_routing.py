"""Routing endpoint: POI resolution, snapping, and failure modes."""

STATION = {
    "osm_id": "1",
    "name": "Station A",
    "type": "station",
    "lat": 45.9,
    "lon": 9.4,
}
HUT = {"osm_id": "2", "name": "Hut B", "type": "hut", "lat": 45.95, "lon": 9.45}

ROUTE_ROW = {
    "total_m": 8200.0,
    "gain_m": 350.0,
    "coordinates": [[9.4, 45.9], [9.42, 45.92], [9.45, 45.95]],
    "osm_way_ids": ["1#0", "2#0"],
    "surfaces": ["gravel", "dirt"],
}


def _happy_path(db):
    db.when("poi_by_name", [STATION])
    db.when("nearest_intersection", [{"osm_node_id": "n1", "distance_m": 12.0}])
    db.when("route_between_intersections", [ROUTE_ROW])


def test_route_returns_linestring_and_effort(client, db):
    _happy_path(db)
    response = client.post("/routes", json={"start": "Station A", "end": "Hut B"})
    assert response.status_code == 200
    body = response.json()
    assert body["total_distance_m"] == 8200.0
    assert body["elevation_gain_m"] == 350.0
    assert body["geometry"]["type"] == "LineString"
    assert body["surfaces"] == ["gravel", "dirt"]


def test_route_404_when_poi_unknown(client, db):
    db.when("poi_by_name", [])
    response = client.post("/routes", json={"start": "Atlantis", "end": "Hut B"})
    assert response.status_code == 404
    assert "Atlantis" in response.json()["detail"]


def test_route_422_when_poi_off_network(client, db):
    db.when("poi_by_name", [STATION])
    db.when("nearest_intersection", [])
    response = client.post("/routes", json={"start": "Station A", "end": "Hut B"})
    assert response.status_code == 422
    assert "trail network" in response.json()["detail"]


def test_route_404_when_no_path_within_limit(client, db):
    db.when("poi_by_name", [STATION])
    db.when("nearest_intersection", [{"osm_node_id": "n1", "distance_m": 12.0}])
    db.when("route_between_intersections", [])
    response = client.post(
        "/routes", json={"start": "Station A", "end": "Hut B", "max_distance_m": 5000}
    )
    assert response.status_code == 404
    assert "no route" in response.json()["detail"]


def test_requested_max_distance_is_capped_by_settings(client, db):
    _happy_path(db)
    client.post(
        "/routes",
        json={"start": "Station A", "end": "Hut B", "max_distance_m": 10_000_000},
    )
    params = db.params_for("route_between_intersections")
    assert params["max_distance_m"] == 100_000.0  # settings.max_route_distance_m


def test_snap_uses_configured_radius(client, db):
    _happy_path(db)
    client.post("/routes", json={"start": "Station A", "end": "Hut B"})
    assert db.params_for("nearest_intersection")["radius_m"] == 500.0


def test_route_rejects_non_positive_max_distance(client, db):
    response = client.post(
        "/routes", json={"start": "A", "end": "B", "max_distance_m": 0}
    )
    assert response.status_code == 422
