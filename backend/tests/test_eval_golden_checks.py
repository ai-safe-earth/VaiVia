"""The answer-stage eval checks are themselves code, so they get checked.

check_answer grades the RAW model answer against the code-checkable rules of
ANSWER_SYSTEM_PROMPT (no links, no trailforks, no headers, the totals stated).
Production repairs links after the fact (sanitize.strip_links_stream); the
eval measures whether the model obeyed BEFORE the repair — which is why these
detectors must not false-positive on ordinary trail prose.
"""

import pytest

from chat.sanitize import find_link
from scripts.eval_golden import check_answer, select


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


def test_a_missing_count_fails() -> None:
    """The count rule (2026-08-27): when the view carries the true total, the
    answer must state it as digits — a count the cards contradict is worse
    than none."""
    view = {"total_loops": 12, "loops": [{"id": "vv2-abc", "name": "Anello"}]}
    problems = check_answer("I found several routes for your request.", view)
    assert any("count missing" in p for p in problems)


def test_the_stated_count_passes() -> None:
    view = {"total_loops": 12, "loops": [{"id": "vv2-abc", "name": "Anello"}]}
    answer = "I found 12 routes — add a distance to narrow them down."
    assert check_answer(answer, view) == []


def test_a_count_hiding_inside_another_number_fails() -> None:
    """Digit-bounded, not substring: total 5 is not stated by "15 routes" or
    by a "12.5 km" distance — those were the false passes of the naive `in`."""
    view = {"total_loops": 5, "loops": []}
    assert check_answer("I found 15 routes for you.", view) != []
    assert check_answer("A fine outing of 12.5 km.", view) != []


def test_a_comma_grouped_count_still_passes() -> None:
    view = {"total_loops": 1035, "loops": []}
    assert check_answer("I found 1,035 routes — narrow them down.", view) == []


def test_the_trails_total_is_checked_too() -> None:
    """The prompt demands total_trails as digits exactly like total_loops."""
    view = {"total_trails": 7, "trails": [{"id": "t1"}]}
    problems = check_answer("I found some nice trails.", view)
    assert any("count missing" in p for p in problems)
    assert check_answer("I found 7 named trails.", view) == []


def test_a_zero_total_needs_no_digit() -> None:
    """An emptied search answers with the empty-block prose ("nothing
    matched..."), which the count rule must not punish."""
    view = {"total_loops": 0, "loops": []}
    answer = "Nothing matched all of that — try relaxing the distance."
    assert check_answer(answer, view) == []


def test_no_total_means_no_count_requirement() -> None:
    """A view without total_loops (a trails-only or routes answer) imposes no
    count on the reply."""
    view = {"loops": [{"id": "vv2-abc", "name": "Anello"}]}
    assert check_answer("A fine walk by the lake.", view) == []


def test_a_clean_answer_passes() -> None:
    view = {"total_loops": 5, "loops": [{"id": "vv2-abc", "name": "Anello"}]}
    answer = "I found 5 routes for your request."
    assert check_answer(answer, view) == []


# --- --only, the single-entry rerun -----------------------------------------
# The flag exists so one failure can be read again without paying for the other
# forty-nine. A typo in it must not read as a clean run.

ENTRIES = [{"id": "g01"}, {"id": "g27"}, {"id": "g49"}]


def test_no_only_runs_the_whole_dataset() -> None:
    assert select(ENTRIES, None) == ENTRIES
    assert select(ENTRIES, "") == ENTRIES


def test_only_keeps_dataset_order_not_the_order_asked_for() -> None:
    """The run log and the console read top to bottom; a rerun that shuffled
    the entries would not line up against the run before it."""
    assert [entry["id"] for entry in select(ENTRIES, "g49,g01")] == ["g01", "g49"]


def test_only_tolerates_spaces_around_the_commas() -> None:
    assert [entry["id"] for entry in select(ENTRIES, " g27 , g49 ")] == ["g27", "g49"]


def test_an_unknown_id_stops_the_run() -> None:
    """Silently selecting nothing would print 0/0 and read as a pass."""
    with pytest.raises(SystemExit) as caught:
        select(ENTRIES, "g27,g99")
    assert "g99" in str(caught.value)
