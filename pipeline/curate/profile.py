"""Ascent and descent from an altitude profile. Pure, so it is tested.

The sampling is one SQL statement over a whole table and belongs in PostGIS.
This does not: deciding what a missing sample means is per-feature branching,
it is the rule most likely to be got quietly wrong, and it is exactly what a
unit test is for (CLAUDE.md, division of labour).

Two rules, both deliberate:

  * **No noise threshold.** Measured 2026-08-20: binned by point spacing, the
    median |dz| of a bilinear profile is 0.12 m at sub-2 m spacing and rises
    with distance without ever plateauing, and the median implied slope holds
    at 9-14% across every band. There is no noise floor to subtract, so
    subtracting one would only remove real terrain. The full table is in
    sql/0008_elevation.sql. (Under nearest-neighbour sampling there IS an
    artefact to remove -- which is why the sampling is bilinear instead.)

  * **A gap makes the climb unknown, not smaller.** One missing sample and the
    whole edge returns None. Skipping the gap and summing the rest would report
    a smaller ascent than the ground has, with nothing to say it was partial --
    the failure mode the pipeline avoids everywhere else by returning "I do not
    know" rather than a confident understatement.
"""

from __future__ import annotations

from collections.abc import Sequence


def ascent_descent(
    profile: Sequence[float | None] | None,
) -> tuple[float, float] | None:
    """Metres climbed and metres dropped along the profile, in its own order.

    Returns None when the profile is absent, too short to have a gradient, or
    has any missing sample. Reversing the profile swaps the two numbers, which
    is why they are stored as a pair rather than as a single net figure.
    """
    if not profile or len(profile) < 2:
        return None
    if any(z is None for z in profile):
        return None

    ascent = 0.0
    descent = 0.0
    previous = profile[0]
    for z in profile[1:]:
        delta = z - previous  # type: ignore[operator]
        if delta > 0:
            ascent += delta
        else:
            descent -= delta
        previous = z
    return ascent, descent
