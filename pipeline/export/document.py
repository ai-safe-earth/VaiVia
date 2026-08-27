"""Assembling a route document. Pure, so it is tested.

The route document is the product (docs/route-document.md). PostGIS holds the
value and answers the geometry questions; this turns one route's rows into the
artefact everything downstream reads.

Fetching is one statement per route and belongs in SQL. Assembly does not: the
length-weighted rules are per-feature judgement with a history of being got
wrong, and every one of them is pinned here by a test.

Three rules carried over from docs/metadata-rules.md rather than reinvented:

  * **Difficulty is the hardest grade covering >= 5% of the length**, never the
    max. 30 m of scramble must not label a 20 km valley walk as alpine. Same
    rule as backend/graph/graphhopper.py::_weighted_max, which proved it.
  * **Surface is a distribution kept whole**, plus a dominant value. "62%
    unpaved" is a fact about a route; "unpaved" alone is a claim.
  * **Absent is not zero.** An unknown climb is null, an ungraded route has no
    grade. A document that reports 0 m of ascent for a route whose profile is
    missing has told a confident lie.

Duration is deliberately ABSENT. DIN 33466 rates the classic Grigna ascent at
10 hours where guidebooks say 6-8, so the figure the codebase can compute today
is one a user would not trust. An absent field invites the calibration; a wrong
one ships.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

# 2.1: adds `recommended` — which of a direction pair to suggest (the
# steep-up gentle-descent rule, owner-ratified 2026-08-27). Additive, so a
# minor version per the schema's own rule.
SCHEMA_VERSION = "2.1"

# The category vocabularies are qa's, verbatim — one name, one vocabulary,
# on both sides of the store (climb_class and difficulty_class exist in the
# qa views with these exact values; forking them under the same names made
# the legend and the document disagree about the same route). distance_class
# is the document's own (qa's corpus-wide length_class serves relations up
# to multi-day; these cuts come from the catalogue's measured quintiles
# 1.6 / 3.3 / 5.6 / 9.9 km, taken 2026-08-25).
DISTANCE_CUTS_KM = (3.0, 6.0, 10.0, 15.0)

# The share of length below which a grade is an incident rather than the
# character of the route. Proven in backend/graph/graphhopper.py.
SIGNIFICANT_SHARE = 0.05

# SAC grades in order. A route's grade is the hardest of these that covers a
# significant share, so the ORDER is the rule and a set would not do.
SAC_ORDER = [
    "hiking",
    "mountain_hiking",
    "demanding_mountain_hiking",
    "alpine_hiking",
    "demanding_alpine_hiking",
    "difficult_alpine_hiking",
]


def distance_class(distance_m: float) -> str:
    """The distance category, leading digit so a legend sorts."""
    km = distance_m / 1000.0
    if km < DISTANCE_CUTS_KM[0]:
        return "0 short (<3 km)"
    if km < DISTANCE_CUTS_KM[1]:
        return "1 half-day (3-6 km)"
    if km < DISTANCE_CUTS_KM[2]:
        return "2 day (6-10 km)"
    if km < DISTANCE_CUTS_KM[3]:
        return "3 long (10-15 km)"
    return "4 very long (>15 km)"


def climb_class(ascent_m: float | None) -> str:
    """qa.v_route's climb bands, verbatim. Unknown is its own bucket —
    absent is not zero."""
    if ascent_m is None:
        return "9 unknown"
    if ascent_m < 200:
        return "1 flat (<200 m)"
    if ascent_m < 600:
        return "2 rolling (200-600 m)"
    if ascent_m < 1200:
        return "3 hilly (600-1200 m)"
    return "4 mountain (>1200 m)"


def difficulty_class(sac_max: str | None) -> str:
    """qa.difficulty_class's vocabulary, verbatim, applied to the EXIGENT
    grade — the safety promise, never the character label (a T2 walk with
    a T4 move must read T4)."""
    if sac_max is None:
        return "0 ungraded"
    labels = {
        "hiking": "1 hiking (T1)",
        "mountain_hiking": "2 mountain (T2)",
        "demanding_mountain_hiking": "3 demanding mountain (T3)",
        "alpine_hiking": "4 alpine (T4)",
        "demanding_alpine_hiking": "5 demanding alpine (T5)",
        "difficult_alpine_hiking": "6 difficult alpine (T6)",
    }
    return labels.get(sac_max, "9 invalid tag")


#: qa.surface_class's groups, verbatim.
_PAVED = {
    "asphalt",
    "concrete",
    "paved",
    "paving_stones",
    "sett",
    "cobblestone",
    "concrete:plates",
    "metal",
    "wood",
}
_UNPAVED = {
    "compacted",
    "fine_gravel",
    "gravel",
    "pebblestone",
    "unpaved",
    "ground",
    "dirt",
    "earth",
    "grass",
    "sand",
    "mud",
    "rock",
    "stone",
    "woodchips",
    "grass_paver",
}


def surface_class(dominant_surface: str | None) -> str:
    """qa.surface_class's vocabulary, verbatim, over the dominant surface."""
    if dominant_surface is None:
        return "0 untagged"
    if dominant_surface in _PAVED:
        return "1 paved"
    if dominant_surface in _UNPAVED:
        return "2 unpaved"
    return "3 other"


