"""Splitting a .cypher file into statements.

The original splitter cut on ';' before removing comments. Every .cypher file in
this repo has semicolons inside its comment prose, so a comment was sliced in
half and its tail was sent to the server as Cypher -- which is how
`durations are MINUTES.` became a syntax error the first time the schema was
applied to a real database.
"""

from pathlib import Path

from graph.neo4j_client import split_statements, strip_line_comments

GRAPH_DIR = Path(__file__).resolve().parent.parent / "graph"


def test_semicolon_inside_a_comment_does_not_start_a_statement():
    text = """
    // All distances are METRES (suffixed _m); durations are MINUTES.
    CREATE CONSTRAINT trail_id IF NOT EXISTS FOR (t:Trail) REQUIRE t.id IS UNIQUE;
    """
    statements = split_statements(text)
    assert len(statements) == 1
    assert statements[0].startswith("CREATE CONSTRAINT trail_id")
    assert "MINUTES" not in statements[0]


def test_comment_only_input_yields_no_statements():
    assert split_statements("// just a note;\n// and another;\n") == []


def test_double_slash_inside_a_string_literal_survives():
    text = "MERGE (s:Source {url: 'https://example.com/x'}) SET s.seen = true;"
    statements = split_statements(text)
    assert len(statements) == 1
    assert "https://example.com/x" in statements[0]


def test_escaped_quote_does_not_end_the_literal():
    text = r"CREATE (n:N {v: 'it\'s // not a comment'});"
    statements = split_statements(text)
    assert len(statements) == 1
    assert "not a comment" in statements[0]


def test_trailing_comment_after_a_statement_is_removed():
    statements = split_statements("RETURN 1; // trailing note; with a semicolon\n")
    assert statements == ["RETURN 1"]


def test_strip_line_comments_keeps_code_before_the_comment():
    assert strip_line_comments("RETURN 1 // note").rstrip() == "RETURN 1"


def test_every_repo_cypher_file_splits_into_runnable_statements():
    """No statement may begin with prose -- the symptom the original bug had."""
    for path in sorted(GRAPH_DIR.glob("*.cypher")):
        for statement in split_statements(path.read_text(encoding="utf-8")):
            first = statement.split(None, 1)[0].upper().lstrip("(")
            assert first in {
                "CREATE",
                "DROP",
                "MERGE",
                "MATCH",
                "CALL",
                "UNWIND",
                "WITH",
                "RETURN",
                "OPTIONAL",
                "SHOW",
            }, f"{path.name}: statement starts with prose -> {statement[:60]!r}"
