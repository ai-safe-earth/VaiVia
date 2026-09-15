"""Choosing where an out-and-back goes. No database — pure functions."""

from __future__ import annotations

import pytest

from draw.destinations import (
    Destination,
    crow_band,
    interest,
    rank,
    route_name,
)


def dest(
    place_id: str = "poi:n1",
    kind: str = "peak",
    name: str | None = "Grignone",
    vertex_id: int = 1,
    crow_m: float = 2000.0,
) -> Destination:
    return Destination(place_id, kind, name, vertex_id, crow_m)


def test_the_band_scales_with_the_target():
    low5, high5 = crow_band(5_000)
    _low15, high15 = crow_band(15_000)

    assert high15 == 3 * high5
    assert low5 < high5
    # Generous by design: measured wander is 1.4-3.3, so the band must span it.
    assert high5 / low5 == pytest.approx(3.5 / 1.1, rel=0.01)


def test_a_named_peak_outranks_an_unnamed_one():
    named = dest("poi:n1", "peak", "Grignone")
    unnamed = dest("poi:n2", "peak", None)

    assert interest(named) > interest(unnamed)


def test_a_named_view_outranks_a_named_spring():
    # The owner's examples ("views, peak...") are the archetype; a spring is a
    # waypoint, not a destination.
    view = dest("poi:n1", "viewpoint", "Belvedere")
    spring = dest("poi:n2", "spring", "Fonte")

    assert interest(view) > interest(spring)


def test_an_unnamed_hut_still_beats_a_named_spring():
    # Weights are not drowned by the name bonus: kind carries real signal.
    hut = dest("poi:n1", "hut", None)
    spring = dest("poi:n2", "spring", "Fonte")

    assert interest(hut) > interest(spring)


def test_rank_is_deterministic_and_bounded():
    pool = [
        dest("poi:n3", "spring", "Fonte", crow_m=1000),
        dest("poi:n1", "peak", "Grignone", crow_m=2000),
        dest("poi:n2", "hut", "Rifugio Elisa", crow_m=1500),
    ]

    first = rank(pool, top=2)
    second = rank(list(reversed(pool)), top=2)

    assert first == second  # same pool, same order: route ids depend on this
    assert [d.kind for d in first] == ["peak", "hut"]


def test_rank_ties_break_on_distance_then_id():
    near = dest("poi:n1", "peak", "A", crow_m=1000)
    far = dest("poi:n2", "peak", "B", crow_m=3000)

    assert rank([far, near], top=2) == [near, far]


def test_the_route_is_named_after_its_destination_or_not_at_all():
    assert route_name(dest(name="Rifugio Elisa")) == "To Rifugio Elisa"
    # "To unnamed viewpoint" is worse than silence.
    assert route_name(dest(name=None)) is None


def test_an_unknown_kind_has_zero_interest():
    assert interest(dest(kind="helipad", name=None)) == 0.0
