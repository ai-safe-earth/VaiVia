"""Stored route documents over HTTP: geometry, detail and the honesty ladder.

POI-to-POI routing and its GDS projection were tested here too, until R7
deleted `POST /routes` (nothing called it). The A-to-B ask that IS reachable
runs through /chat, covered in test_chat_orchestrator.
"""

# ── saved route geometry (GET /routes/{id}/geojson) ──────────────────────

ROUTE_ID = "vv2-abc123def4567890-fwd"


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


# Contract-complete: id, schema_version and provenance.run_id are what the
# API now verifies before serving anything (docs/route-document.md).
DOCUMENT = {
    "id": ROUTE_ID,
    "schema_version": "2.0",
    "kind": "generated",
    "shape": "loop",
    "geometry": {"type": "LineString", "coordinates": [[9.4, 45.9], [9.41, 45.91]]},
    "provenance": {
        "run_id": "export-t1",
        "sources": [{"attribution": "© OpenStreetMap contributors"}],
    },
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
    """A saved route whose document store is unconfigured is a degradation,
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
        **DOCUMENT,
        "kind": "osm_route",
        "shape": "linear",
        "geometry": {
            "type": "MultiLineString",
            "coordinates": [
                [[9.4, 45.9], [9.41, 45.91], [9.42, 45.92]],
                [[9.5, 45.95], [9.51, 45.96]],
            ],
        },
    }
    _documents_dir(tmp_path, monkeypatch, document)
    db.when("route_exists", [{"id": ROUTE_ID}])
    body = client.get(f"/routes/{ROUTE_ID}/geojson").json()
    assert len(body["geometry"]["coordinates"]) == 3
    assert "pieces" in body["properties"]["note"]
    assert body["properties"]["pieces_total"] == 2
    assert body["properties"]["kind"] == "osm_route"
    assert body["properties"]["shape"] == "linear"


def test_the_longest_piece_is_measured_in_metres_not_points(
    client, db, tmp_path, monkeypatch
):
    """'Longest piece' must mean distance on the ground.

    Sorting by len() picked the piece with the most POINTS — on OSM data a
    function of how enthusiastically a mapper clicked, not of length. Here
    the sparse piece spans ~1.4 km in two points and the dense one ~30 m in
    five; the map must show the kilometre, not the doorstep.
    """
    document = {
        **DOCUMENT,
        "geometry": {
            "type": "MultiLineString",
            "coordinates": [
                [[9.4, 45.9], [9.41, 45.91]],
                [
                    [9.5, 45.95],
                    [9.5001, 45.9501],
                    [9.5002, 45.9501],
                    [9.5002, 45.9502],
                    [9.5003, 45.9502],
                ],
            ],
        },
    }
    _documents_dir(tmp_path, monkeypatch, document)
    db.when("route_exists", [{"id": ROUTE_ID}])
    body = client.get(f"/routes/{ROUTE_ID}/geojson").json()
    assert body["geometry"]["coordinates"] == [[9.4, 45.9], [9.41, 45.91]]


def test_a_document_wearing_another_id_is_a_visible_failure(
    client, db, tmp_path, monkeypatch
):
    """The filename used to be the whole contract: a file at this path was
    served verbatim whatever its id said. A store desynced from the
    graph's saved :Route must fail loudly, never display another route."""
    _documents_dir(
        tmp_path, monkeypatch, {**DOCUMENT, "id": "vv2-5011b0d1e5011b0d-fwd"}
    )
    db.when("route_exists", [{"id": ROUTE_ID}])
    response = client.get(f"/routes/{ROUTE_ID}/geojson")
    assert response.status_code == 503
    assert "document_mismatch" in response.json()["detail"]


def test_an_unknown_schema_version_is_refused(client, db, tmp_path, monkeypatch):
    """A reader that does not know a version must say so, not serve it on
    the guess that the fields still line up."""
    _documents_dir(tmp_path, monkeypatch, {**DOCUMENT, "schema_version": "9.9"})
    db.when("route_exists", [{"id": ROUTE_ID}])
    response = client.get(f"/routes/{ROUTE_ID}/geojson")
    assert response.status_code == 503
    assert "schema_version" in response.json()["detail"]


def test_a_document_from_another_build_is_refused(client, db, tmp_path, monkeypatch):
    """A :Route from export N serving a file from export N-1 was a
    perfectly silent 200 — the name from one build, the line from another,
    which is the card/map defect at the data layer."""
    _documents_dir(tmp_path, monkeypatch, DOCUMENT)  # provenance.run_id export-t1
    db.when("route_exists", [{"id": ROUTE_ID, "doc_run_id": "export-t2"}])
    response = client.get(f"/routes/{ROUTE_ID}/geojson")
    assert response.status_code == 503
    assert "build_mismatch" in response.json()["detail"]


def test_matching_builds_serve_and_a_graph_without_the_field_still_serves(
    client, db, tmp_path, monkeypatch
):
    """Agreement passes; a node saved before doc_run_id existed carries
    null, which is 'nothing to compare', not 'wrong'."""
    _documents_dir(tmp_path, monkeypatch, DOCUMENT)
    db.when("route_exists", [{"id": ROUTE_ID, "doc_run_id": "export-t1"}])
    assert client.get(f"/routes/{ROUTE_ID}/geojson").status_code == 200

    db.when("route_exists", [{"id": ROUTE_ID, "doc_run_id": None}])
    assert client.get(f"/routes/{ROUTE_ID}/geojson").status_code == 200


# ── /routes/{id}/detail: the expandable card's payload ──────────────────────

DETAIL_DOCUMENT = {
    "id": ROUTE_ID,
    "schema_version": "2.0",
    "kind": "osm_route",
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
    "provenance": {
        "run_id": "export-t1",
        "sources": [{"attribution": "© OpenStreetMap contributors"}],
    },
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
