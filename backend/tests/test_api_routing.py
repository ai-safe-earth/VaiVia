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


# ── GDS Dijkstra path (preferred when a projection is available) ──────────────

# total_cost is comfort-penalised (core/comfort.py) and deliberately unlike the
# real length: the endpoint must report the sum of distance_m over the edges,
# never this. 7800 here would be the wrong answer.
GDS_ROW = {
    "total_cost": 7800.0,
    "coordinates": [[9.4, 45.9], [9.43, 45.93], [9.45, 45.95]],
    "node_ids": ["n1", "n7", "n2"],
}
EDGE_DETAILS = [
    {
        "i": 0,
        "osm_way_id": "1#0",
        "surface": "gravel",
        "highway_type": "path",
        "distance_m": 2000.0,
        "gain_m": 120.0,
    },
    {
        "i": 1,
        "osm_way_id": "2#0",
        "surface": "dirt",
        "highway_type": "track",
        "distance_m": 1000.0,
        "gain_m": 80.0,
    },
]


def _gds_available(db):
    db.when("poi_by_name", [STATION])
    db.when("nearest_intersection", [{"osm_node_id": "n1", "distance_m": 12.0}])
    # Where the endpoints ARE, which is what decides the bbox to project. The
    # fake resolves every POI to n1, so one row answers both lookups.
    db.when(
        "intersection_locations",
        [{"osm_node_id": "n1", "lat": 45.86, "lon": 9.39}],
    )
    db.when("graph_project_routing", [{"graph_name": "g", "nodes": 100, "rels": 200}])
    db.when("route_gds_dijkstra", [GDS_ROW])
    db.when("route_edge_details", EDGE_DETAILS)


def test_gds_route_is_preferred_and_enriched(client, db):
    _gds_available(db)
    db.when("route_between_intersections", [ROUTE_ROW])  # would give 8200 m
    response = client.post("/routes", json={"start": "Station A", "end": "Hut B"})
    assert response.status_code == 200
    body = response.json()
    # Summed from distance_m (2000 + 1000), NOT Dijkstra's 7800 total_cost:
    # the weight is comfort-penalised and would over-report the length.
    assert body["total_distance_m"] == 3000.0
    assert body["elevation_gain_m"] == 200.0  # summed from edge details
    assert body["surfaces"] == ["gravel", "dirt"]
    # shortestPath was never needed
    assert all(name != "route_between_intersections" for name, _ in db.calls)


def test_reported_distance_never_comes_from_the_comfort_weight(client, db):
    """Regression guard: cost_m scales distance by how unpleasant a way is, so
    quoting totalCost would tell a user a 3 km walk is 7.8 km."""
    _gds_available(db)
    body = client.post("/routes", json={"start": "Station A", "end": "Hut B"}).json()
    assert body["total_distance_m"] != GDS_ROW["total_cost"]
    assert body["total_distance_m"] == sum(d["distance_m"] for d in EDGE_DETAILS)


def test_gds_projection_is_always_dropped(client, db):
    _gds_available(db)
    client.post("/routes", json={"start": "Station A", "end": "Hut B"})
    called = [name for name, _ in db.calls]
    assert "graph_drop_routing" in called
    drop_params = db.params_for("graph_drop_routing")
    project_params = db.params_for("graph_project_routing")
    assert drop_params["graph_name"] == project_params["graph_name"]


def test_empty_projection_falls_back_to_shortest_path(client, db):
    _happy_path(db)  # graph_project_routing not queued -> FakeDb returns []
    response = client.post("/routes", json={"start": "Station A", "end": "Hut B"})
    assert response.status_code == 200
    assert response.json()["total_distance_m"] == 8200.0


def test_gds_route_over_cap_falls_back_then_404s(client, db):
    """Over the cap the comfortable route is abandoned for shortestPath, which
    minimises real distance and may fit where the pleasant route did not. Here
    it also finds nothing, so the request 404s."""
    _gds_available(db)
    db.when(
        "route_edge_details",
        [dict(EDGE_DETAILS[0], distance_m=999_999.0)],
    )
    db.when("route_between_intersections", [])  # min distance > cap -> none here either
    response = client.post(
        "/routes", json={"start": "Station A", "end": "Hut B", "max_distance_m": 5000}
    )
    assert response.status_code == 404


