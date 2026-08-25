"""Vertical levels: the guard that keeps a repair on the ground it belongs to.

Noding is topological — two ways that cross without sharing a node are a
bridge over a road, and welding them routes walkers through the air (the
pgr_nodeNetwork trap, sql/v2 baseline). The gap REPAIRS carried no such
guard: they weld on 2 m geodesic proximity alone, and a bridge deck passes
within 2 m horizontally of the road beneath it. A deck's loose end welded
onto the road below is the same defect the noding rule exists to prevent,
introduced by the repair pass instead of the build.

The level of a way is what OSM says about its vertical position: the
``layer`` tag (default 0) and whether it is a ``bridge`` or ``tunnel``.
Two ends may weld only when they run at the SAME level; anything else is a
grade-separated crossing and stays a judgement queue, never an auto-repair.
"""

from __future__ import annotations

Level = tuple[str, bool, bool]


def level_of(tags: dict | None) -> Level:
    """(layer, bridge?, tunnel?) — the vertical position a way claims.

    ``layer`` defaults to "0" (OSM's own default); ``bridge``/``tunnel``
    count as present for any value except an explicit "no".
    """
    tags = tags or {}
    return (
        tags.get("layer") or "0",
        tags.get("bridge") not in (None, "no"),
        tags.get("tunnel") not in (None, "no"),
    )


def levels_compatible(a: list[Level], b: list[Level]) -> bool:
    """May ends carrying these levels be welded?

    Each side brings the levels of its incident edges (a dangle brings one;
    a junction brings several). A weld is safe when SOME edge on each side
    runs at the same level — the joined ground exists. Empty on either side
    is treated as compatible: a vertex with no edges is a stale finding the
    weld itself will refuse for other reasons, not a grade separation.
    """
    if not a or not b:
        return True
    return any(la == lb for la in a for lb in b)


def level_text(level: Level) -> str:
    """The level as the QA note carries it: '0', '1+bridge', '-1+tunnel'."""
    layer, bridge, tunnel = level
    parts = [layer]
    if bridge:
        parts.append("bridge")
    if tunnel:
        parts.append("tunnel")
    return "+".join(parts)