def sibling_route_id(route_id: str) -> str | None:
    """The other direction's id (delegates to pipeline/ids.py at the seam
    where both modules must agree; re-exported here so document assembly
    has one import)."""
    from ids import sibling_id

    return sibling_id(route_id)


class Span(NamedTuple):
    """A length of route carrying one value of some attribute."""

    value: str | None
    length_m: float


def shares(spans: Iterable[Span]) -> dict[str, float]:
    """Length-weighted share per value, as fractions summing to 1.

    Spans with no value are counted under 'unknown' rather than dropped: a
    route that is 40% untagged should say so, not report the tagged 60% as if
    it were the whole.
    """
    totals: dict[str, float] = {}
    for value, length_m in spans:
        if length_m <= 0:
            continue
        key = value if value else "unknown"
        totals[key] = totals.get(key, 0.0) + length_m
    total = sum(totals.values())
    if total <= 0:
        return {}
    return {k: round(v / total, 4) for k, v in sorted(totals.items())}


def dominant(distribution: dict[str, float]) -> str | None:
    """The largest share, or None when nothing is known.

    Ties break on the value's name so the same route always reports the same
    dominant surface — a document that changes between identical runs is not a
    document.
    """
    known = {k: v for k, v in distribution.items() if k != "unknown"}
    if not known:
        return None
    return max(sorted(known), key=lambda k: known[k])


def exigent_grade(spans: Iterable[Span], order: Sequence[str]) -> str | None:
    """The hardest graded metre walked, however short.

    Owner rule (2026-08-20): when segments disagree, a joined attribute takes
    the MOST DEMANDING value. This is the safety twin of significant_grade —
    "a T2 walk with a T4 move in it" is character T2, exigent T4, and both
    facts are true and both are carried.
    """
    graded = [value for value, length_m in spans if value in order and length_m > 0]
    if not graded:
        return None
    return max(graded, key=order.index)


def graded_share(spans: Iterable[Span], order: Sequence[str]) -> float:
    """How much of the length carries ANY recognised grade.

    Median 16% on the first generated catalogue — which is why an "ungraded"
    verdict needs this beside it: sparse grading is a mapping fact, and without
    the share it reads as a failed join.
    """
    total = 0.0
    graded = 0.0
    for value, length_m in spans:
        if length_m <= 0:
            continue
        total += length_m
        if value in order:
            graded += length_m
    return round(graded / total, 4) if total > 0 else 0.0


def significant_grade(spans: Iterable[Span], order: Sequence[str]) -> str | None:
    """The hardest grade covering at least SIGNIFICANT_SHARE of the length.

    A plain max would let 30 m of scramble label a whole valley walk alpine.
    Values outside `order` are ignored rather than guessed at — 12 edges in this
    network carry junk in sac_scale, and one of them contains a sentence.
    """
    distribution = shares(spans)
    significant = [
        value
        for value, share in distribution.items()
        if value in order and share >= SIGNIFICANT_SHARE
    ]
    if not significant:
        return None
    return max(significant, key=order.index)


def quality_warnings(
    *,
    pieces: int,
    edges_without_profile: int,
    matched_fraction: float | None,
    places: int,
) -> list[str]:
    """What a reader should know before trusting this document.

    Carried IN the document rather than filtered out before it: a route that
    this network holds in three pieces is still a real route, and the reader
    deciding whether to show it needs to know which one it is.
    """
    warnings: list[str] = []
    # Being in pieces is NOT a warning any more (owner rule 2026-08-27): the
    # multi-piece matched floor holds the clipped fragments back entirely, so
    # an emitted multi-piece document is one the policy chose to offer, and
    # its brokenness is a described property — `continuity` carries pieces
    # and reason, and the chat gate (warnings = 0) must not re-hide what the
    # floor ratified. Fragments still warn below, via matched_fraction.
    if edges_without_profile:
        warnings.append(
            f"{edges_without_profile} edges have no altitude profile, so ascent "
            "and descent are unknown rather than partial"
        )
    if matched_fraction is not None and matched_fraction < 0.2:
        warnings.append(
            f"only {matched_fraction:.0%} of the relation's ways are in this "
            "network: this is a fragment of the route, not the route"
        )
    if not places:
        warnings.append("no named place within 100 m of the line")
    return warnings


