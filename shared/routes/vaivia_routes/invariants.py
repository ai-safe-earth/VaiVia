"""Planner invariants: the F2 review rules as pure functions. Detect-only.

These replace the QGIS review for a route a user sees seconds after asking
(docs/route-design.md, decision 1; docs/plan.md Phase 10 F2). Each detector
returns findings; the caller decides what a finding means, because every
tolerance here is still unmeasured — the rule ships detect-only until the
distribution is read, like every other tolerance in this project.

`assert_connected` lives in assemble.py beside the walk it guards.
"""

from __future__ import annotations

from typing import NamedTuple

from vaivia_routes.assemble import WalkedEdge


class Finding(NamedTuple):
    """A span of the walk, as step indices [start, end] inclusive."""

    start: int
    end: int
    length_m: float


def mini_loops(edges: list[WalkedEdge], within_m: float) -> list[Finding]:
    """Spans where the walk returns to a vertex visited < `within_m` earlier.

    A mini loop is a detour a walker would call a mistake: out and around a
    block, back to where you stood moments ago. Excising the span keeps the
    walk connected by construction — both ends are one vertex. Needs
    source/target on every edge; steps without them are not checked.
    """
    findings: list[Finding] = []
    travelled = 0.0
    # vertex -> (steps completed when last standing there, distance there)
    last_seen: dict[int, tuple[int, float]] = {}
    for i, edge in enumerate(edges):
        start_v = edge.source if edge.forward else edge.target
        if start_v is not None:
            last_seen.setdefault(start_v, (i, travelled))
        travelled += edge.length_m
        end_v = edge.target if edge.forward else edge.source
        if end_v is None:
            continue
        seen = last_seen.get(end_v)
        # Not the walk's own closure: a loop legitimately ends where it began.
        if (
            seen is not None
            and 0 < travelled - seen[1] < within_m
            and i + 1 < len(edges)
        ):
            # The excisable steps [start, end]: everything walked since last
            # standing at end_v, up to and including the returning step.
            findings.append(Finding(seen[0], i, travelled - seen[1]))
        last_seen[end_v] = (i + 1, travelled)
    return findings


def spurs(edges: list[WalkedEdge]) -> list[Finding]:
    """Palindromic sub-sequences: out along some edges and straight back.

    Grown outwards from every immediate backtrack (the same edge walked
    twice in opposite directions in consecutive steps). The caller exempts
    out_and_back shapes, whose whole walk is the palindrome by construction
    (assemble.strict_return), and keeps spurs whose tip is the destination.
    """
    findings: list[Finding] = []
    claimed_until = -1
    for k in range(len(edges) - 1):
        a, b = edges[k], edges[k + 1]
        if k <= claimed_until or a.edge_id != b.edge_id or a.forward == b.forward:
            continue
        lo, hi = k, k + 1
        while (
            lo > claimed_until + 1
            and hi + 1 < len(edges)
            and edges[lo - 1].edge_id == edges[hi + 1].edge_id
            and edges[lo - 1].forward != edges[hi + 1].forward
        ):
            lo -= 1
            hi += 1
        findings.append(Finding(lo, hi, sum(e.length_m for e in edges[lo : hi + 1])))
        claimed_until = hi
    return findings