# ── catalogue route geometry (GET /routes/{id}/geojson) ──────────────────────

ROUTE_ID = "generated-abc123def4567890"


def _documents_dir(tmp_path, monkeypatch, document: dict | None):
    """Point the app at a temp document store, on the CACHED settings object —
    clearing the cache would re-read the real .env and wake the gateway-secret
    middleware under the test client."""
    import json as _json

    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "route_documents_dir", str(tmp_path))
    if document is not None:
        (tmp_path / f"{ROUTE_ID}.json").write_text(
            _json.dumps(document), encoding="utf-8"
        )
    return tmp_path


DOCUMENT = {
    "geometry": {"type": "LineString", "coordinates": [[9.4, 45.9], [9.41, 45.91]]},
    "provenance": {"sources": [{"attribution": "© OpenStreetMap contributors"}]},
}


def test_route_geojson_serves_the_document(client, db, tmp_path, monkeypatch):
    """The graph answers only "does this route exist"; the geometry comes from
    the route DOCUMENT — the canonical artefact, served by the API
    (docs/route-document.md). Attribution travels with the feature because the
    document is the ODbL Produced Work."""
    _documents_dir(tmp_path, monkeypatch, DOCUMENT)
    db.when("route_exists", [{"id": ROUTE_ID}])
    response = client.get(f"/routes/{ROUTE_ID}/geojson")
    assert response.status_code == 200
    body = response.json()
    assert body["geometry"]["type"] == "LineString"
    assert body["geometry"]["coordinates"] == [[9.4, 45.9], [9.41, 45.91]]
    assert body["properties"]["route_id"] == ROUTE_ID
    assert "OpenStreetMap" in body["properties"]["attribution"]


def test_route_geojson_404s_before_touching_the_filesystem(
    client, db, tmp_path, monkeypatch
):
    _documents_dir(tmp_path, monkeypatch, None)
    db.when("route_exists", [])
    assert client.get("/routes/nope/geojson").status_code == 404


def test_route_geojson_503s_when_documents_are_not_mounted(client, db, monkeypatch):
    """A catalogue route whose document store is unconfigured is a degradation,
    never an empty shape — the semantic-search 503 rule."""
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "route_documents_dir", None)
    db.when("route_exists", [{"id": ROUTE_ID}])
    assert client.get(f"/routes/{ROUTE_ID}/geojson").status_code == 503


def test_route_geojson_503s_when_one_document_is_missing(
    client, db, tmp_path, monkeypatch
):
    _documents_dir(tmp_path, monkeypatch, None)  # dir exists, file does not
    db.when("route_exists", [{"id": ROUTE_ID}])
    assert client.get(f"/routes/{ROUTE_ID}/geojson").status_code == 503


def test_route_geojson_serves_the_longest_piece_of_a_multipart_route(
    client, db, tmp_path, monkeypatch
):
    """A route the network holds in pieces is a MultiLineString in its document.
    The map endpoint shows the longest piece and says so, never a straight line
    across the gaps (metadata-rules.md: a broken route is never drawn whole)."""
    document = {
        "geometry": {
            "type": "MultiLineString",
            "coordinates": [
                [[9.4, 45.9], [9.41, 45.91], [9.42, 45.92]],
                [[9.5, 45.95], [9.51, 45.96]],
            ],
        },
        "provenance": {"sources": [{"attribution": "©"}]},
    }
    _documents_dir(tmp_path, monkeypatch, document)
    db.when("route_exists", [{"id": ROUTE_ID}])
    body = client.get(f"/routes/{ROUTE_ID}/geojson").json()
    assert len(body["geometry"]["coordinates"]) == 3
    assert "pieces" in body["properties"]["note"]


# ── /routes/{id}/detail: the expandable card's payload ──────────────────────

