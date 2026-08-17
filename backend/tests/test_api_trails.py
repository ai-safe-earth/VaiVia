"""Trail endpoint contract tests (fake graph client — no Neo4j needed)."""

from tests.conftest import TRAIL_ROW


def test_search_returns_trails_and_count(client, db):
    db.when("search_trails", [TRAIL_ROW])
    response = client.post("/trails/search", json={"activity": "mtb"})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["trails"][0]["name"] == "Lago Loop"
    assert body["trails"][0]["duration_mtb_min"] == 88


def test_search_passes_every_filter_as_a_parameter(client, db):
    db.when("search_trails", [])
    client.post(
        "/trails/search",
        json={
            "activity": "hike",
            "max_difficulty_level": 2,
            "max_distance_m": 15000,
            "poi_types": ["lake", "hut"],
            "surface_exclusions": ["asphalt"],
            "season": "winter",
            "exclude_hazards": ["ice"],
            "region": "Lecco",
            "limit": 5,
        },
    )
    params = db.params_for("search_trails")
    assert params["activity"] == "hike"
    assert params["max_difficulty_level"] == 2
    assert params["poi_types"] == ["lake", "hut"]
    assert params["exclude_hazards"] == ["ice"]
    assert params["region"] == "Lecco"
    assert params["limit"] == 5
    # Unset filters must arrive as explicit nulls (the template tests IS NULL).
    assert params["min_distance_m"] is None
    assert params["max_elevation_gain_m"] is None


def test_empty_search_body_is_valid(client, db):
    db.when("search_trails", [])
    assert client.post("/trails/search", json={}).status_code == 200


def test_search_rejects_out_of_range_difficulty(client, db):
    response = client.post("/trails/search", json={"max_difficulty_level": 9})
    assert response.status_code == 422


def test_search_rejects_unknown_poi_type(client, db):
    response = client.post("/trails/search", json={"poi_types": ["casino"]})
    assert response.status_code == 422


def test_search_rejects_limit_above_cap(client, db):
    assert client.post("/trails/search", json={"limit": 500}).status_code == 422


def test_get_trail_detail(client, db):
    db.when("trail_by_id", [{**TRAIL_ROW, "description": "A scenic loop."}])
    response = client.get("/trails/tf_001")
    assert response.status_code == 200
    assert response.json()["description"] == "A scenic loop."


def test_get_trail_404_when_missing(client, db):
    db.when("trail_by_id", [])
    response = client.get("/trails/nope")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_detail_drops_optional_match_poi_placeholder(client, db):
    db.when("trail_by_id", [{**TRAIL_ROW, "pois": [{"name": None, "type": None}]}])
    assert client.get("/trails/tf_001").json()["pois"] == []


def test_geojson_builds_multilinestring_in_seq_order(client, db):
    db.when(
        "trail_geometry",
        [
            {
                "seq": 0,
                "match_confidence": 0.9,
                "osm_way_id": "1#0",
                "surface": "gravel",
                "length_m": 100.0,
                "coordinates": [[9.38, 45.91], [9.385, 45.913]],
            },
            {
                "seq": 1,
                "match_confidence": 0.7,
                "osm_way_id": "1#1",
                "surface": "dirt",
                "length_m": 150.0,
                "coordinates": [[9.385, 45.913], [9.39, 45.917]],
            },
        ],
    )
    body = client.get("/trails/tf_001/geojson").json()
    assert body["type"] == "Feature"
    assert body["geometry"]["type"] == "MultiLineString"
    assert len(body["geometry"]["coordinates"]) == 2
    # GeoJSON is [lon, lat] — longitude first
    assert body["geometry"]["coordinates"][0][0] == [9.38, 45.91]
    assert body["properties"]["total_length_m"] == 250.0
    assert body["properties"]["min_match_confidence"] == 0.7


def test_geojson_404_when_no_matched_segments(client, db):
    db.when("trail_geometry", [])
    assert client.get("/trails/tf_001/geojson").status_code == 404
