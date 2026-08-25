"""The v2 route id: stable across rebuilds, direction-aware, minted only here."""

from __future__ import annotations

import pytest

from ids import (
    DIRECTED_SHAPES,
    canonical_piece,
    digest,
    forward_is_stored,
    route_id,
    sibling_id,
)

LINE = [(9.30001, 45.80001), (9.31, 45.81), (9.32, 45.82)]


def test_rounding_absorbs_sub_metre_noise():
    nudged = [(x + 0.000001, y - 0.000001) for x, y in LINE]
    assert digest([LINE]) == digest([nudged])


def test_a_real_reroute_is_a_new_id():
    rerouted = [*LINE[:-1], (9.34, 45.83)]
    assert digest([LINE]) != digest([rerouted])


def test_direction_is_normalised_into_the_digest():
    assert digest([LINE]) == digest([list(reversed(LINE))])


def test_consecutive_duplicates_after_rounding_collapse():
    dense = [LINE[0], (9.300011, 45.800012), *LINE[1:]]
    assert digest([dense]) == digest([LINE])


def test_piece_order_cannot_rename_a_multipiece_route():
    a = [(9.3, 45.8), (9.31, 45.81)]
    b = [(9.5, 45.9), (9.51, 45.91)]
    assert digest([a, b]) == digest([b, a])


def test_directed_shapes_carry_their_direction():
    for shape in DIRECTED_SHAPES:
        assert route_id([LINE], shape).endswith("-fwd")
        assert route_id([LINE], shape, "rev").endswith("-rev")
    assert route_id([LINE], "destination") == route_id([LINE], "out_and_back")
    assert route_id([LINE], "destination") == f"vv2-{digest([LINE])}"


def test_a_directed_shape_refuses_a_missing_direction():
    with pytest.raises(ValueError):
        route_id([LINE], "loop", None)


def test_the_fwd_sense_comes_from_geometry_alone():
    # Whichever orientation is stored, exactly one of the pair reads as fwd —
    # and it is the same one however the network was rebuilt, because nothing
    # but coordinates goes in. (The amendment to PR #32: edge ids are
    # reassigned every rebuild and must not decide this.)
    assert forward_is_stored(LINE) != forward_is_stored(list(reversed(LINE)))


def test_a_palindrome_out_and_back_reads_fwd_harmlessly():
    there_and_back = [*LINE, *reversed(LINE[:-1])]
    assert forward_is_stored(there_and_back)


def test_canonical_piece_is_the_lexicographic_minimum():
    assert canonical_piece(LINE) == canonical_piece(list(reversed(LINE)))
    assert canonical_piece(LINE) == min(
        tuple((round(x, 5), round(y, 5)) for x, y in LINE),
        tuple(reversed([(round(x, 5), round(y, 5)) for x, y in LINE])),
    )


def test_siblings_swap_and_undirected_has_none():
    fwd = route_id([LINE], "loop")
    assert sibling_id(fwd) == fwd[:-4] + "-rev"
    assert sibling_id(sibling_id(fwd)) == fwd
    assert sibling_id(route_id([LINE], "destination")) is None


def test_the_id_format_is_the_contract_pattern():
    import re

    pattern = re.compile(r"^vv2-[0-9a-f]{16}(-(fwd|rev))?$")
    assert pattern.match(route_id([LINE], "loop"))
    assert pattern.match(route_id([LINE], "destination"))
