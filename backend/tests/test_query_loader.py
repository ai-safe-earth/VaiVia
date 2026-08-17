"""The template library must parse, be unique, and stay parameter-only."""

import re

import pytest

from graph import query_loader

EXPECTED = {
    "search_trails",
    "trail_by_id",
    "trail_geometry",
    "nearest_intersection",
    "poi_by_name",
    "poi_by_name_fulltext",
    "semantic_search_trails",
    "semantic_search_trails_filtered",
    "count_embedded_trails",
    "route_between_intersections",
    "route_gds_dijkstra",
    "graph_project_routing",
    "graph_drop_routing",
    "healthcheck",
    "graph_counts",
}


def test_all_expected_templates_load():
    assert EXPECTED <= set(query_loader.query_names())


def test_unknown_template_raises_with_suggestions():
    with pytest.raises(KeyError, match="unknown query template"):
        query_loader.get_query("no_such_query")


def test_bodies_are_stripped_of_comments():
    for name in query_loader.query_names():
        body = query_loader.get_query(name)
        assert body, f"{name} is empty"
        assert not any(
            line.strip().startswith("//") for line in body.splitlines()
        ), f"{name} still contains comment lines"


def test_duplicate_names_are_rejected():
    with pytest.raises(ValueError, match="duplicate template name"):
        query_loader.parse("// name: a\nRETURN 1\n// name: a\nRETURN 2\n")


def test_empty_body_is_rejected():
    with pytest.raises(ValueError, match="no statement body"):
        query_loader.parse("// name: a\n// only a comment\n")


def test_no_template_writes_to_the_graph():
    """Read-only by construction: the query service must never mutate the graph.

    (Ingestion writes, but it does not use this library.)
    """
    forbidden = re.compile(
        r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP)\b(?!\s+CONSTRAINT)", re.I
    )
    for name in query_loader.query_names():
        if name.startswith("graph_"):  # GDS projection lifecycle, not graph data
            continue
        assert not forbidden.search(
            query_loader.get_query(name)
        ), f"{name} mutates data"


def test_routing_templates_never_traverse_semantic_edges():
    """PASSES_BY / COMPOSED_OF / LOCATED_IN must not appear in path expressions."""
    for name in ("route_between_intersections", "route_gds_dijkstra"):
        body = query_loader.get_query(name)
        assert "PASSES_BY" not in body
        assert "COMPOSED_OF" not in body
        assert "LOCATED_IN" not in body
        assert "NEAR_POI" not in body


def test_variable_length_traversals_are_bounded():
    unbounded = re.compile(r"\*\s*\]")  # e.g. [:CONNECTS_TO*]
    for name in query_loader.query_names():
        assert not unbounded.search(query_loader.get_query(name)), f"{name} unbounded"
