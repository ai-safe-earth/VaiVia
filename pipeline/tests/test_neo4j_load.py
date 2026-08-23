"""The document → graph-row mapping, and the template parser. No database.

Neo4j is a reader of the route documents; these tests pin what the reading
carries and what it deliberately leaves behind.
"""

from __future__ import annotations

from export.document import Span, build_document
from export.neo4j_load import document_rows, templates


def _base_kwargs(**overrides):
    base = {
        "route_id": "generated-abc123",
        "kind": "generated",
        "shape": "destination",
        "identity": {
            "name": "To Rifugio Elisa",
            "ref": None,
            "activity": "hiking",
            "network": None,
            "waymark": None,
            "from": None,
            "to": "Rifugio Elisa",
            "operator": None,
            "regions": ["Lecco"],
            "osm_relation_id": None,
        },
        "geometry": {
            "type": "LineString",
            "coordinates": [[9.33, 45.92], [9.35, 45.94]],
        },
        "bbox": [9.33, 45.92, 9.35, 45.94],
        "distance_m": 9000.0,
        "ascent_m": 800.0,
        "descent_m": 800.0,
        "lowest_m": 400.0,
        "highest_m": 1200.0,
        "profile": {"distance_m": [0.0, 9000.0], "elevation_m": [400.0, 1200.0]},
        "surface_spans": [Span("gravel", 9000.0)],
        "sac_spans": [Span("mountain_hiking", 8000.0), Span("alpine_hiking", 100.0)],
        "pieces": 1,
        "edges_without_profile": 0,
        "matched_fraction": None,
        "places": [
            {
                "id": "n1",
                "kind": "hut",
                "name": "Rifugio Elisa",
                "ele_m": 1515.0,
                "lon": 9.35,
                "lat": 45.94,
                "offset_m": 12.0,
                "distance_along_m": 8800.0,
                "is_start": False,
            }
        ],
        "start": {
            "vertex_id": 42,
            "names": [],
            "anchors": 1,
            "nearest_m": 3.0,
            "car_free": True,
            "point": {"type": "Point", "coordinates": [9.33, 45.92]},
        },
        "provenance": {
            "run_id": "draw-x",
            "producer": "pipeline/draw/emit.py",
            "generation": {
                "activity": "foot",
                "shape": "destination",
                "destination": {"id": "poi:n1", "kind": "hut", "name": "Rifugio Elisa"},
                "target_m": 10000.0,
                "seed": 0,
                "score": 0.81,
                "mtb_rideable": True,
                "mtb_scale": None,
                "bike_blocked_m": 0.0,
                "off_road_share": 0.62,
                "retrace_share": 0.05,
            },
            "sources": [
                {"name": "OpenStreetMap", "licence": "ODbL 1.0", "attribution": "©"}
            ],
        },
    }
    base.update(overrides)
    return base


def sample(**overrides):
    return document_rows(build_document(**_base_kwargs(**overrides)))


def test_selection_properties_travel_and_geometry_does_not():
    rows = sample()
    props = rows["route"]["props"]

    assert rows["route"]["route_id"] == "generated-abc123"
    assert props["name"] == "To Rifugio Elisa"
    assert props["sac_scale"] == "mountain_hiking"  # character
    assert props["sac_max"] == "alpine_hiking"  # exigent
    # Ranks travel as ints because Cypher cannot order the grade strings.
    assert props["sac_scale_rank"] == 2
    assert props["sac_max_rank"] == 4
    assert props["mtb_rideable"] is True
    assert props["destination_name"] == "Rifugio Elisa"
    assert props["bbox"] == [9.33, 45.92, 9.35, 45.94]
    # The document stays canonical for these; Neo4j must not grow a second copy.
    assert "geometry" not in props
    assert "profile" not in props


def test_places_and_passes_stay_aligned():
    rows = sample()

    assert rows["places"][0]["place_id"] == "n1"
    assert rows["places"][0]["lat"] == 45.94
    assert rows["passes"][0] == {
        "route_id": "generated-abc123",
        "place_id": "n1",
        "seq": 0,
        "offset_m": 12.0,
        "distance_along_m": 8800.0,
        "is_start": False,
    }


def test_the_start_becomes_a_node_and_a_link():
    rows = sample()

    assert rows["start"]["vertex_id"] == 42
    assert rows["start"]["car_free"] is True
    assert rows["start_link"]["nearest_m"] == 3.0


def test_a_document_without_a_start_loads_without_one():
    rows = sample(start=None)

    assert rows["start"] is None
    assert rows["start_link"] is None


def test_a_place_without_coordinates_is_skipped_not_invented():
    # Spike-era documents carried no coordinates; a node at (0, 0) would be a
    # lie on the map.
    rows = sample(
        places=[
            {
                "id": "n9",
                "kind": "peak",
                "name": None,
                "ele_m": None,
                "offset_m": 5.0,
                "distance_along_m": 1.0,
                "is_start": False,
            }
        ]
    )

    assert rows["places"] == []
    assert rows["passes"] == []


def test_an_osm_document_maps_with_its_measured_shape():
    rows = sample(
        route_id="osm-relation-74613",
        kind="osm_route",
        shape="circular",  # measured by export/shape.py, carried top-level
        matched_fraction=0.97,
        provenance={
            "run_id": "export-x",
            "producer": "pipeline/export/route_documents.py",
            "sources": [
                {"name": "OpenStreetMap", "licence": "ODbL 1.0", "attribution": "©"}
            ],
        },
    )
    props = rows["route"]["props"]

    assert props["kind"] == "osm_route"
    assert props["shape"] == "circular"
    assert props["matched_fraction"] == 0.97
    assert props["score"] is None  # absent is not zero


def test_a_legacy_document_without_shape_falls_back():
    """Schema 1.1 documents carry no top-level shape. The loader serves them
    with the old chain -- generation shape for generated, 'named' for OSM --
    so a store that predates the bump still loads rather than lying."""
    from export.document import build_document

    generated = build_document(**_base_kwargs())
    del generated["shape"]
    assert document_rows(generated)["route"]["props"]["shape"] == "destination"

    osm = build_document(
        **{
            **_base_kwargs(),
            "route_id": "osm-relation-74613",
            "kind": "osm_route",
            "provenance": {
                "run_id": "export-x",
                "producer": "pipeline/export/route_documents.py",
                "sources": [
                    {"name": "OpenStreetMap", "licence": "ODbL 1.0", "attribution": "©"}
                ],
            },
        }
    )
    del osm["shape"]
    assert document_rows(osm)["route"]["props"]["shape"] == "named"


def test_every_template_the_loader_runs_exists_and_is_parameterised():
    cypher = templates()
    needed = {
        "constraints_route",
        "constraints_place",
        "constraints_start",
        "count_owned",
        "wipe_owned",
        "load_routes",
        "load_places",
        "load_starts",
        "link_passes",
        "link_starts",
        "verify_counts",
        "sample_selection",
    }

    assert needed <= set(cypher)
    # Parameters only, never interpolation: the backend/graph discipline.
    for name in ("load_routes", "load_places", "link_passes"):
        assert "$rows" in cypher[name]
