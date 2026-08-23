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
    # The catalogue templates. Absent until 2026-08-21, which meant deleting
    # the whole loop-search surface would not have failed this test.
    "search_loops",
    "estimate_loops",
    "route_exists",
    "routes_by_ids",
    "intersection_locations",
    # The graph's own extent, shared by every script that projects it into
    # GDS. It lives here rather than as a string in three scripts because
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


# ── Fragment includes (2026-08-21): one filter block, no drift ──────────────


def test_fragments_are_not_runnable_templates():
    """A fragment is spliced into templates, never run on its own. It must not
    appear in query_names() and must raise on a get."""
    assert "loop_candidates" not in query_loader.query_names()
    assert "loop_poi_conjunction" not in query_loader.query_names()
    with pytest.raises(KeyError):
        query_loader.get_query("loop_candidates")


def test_search_and_estimate_share_the_identical_filter_block():
    """The whole point of the fragment: a count cannot diverge from the search
    it counts. Assert the shared block is byte-identical in both."""
    search = query_loader.get_query("search_loops")
    estimate = query_loader.get_query("estimate_loops")

    def block(body: str) -> str:
        start = body.index("MATCH (r:Route)")
        end = body.index("p.kind = wanted })") + len("p.kind = wanted })")
        return body[start:end]

    assert block(search) == block(estimate)
    assert "found_kinds" not in search  # the CALL rewrite dropped it


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


def test_estimate_loops_is_read_only_and_bounded():
    """It runs through run_read, but the guard suite already asserts no-write
    and bounded-traversal over every template including this one — this pins
    that estimate_loops is covered rather than special-cased."""
    body = query_loader.get_query("estimate_loops")
    assert not re.search(
        r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP)\b", body, re.I
    )
    assert "count(r) AS total" in body


def test_every_route_reading_template_quarantines_warned_routes():
    """warnings = 0 is the catalogue's quarantine, and it has to hold on every
    surface — not only on search.

    Favorites was the hole: route_exists let any route_id be saved and
    routes_by_ids hydrated it into a full card, so the 0.0 km OSM fragments
    wearing famous names that loop_candidates exists to hide reached the
    screen by a different door.
    """
    for name in ("search_loops", "estimate_loops", "route_exists", "routes_by_ids"):
        body = query_loader.get_query(name)
        assert "r.warnings = 0" in body, f"{name} does not quarantine warned routes"


def test_the_favorites_row_is_the_search_row():
    """Same fragment, so a saved card and a search card cannot come to differ.

    They were hand-copies once and had already drifted — the copy carried a
    stray relationship variable — which is what the fragment mechanism exists
    to stop.
    """
    search = query_loader.get_query("search_loops")
    favorites = query_loader.get_query("routes_by_ids")

    def card(body: str) -> str:
        return body[body.index("CALL (r) {") :].split("ORDER BY")[0].strip()

    assert card(search) == card(favorites)
    assert "[e:PASSES]" not in favorites  # the drift that proved the point
