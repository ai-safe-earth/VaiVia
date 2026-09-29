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
    "graph_project_routing",
    "graph_drop_routing",
    "healthcheck",
    "graph_counts",
    # The saved-route templates: the favourites 404 check and the hydrate.
    "route_exists",
    "routes_by_ids",
    # The graph's own extent, for the one script that projects it into GDS
    # (check_graph_connectivity). It lives here rather than inline because
    # settings.default_bbox kept being used for it instead
    # (docs/fragilities.md #16).
    "graph_extent",
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


#: The only templates exempt from the write check, by name rather than by a
#: `graph_` prefix: they mutate the GDS catalogue, not graph data
#: (gds.graph.project reads, gds.graph.drop trips the regex on DROP). A prefix
#: would quietly extend the exemption to every future `graph_*` template —
#: graph_counts and graph_extent are ordinary reads and are checked like the
#: rest.
PROJECTION_LIFECYCLE = {"graph_project_routing", "graph_drop_routing"}


def test_no_template_writes_to_the_graph():
    """Read-only by construction: the query service must never mutate the graph.

    (Ingestion writes, but it does not use this library.)
    """
    forbidden = re.compile(
        r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP)\b(?!\s+CONSTRAINT)", re.I
    )
    for name in query_loader.query_names():
        if name in PROJECTION_LIFECYCLE:
            continue
        assert not forbidden.search(
            query_loader.get_query(name)
        ), f"{name} mutates data"


def test_routing_templates_never_traverse_semantic_edges():
    """PASSES_BY / COMPOSED_OF / LOCATED_IN must not appear in path expressions."""
    for name in ("route_between_intersections",):
        body = query_loader.get_query(name)
        assert "PASSES_BY" not in body
        assert "COMPOSED_OF" not in body
        assert "LOCATED_IN" not in body
        assert "NEAR_POI" not in body


def test_variable_length_traversals_are_bounded():
    unbounded = re.compile(r"\*\s*\]")  # e.g. [:CONNECTS_TO*]
    for name in query_loader.query_names():
        assert not unbounded.search(query_loader.get_query(name)), f"{name} unbounded"


# ── Fragment includes (2026-08-21): one filter block, no drift ──────────────


def test_fragments_are_not_runnable_templates():
    """A fragment is spliced into templates, never run on its own. It must not
    appear in query_names() and must raise on a get."""
    assert "route_card" not in query_loader.query_names()
    with pytest.raises(KeyError):
        query_loader.get_query("route_card")


def test_an_unknown_include_fails_loudly_at_parse():
    with pytest.raises(ValueError, match="unknown fragment"):
        query_loader.parse(
            "// fragment: a\nMATCH (n) RETURN n\n"
            "// name: q\n// include: b\nRETURN 1\n"
        )


def test_a_fragment_may_not_include_another():
    with pytest.raises(ValueError, match="max depth 1"):
        query_loader.parse(
            "// fragment: a\nRETURN 1\n"
            "// fragment: b\n// include: a\nRETURN 2\n"
            "// name: q\n// include: b\nRETURN 3\n"
        )


def test_every_route_reading_template_quarantines_warned_routes():
    """warnings = 0 is the quarantine on a stored route, and it has to hold on
    every surface that reads one.

    Favorites was the hole: route_exists let any route_id be saved and
    routes_by_ids hydrated it into a full card, so a 0.0 km OSM fragment
    wearing a famous name reached the screen by a different door.
    """
    for name in ("route_exists", "routes_by_ids"):
        body = query_loader.get_query(name)
        assert "r.warnings = 0" in body, f"{name} does not quarantine warned routes"


def test_the_favorites_row_comes_from_the_shared_fragment():
    """The card projection is one fragment, so the hydrated row cannot drift
    from whatever else renders a stored route.

    It was a hand-copy once and had already drifted — the copy carried a stray
    relationship variable — which is what the fragment mechanism exists to
    stop.
    """
    body = query_loader.get_query("routes_by_ids")
    assert "CALL (r) {" in body
    assert "// include:" not in body  # the fragment really was substituted in
    assert "[e:PASSES]" not in body  # the drift that proved the point
