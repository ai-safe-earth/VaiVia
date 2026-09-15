"""The vertical-level guard: what may weld, and what is a grade separation."""

from __future__ import annotations

from topology.levels import level_of, level_text, levels_compatible


def test_the_default_is_ground_level():
    assert level_of({}) == ("0", False, False)
    assert level_of(None) == ("0", False, False)
    assert level_of({"highway": "path"}) == ("0", False, False)


def test_bridge_and_tunnel_count_for_any_value_but_an_explicit_no():
    assert level_of({"bridge": "yes"}) == ("0", True, False)
    assert level_of({"bridge": "viaduct"}) == ("0", True, False)
    assert level_of({"bridge": "no"}) == ("0", False, False)
    assert level_of({"tunnel": "culvert"}) == ("0", False, True)
    assert level_of({"layer": "1", "bridge": "yes"}) == ("1", True, False)


def test_same_level_welds_and_a_grade_separation_does_not():
    ground = level_of({})
    deck = level_of({"layer": "1", "bridge": "yes"})
    assert levels_compatible([ground], [ground])
    assert not levels_compatible([deck], [ground])
    # A junction carrying BOTH a ground way and a deck can take a ground weld:
    # the joined ground exists.
    assert levels_compatible([ground], [deck, ground])


def test_an_empty_side_is_not_a_grade_separation():
    # A vertex with no edges is a stale finding, refused elsewhere for its
    # own reason — calling it a level mismatch would mislabel the refusal.
    assert levels_compatible([], [level_of({})])


def test_level_text_reads_like_the_tags():
    assert level_text(level_of({})) == "0"
    assert level_text(level_of({"layer": "1", "bridge": "yes"})) == "1+bridge"
    assert level_text(level_of({"layer": "-1", "tunnel": "yes"})) == "-1+tunnel"
