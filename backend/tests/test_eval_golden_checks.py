"""The answer-stage eval checks are themselves code, so they get checked.

check_answer grades the RAW model answer against the code-checkable rules of
ANSWER_SYSTEM_PROMPT (no links, no trailforks, no headers, named loops named).
Production repairs links after the fact (sanitize.strip_links_stream); the
eval measures whether the model obeyed BEFORE the repair — which is why these
detectors must not false-positive on ordinary trail prose.
"""

from chat.sanitize import find_link
from scripts.eval_golden import check_answer


def test_a_markdown_link_is_found() -> None:
    assert find_link("take the [Sentiero del Viandante](https://x.com/v)") is not None


def test_a_bare_domain_with_a_path_is_found() -> None:
    """The 2026-08-21 live smoke: the model typed trailforks.com links from
    nothing; the domain shape must be caught even without a scheme."""
    assert find_link("details at trailforks.com/trails/lecco") is not None


def test_ordinary_prose_with_dotted_measurements_is_not_a_link() -> None:
    """`Monte Misma, 1.161 m.s.l.m.` reads like a domain to a naive regex and
    is exactly the prose a trail answer contains."""
    assert find_link("Monte Misma, 1.161 m.s.l.m. above the valley") is None


def test_a_linked_answer_fails() -> None:
    problems = check_answer("See www.example.com for the route.", {})
    assert any("link" in p for p in problems)


def test_naming_trailforks_fails_even_without_a_link() -> None:
    """The prompt bans the word, not just the URL: naming Trailforks would
    misattribute OSM data (docs/licensing.md)."""
    problems = check_answer("As Trailforks describes it, a fine ridge.", {})
    assert any("trailforks" in p for p in problems)


def test_a_markdown_header_fails() -> None:
    problems = check_answer("# Best trails\nA fine walk by the lake.", {})
    assert any("header" in p for p in problems)


def test_a_named_loop_must_be_named_in_the_answer() -> None:
    view = {"loops": [{"id": "vv2-abc", "name": "Anello di Camposecco"}]}
    problems = check_answer("A fine 12 km loop past a hut.", view)
    assert any("Anello di Camposecco" in p for p in problems)


def test_a_destination_name_matches_the_out_and_back_phrasing() -> None:
    """A destination route is named 'To Monte X' and the prompt tells the
    model to write 'out and back to Monte X' — the match is case-insensitive,
    or every destination loop would read as uncovered."""
    view = {"loops": [{"id": "vv2-abc", "name": "To Monte Forcellino"}]}
    answer = "A 9.8 km outing, out and back to Monte Forcellino."
    assert check_answer(answer, view) == []


def test_an_unnamed_loop_needs_no_name() -> None:
    """When name is null the prompt says describe by distance, so absence of a
    name is not a coverage failure."""
    view = {"loops": [{"id": "vv2-abc", "name": None}]}
    assert check_answer("A fine 12 km loop past a hut.", view) == []


def test_a_clean_answer_passes() -> None:
    view = {"loops": [{"id": "vv2-abc", "name": "Anello di Camposecco"}]}
    answer = (
        "The Anello di Camposecco is a 12.4 km loop with 640 m of climbing, "
        "back to where you started. Nothing here is exposed in summer."
    )
    assert check_answer(answer, view) == []
