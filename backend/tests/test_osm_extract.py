"""Topology extraction tests on a synthetic 3-way network.

Network (shared node n3 = the only interior intersection):

    n1 ──w1── n2 ──w1── n3 ──w1── n4        w1: path, 4 nodes
                         │
                        w2 (oneway=yes, track)
                         │
                        n5
    n6 ──w3── n3                             w3: footway
"""

from ingestion.osm_extract import (
    connects_to_rows,
    extract,
    located_in_rows,
    passes_by_rows,
    poi_type_for,
)


def _node_geom(*latlons):
    return [{"lat": lat, "lon": lon} for lat, lon in latlons]


def overpass_fixture():
    return {
        "elements": [
            {
                "type": "way",
                "id": 1,
                "tags": {"highway": "path", "surface": "gravel"},
                "nodes": [1, 2, 3, 4],
                "geometry": _node_geom(
                    (45.0, 9.0), (45.001, 9.0), (45.002, 9.0), (45.003, 9.0)
                ),
            },
            {
                "type": "way",
                "id": 2,
                "tags": {"highway": "track", "oneway": "yes"},
                "nodes": [3, 5],
                "geometry": _node_geom((45.002, 9.0), (45.002, 9.002)),
            },
            {
                "type": "way",
                "id": 3,
                "tags": {"highway": "footway"},
                "nodes": [6, 3],
                "geometry": _node_geom((45.004, 9.001), (45.002, 9.0)),
            },
            {
                "type": "node",
                "id": 100,
                "lat": 45.0021,
                "lon": 9.0001,
                "tags": {"tourism": "alpine_hut", "name": "Rifugio Test"},
            },
            {
                "type": "node",
                "id": 101,
                "lat": 45.5,
                "lon": 9.5,
                "tags": {"amenity": "swimming_area"},
            },
        ]
    }


def test_way_split_at_shared_node():
    result = extract(overpass_fixture())
    ids = {s.osm_way_id for s in result.segments}
    # w1 splits at n3 (shared with w2/w3) into two pieces; w2 and w3 stay whole
    assert ids == {"1#0", "1#1", "2#0", "3#0"}
    piece = next(s for s in result.segments if s.osm_way_id == "1#0")
    assert (piece.start_node, piece.end_node) == ("1", "3")
    assert len(piece.coordinates) == 3


def test_intersections_are_endpoints_and_shared_nodes():
    result = extract(overpass_fixture())
    # n2 is interior to w1 only -> NOT an intersection
    assert set(result.intersections) == {"1", "3", "4", "5", "6"}


def test_connects_to_directions_respect_oneway():
    result = extract(overpass_fixture())
    rows = connects_to_rows(result.segments)
    pairs = {(r["from"], r["to"]) for r in rows}
    assert ("3", "5") in pairs  # oneway forward
    assert ("5", "3") not in pairs  # oneway reverse suppressed
    assert ("1", "3") in pairs and ("3", "1") in pairs  # two-way


def test_extract_is_deterministic():
    a, b = extract(overpass_fixture()), extract(overpass_fixture())
    assert [vars(s) for s in a.segments] == [vars(s) for s in b.segments]
    assert a.intersections == b.intersections


def test_poi_extraction_and_tag_mapping():
    result = extract(overpass_fixture())
    types = {p["osm_id"]: p["type"] for p in result.pois}
    assert types == {"100": "hut", "101": "bathing_water"}
    assert poi_type_for({"highway": "path"}) is None


def test_passes_by_threshold():
    result = extract(overpass_fixture())
    # hut 100 sits ~15 m from way 2's start; swimming area 101 is ~60 km away
    rows = passes_by_rows(result.segments, result.pois, threshold_m=50)
    matched_pois = {r["poi_osm_id"] for r in rows}
    assert "100" in matched_pois
    assert "101" not in matched_pois


def test_located_in_rows_uses_bbox():
    result = extract(overpass_fixture())
    rows = located_in_rows(result, bbox=(44.9, 8.9, 45.1, 9.1), region="Test")
    assert set(rows["intersections"]) == {"1", "3", "4", "5", "6"}
    assert rows["pois"] == ["100"]  # 101 is outside the bbox