def build_document(
    *,
    route_id: str,
    kind: str,
    shape: str,
    identity: dict[str, Any],
    geometry: dict[str, Any],
    bbox: Sequence[float],
    distance_m: float,
    ascent_m: float | None,
    descent_m: float | None,
    lowest_m: float | None,
    highest_m: float | None,
    profile: dict[str, list[float]] | None,
    surface_spans: Iterable[Span],
    sac_spans: Iterable[Span],
    pieces: int,
    edges_without_profile: int,
    matched_fraction: float | None,
    places: list[dict[str, Any]],
    terminals: list[dict[str, Any]],
    provenance: dict[str, Any],
    direction: str | None = None,
    recommended: bool | None = None,
    continuity_reason: str | None = None,
    divergence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One route, as the artefact everything downstream reads.

    Field order is stable and the whole document is JSON-serialisable, so two
    runs of the same route produce byte-identical files and a diff means the
    data moved.
    """
    surface = shares(surface_spans)
    sac_spans = list(sac_spans)
    difficulty = shares(sac_spans)
    return {
        "schema_version": SCHEMA_VERSION,
        "id": route_id,
        "kind": kind,
        # loop/destination are CONSTRUCTED (the generator drew them that way);
        # circular/linear are MEASURED on a mapped route (export/shape.py).
        # Distinct pairs, so a classifier bug can never impersonate intent.
        "shape": shape,
        "identity": identity,
        "geometry": geometry,
        "bbox": [round(v, 6) for v in bbox],
        "measures": {
            "distance_m": round(distance_m, 1),
            "ascent_m": None if ascent_m is None else round(ascent_m, 1),
            "descent_m": None if descent_m is None else round(descent_m, 1),
            "lowest_m": None if lowest_m is None else round(lowest_m, 1),
            "highest_m": None if highest_m is None else round(highest_m, 1),
            # duration is deliberately absent: see the module docstring.
        },
        "profile": profile,
        "surface": {"distribution": surface, "dominant": dominant(surface)},
        "difficulty": {
            # character: the label a route wears. exigent: what you must be
            # able to handle. Both true, both carried (owner rule 2026-08-20).
            "sac_scale": significant_grade(sac_spans, SAC_ORDER),
            "sac_max": exigent_grade(sac_spans, SAC_ORDER),
            "graded_share": graded_share(sac_spans, SAC_ORDER),
            "distribution": difficulty,
            "rule": "sac_scale: hardest grade covering at least 5% of the "
            "length; sac_max: hardest graded metre, any length",
        },
        "continuity": {
            "pieces": pieces,
            "continuous": pieces == 1,
            # Why a broken route is broken: at our bbox (coverage_edge, a
            # fact about our bounds) or inside coverage (network_gap). null
            # when continuous. Carried, never filtered on.
            "reason": None if pieces == 1 else (continuity_reason or "unknown"),
        },
        # Which direction of travel this document describes (start/end
        # contract §5); its sibling's id from day one, so the :rev documents
        # land as pure additions.
        "direction": direction,
        "reverse_of": sibling_route_id(route_id),
        # Of a direction pair, the one to suggest: the steep side UP, the
        # gentle side down (owner rule 2026-08-27, computed from the
        # profile's climbing gradients). Null when the route is undirected,
        # unpaired, or has no profile to judge from.
        "recommended": recommended,
        "terminals": terminals,
        "categories": {
            "distance_class": distance_class(distance_m),
            "climb_class": climb_class(ascent_m),
            "difficulty_class": difficulty_class(exigent_grade(sac_spans, SAC_ORDER)),
            "surface_class": surface_class(dominant(surface)),
        },
        "divergence": divergence,
        "places": places,
        "quality": {
            "warnings": quality_warnings(
                pieces=pieces,
                edges_without_profile=edges_without_profile,
                matched_fraction=matched_fraction,
                places=len(places),
            ),
            "matched_fraction": matched_fraction,
            "edges_without_profile": edges_without_profile,
        },
        "provenance": provenance,
    }
