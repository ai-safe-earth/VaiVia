"""Where sibling routes part — the corridor rules, pure."""

from __future__ import annotations

from draw.divergence import Step, divergence, shared_steps


def walk(*edges):
    return [Step(e, True, 100.0, e * 10) for e in edges]


def test_shared_steps_counts_matching_leading_edges_and_direction():
    assert shared_steps(walk(1, 2, 3), walk(1, 2, 9)) == 2
    assert shared_steps(walk(1, 2), walk(1, 2)) == 2
    # Same edge walked the OTHER way is a different corridor.
    a = [Step(1, True, 100.0, 10)]
    b = [Step(1, False, 100.0, 5)]
    assert shared_steps(a, b) == 0


def test_divergence_measures_against_the_longest_sharing_sibling():
    walks = {
        "a": walk(1, 2, 3, 4),
        "b": walk(1, 2, 8, 9),
        "c": walk(1, 7, 7, 7),
    }
    parted = divergence(walks)
    # a shares 2 steps with b (longest), so it parts at edge 2's end.
    assert parted["a"] == (20, 200.0)
    assert parted["b"] == (20, 200.0)
    # c shares only the first edge with either sibling.
    assert parted["c"] == (10, 100.0)


def test_no_shared_first_step_means_no_corridor():
    walks = {"a": walk(1, 2), "b": walk(5, 6)}
    assert divergence(walks) == {"a": None, "b": None}


def test_a_route_without_siblings_records_none():
    assert divergence({"only": walk(1, 2, 3)}) == {"only": None}
