"""The emitter and the contract must agree. No database — pure functions.

`schemas/route-document.schema.json` is read by other tiers (docs/route-document.md:
the API, the Neo4j export, the frontend, the social layer). A contract nothing
checks is a comment, so these tests build documents the way the emitter does and
validate them against the file the readers will use.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from export.document import SCHEMA_VERSION, Span, build_document

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "schemas" / "route-document.schema.json"
)


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def document(**overrides):
    base = {
        "route_id": "osm-relation-74613",
        "kind": "osm_route",
        "identity": {
            "name": None,
            "ref": "14",
            "activity": "hiking",
            "network": "lwn",
            "waymark": "red:red:white_stripe:14:black",
            "from": "Rongio",
            "to": "Buco di Grigna",
            "operator": "Club Alpino Italiano Grigne",
            "regions": ["Lecco"],
            "osm_relation_id": 74613,
        },
        "geometry": {
            "type": "LineString",
            "coordinates": [[9.330443, 45.927594], [9.387812, 45.942009]],
        },
        "bbox": [9.330443, 45.927594, 9.387812, 45.942009],
        "distance_m": 7857.1,
        "ascent_m": 1478.5,
        "descent_m": 686.2,
        "lowest_m": 396.7,
        "highest_m": 1827.6,
        "profile": {"distance_m": [0.0, 4.1], "elevation_m": [397.9, 397.5]},
        "surface_spans": [Span("rock", 1500), Span(None, 2500), Span("paved", 3000)],
        "sac_spans": [Span("mountain_hiking", 4000), Span("hiking", 3000)],
        "pieces": 1,
        "edges_without_profile": 0,
        "matched_fraction": 1.0,
        "places": [
            {
                "id": "w41777893",
                "kind": "parking",
                "name": "Piazza Sant'Antonio",
                "ele_m": None,
                "lon": 9.3304,
                "lat": 45.9277,
                "offset_m": 45.6,
                "distance_along_m": 0.0,
                "is_start": True,
            }
        ],
        "start": {
            "vertex_id": 1828,
            "names": [],
            "anchors": 1,
            "nearest_m": 0.0,
            "car_free": False,
            "point": {"type": "Point", "coordinates": [9.3304428, 45.9276882]},
        },
        "provenance": {
            "run_id": "export-2e6f07a6",
            "producer": "pipeline/export/route_documents.py",
            "sources": [
                {
                    "name": "OpenStreetMap",
                    "licence": "ODbL 1.0",
                    "attribution": "© OpenStreetMap contributors",
                    "url": "https://www.openstreetmap.org/copyright",
                    "provides": ["geometry"],
                }
            ],
        },
    }
    base.update(overrides)
    return build_document(**base)


def test_what_the_emitter_produces_satisfies_the_contract(validator):
    validator.validate(document())


def test_the_builder_and_the_schema_agree_on_the_version(validator):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert SCHEMA_VERSION in schema["$id"]
    assert document()["schema_version"] == SCHEMA_VERSION


def test_a_route_with_nothing_known_still_validates(validator):
    # The honest-nulls case: no profile, no grade, no tags, no start, nothing
    # passed. It must be a valid document, not an unrepresentable one.
    validator.validate(
        document(
            ascent_m=None,
            descent_m=None,
            lowest_m=None,
            highest_m=None,
            profile=None,
            surface_spans=[Span(None, 1000)],
            sac_spans=[Span(None, 1000)],
            places=[],
            start=None,
            edges_without_profile=3,
        )
    )


def test_a_generated_route_validates_with_no_relation_to_match(validator):
    validator.validate(
        document(
            route_id="generated-9f2c1ab4",
            kind="generated",
            identity={"name": "Loop from Ballabio", "regions": ["Lecco"]},
            matched_fraction=None,
        )
    )


def test_a_route_in_pieces_validates_and_says_so(validator):
    doc = document(
        pieces=3,
        geometry={
            "type": "MultiLineString",
            "coordinates": [
                [[9.33, 45.92], [9.34, 45.93]],
                [[9.35, 45.94], [9.36, 45.95]],
            ],
        },
        places=[
            {
                "id": "n1",
                "kind": "peak",
                "name": "Somewhere",
                "ele_m": 1800.0,
                "lon": 9.34,
                "lat": 45.93,
                "offset_m": 12.0,
                # No single measure along a route held in pieces.
                "distance_along_m": None,
                "is_start": False,
            }
        ],
    )
    validator.validate(doc)

    assert doc["continuity"]["continuous"] is False
    assert any("disconnected pieces" in w for w in doc["quality"]["warnings"])


def test_attribution_is_required_by_the_contract_not_merely_supplied(validator):
    # ODbL attribution travels inside the document, so a consumer rendering the
    # geometry elsewhere cannot strip it by accident. The schema has to enforce
    # that, or the next producer omits it and nothing notices.
    naked = document()
    naked["provenance"]["sources"] = []

    assert not validator.is_valid(naked)

    del naked["provenance"]["sources"]
    assert not validator.is_valid(naked)


def test_the_schema_rejects_a_duration_smuggled_into_measures(validator):
    # Absent on purpose (docs/route-document.md). `measures` is closed so a
    # miscalibrated figure cannot arrive by accident.
    doc = document()
    doc["measures"]["duration_min"] = 900

    assert not validator.is_valid(doc)


def test_the_schema_rejects_an_unknown_sac_grade(validator):
    # 12 edges in this network carry junk in sac_scale, one of them a sentence.
    doc = document()
    doc["difficulty"]["sac_scale"] = "a sentence about stone ruins"

    assert not validator.is_valid(doc)