DETAIL_DOCUMENT = {
    "shape": "circular",
    "geometry": {"type": "LineString", "coordinates": [[9.4, 45.9], [9.41, 45.91]]},
    "measures": {
        "distance_m": 11000.0,
        "ascent_m": 1050.0,
        "descent_m": 1050.0,
        "lowest_m": 400.0,
        "highest_m": 1450.0,
    },
    "profile": {
        "distance_m": [0.0, 5500.0, 11010.0],
        "elevation_m": [400.0, 1450.0, 400.0],
    },
    "continuity": {"pieces": 1, "continuous": True},
    "surface": {"distribution": {"unpaved": 0.8, "paved": 0.2}, "dominant": "unpaved"},
    "places": [{"id": "n1", "kind": "peak", "name": "Corno", "offset_m": 12.0}],
    "provenance": {"sources": [{"attribution": "© OpenStreetMap contributors"}]},
}


def test_route_detail_serves_the_documents_knowledge(client, db, tmp_path, monkeypatch):
    """Geometry stays on /geojson; /detail carries the rest — the profile the
    elevation panel exists to draw, measures, continuity, surface, places —
    with attribution, because the document is the ODbL Produced Work."""
    _documents_dir(tmp_path, monkeypatch, DETAIL_DOCUMENT)
    db.when("route_exists", [{"id": ROUTE_ID}])
    body = client.get(f"/routes/{ROUTE_ID}/detail").json()
    assert body["shape"] == "circular"
    assert body["profile"]["elevation_m"] == [400.0, 1450.0, 400.0]
    assert body["profile_quality"] == "ok"  # 11010 vs 11000 is within 1%
    assert body["measures"]["highest_m"] == 1450.0
    assert body["surface"]["dominant"] == "unpaved"
    assert body["places"][0]["name"] == "Corno"
    assert "OpenStreetMap" in body["attribution"]


def test_route_detail_flags_a_concatenated_profile_as_approximate(
    client, db, tmp_path, monkeypatch
):
    """A multi-piece OSM profile is a concatenation across gaps (16 of 752
    measured >1% off the route length on 2026-08-21). It is served — the data
    is real heights — but marked, so the client draws it with a caveat, never
    as a clean along-route measure."""
    document = dict(DETAIL_DOCUMENT)
    document["profile"] = {
        "distance_m": [0.0, 6160.0],
        "elevation_m": [400.0, 900.0],
    }
    document["measures"] = {**DETAIL_DOCUMENT["measures"], "distance_m": 4489.8}
    document["continuity"] = {"pieces": 3, "continuous": False}
    _documents_dir(tmp_path, monkeypatch, document)
    db.when("route_exists", [{"id": ROUTE_ID}])
    body = client.get(f"/routes/{ROUTE_ID}/detail").json()
    assert body["profile_quality"] == "approximate"


def test_route_detail_with_no_profile_says_so(client, db, tmp_path, monkeypatch):
    """Absent is not zero: a route whose DEM tile is missing carries
    profile: null, and quality is absent rather than invented."""
    document = {**DETAIL_DOCUMENT, "profile": None}
    _documents_dir(tmp_path, monkeypatch, document)
    db.when("route_exists", [{"id": ROUTE_ID}])
    body = client.get(f"/routes/{ROUTE_ID}/detail").json()
    assert body["profile"] is None
    assert body["profile_quality"] is None


def test_a_profile_with_nothing_to_check_it_against_is_approximate(
    client, db, tmp_path, monkeypatch
):
    """No measured length means no basis for calling the profile accurate.

    The guard read `off_by = ... if route_m else 0.0`, and a zero disagreement
    is a PERFECT one: a clipped fragment measuring 0 m shipped 'ok' and the
    client drew the chart as a trusted along-route measure. No basis is a
    caveat, not a clean bill of health.
    """
    document = dict(DETAIL_DOCUMENT)
    document["measures"] = {**DETAIL_DOCUMENT["measures"], "distance_m": 0.0}
    _documents_dir(tmp_path, monkeypatch, document)
    db.when("route_exists", [{"id": ROUTE_ID}])
    body = client.get(f"/routes/{ROUTE_ID}/detail").json()
    assert body["profile_quality"] == "approximate"


def test_a_re_export_is_read_again_not_served_from_the_cache(
    client, db, tmp_path, monkeypatch
):
    """The parse is cached, which is only safe if a rebuild invalidates it.

    Route ids are geometry-derived and survive a rebuild on purpose, so the
    path alone would name the OLD document for ever. The key carries mtime and
    size, so a re-emitted file simply does not match the entry.
    """
    import json as _json

    _documents_dir(tmp_path, monkeypatch, DETAIL_DOCUMENT)
    db.when("route_exists", [{"id": ROUTE_ID}])
    first = client.get(f"/routes/{ROUTE_ID}/detail").json()
    assert first["measures"]["highest_m"] == 1450.0

    rebuilt = dict(DETAIL_DOCUMENT)
    rebuilt["measures"] = {**DETAIL_DOCUMENT["measures"], "highest_m": 1600.0}
    path = tmp_path / f"{ROUTE_ID}.json"
    path.write_text(_json.dumps(rebuilt), encoding="utf-8")
    # Windows and Linux both keep sub-second mtimes, but the SIZE is part of
    # the key too and this document differs in both.
    assert (
        client.get(f"/routes/{ROUTE_ID}/detail").json()["measures"]["highest_m"]
        == 1600.0
    )


def test_route_detail_shares_the_honesty_ladder(client, db, tmp_path, monkeypatch):
    from core.config import get_settings

    db.when("route_exists", [])
    assert client.get("/routes/nope/detail").status_code == 404

    db.when("route_exists", [{"id": ROUTE_ID}])
    monkeypatch.setattr(get_settings(), "route_documents_dir", None)
    assert client.get(f"/routes/{ROUTE_ID}/detail").status_code == 503


def test_the_projection_follows_the_query_not_the_configured_bbox(client, db):
    """A route in Bergamo must be projected around Bergamo.

    The bbox was settings.default_bbox — one Lecco-shaped box that held 31,514
    of the graph's 84,137 intersections once Bergamo was ingested. Endpoints
    outside it are absent from the in-memory graph, so Dijkstra raises, the
    fallback catches it, and 63% of the network quietly got hop-count routing
    while the comfort weighting never ran.
    """
    _gds_available(db)
    db.when(
        "intersection_locations",
        [{"osm_node_id": "n1", "lat": 45.7319, "lon": 9.7404}],  # Bergamo
    )
    response = client.post("/routes", json={"start": "Station A", "end": "Hut B"})
    assert response.status_code == 200

    params = db.params_for("graph_project_routing")
    assert params["min_lat"] < 45.7319 < params["max_lat"]
    assert params["min_lon"] < 9.7404 < params["max_lon"]
    # ...and nowhere near the configured default, which is what used to run.
    from core.config import get_settings

    default_min_lat, default_min_lon, _, _ = get_settings().bbox
    assert params["min_lat"] != default_min_lat
    assert params["min_lon"] != default_min_lon
    # GDS answered, so the hop-count fallback was never needed.
    assert all(name != "route_between_intersections" for name, _ in db.calls)


def test_the_projected_box_covers_the_distance_the_caller_allowed(client, db):
    """The margin is the caller's cap, so a route inside the cap cannot leave
    the box: no reachable route is projected away."""
    _gds_available(db)
    db.when(
        "intersection_locations",
        [{"osm_node_id": "n1", "lat": 45.86, "lon": 9.39}],
    )
    client.post(
        "/routes",
        json={"start": "Station A", "end": "Hut B", "max_distance_m": 20000},
    )
    params = db.params_for("graph_project_routing")
    # 20 km north-south is about 0.18 degrees of latitude.
    assert params["max_lat"] - 45.86 > 0.17
    assert 45.86 - params["min_lat"] > 0.17
    # Longitude degrees are shorter this far north, so its margin is WIDER.
    assert params["max_lon"] - 9.39 > params["max_lat"] - 45.86


def test_an_endpoint_with_no_location_falls_back_instead_of_guessing(client, db):
    _gds_available(db)
    db.when("intersection_locations", [])  # neither endpoint located
    db.when("route_between_intersections", [ROUTE_ROW])
    response = client.post("/routes", json={"start": "Station A", "end": "Hut B"})
    assert response.status_code == 200
    # No box to project around, so nothing was projected...
    assert all(name != "graph_project_routing" for name, _ in db.calls)
    # ...and the answer came from shortestPath.
    assert any(name == "route_between_intersections" for name, _ in db.calls)
