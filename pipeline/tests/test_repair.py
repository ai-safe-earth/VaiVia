"""The repair arithmetic, without a database.

These pin the decisions that are easy to undo by accident: a weld moves an end
and only an end, a split refuses rather than emitting a zero-length piece, and
the loose end itself becomes the shared coordinate so the join is exact rather
than nearly.
"""

from __future__ import annotations

from typing import ClassVar

from topology.repair import collapses, snap_endpoint, split_at_point, split_ring

# A short way running east, and a loose end 1.5 m north-ish of its middle.
LINE = [(9.40, 45.86), (9.41, 45.86), (9.42, 45.86)]


class TestSnapEndpoint:
    def test_moves_the_start_when_it_matches(self):
        out = snap_endpoint(LINE, (9.40, 45.86), (9.399, 45.861))
        assert out[0] == (9.399, 45.861)
        assert out[1:] == LINE[1:]

    def test_moves_the_end_when_it_matches(self):
        out = snap_endpoint(LINE, (9.42, 45.86), (9.421, 45.859))
        assert out[-1] == (9.421, 45.859)
        assert out[:-1] == LINE[:-1]

    def test_leaves_an_interior_coordinate_alone(self):
        """The welded vertex is an END of this edge by definition. Touching a
        matching interior point would deform the line somewhere nobody looked."""
        out = snap_endpoint(LINE, (9.41, 45.86), (9.999, 45.999))
        assert out == LINE

    def test_unchanged_when_neither_end_matches(self):
        """The caller reads 'unchanged' as 'this finding is stale' — an earlier
        repair in the same pass already moved the vertex."""
        assert snap_endpoint(LINE, (1.0, 1.0), (2.0, 2.0)) == LINE

    def test_a_closed_ring_moves_both_ends_together(self):
        ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
        out = snap_endpoint(ring, (0.0, 0.0), (0.1, 0.1))
        assert out[0] == (0.1, 0.1)
        assert out[-1] == (0.1, 0.1)

    def test_empty_input_is_returned_unchanged(self):
        assert snap_endpoint([], (0.0, 0.0), (1.0, 1.0)) == []


class TestCollapses:
    def test_two_distinct_points_do_not_collapse(self):
        assert not collapses([(0.0, 0.0), (1.0, 1.0)])

    def test_repeated_points_collapse(self):
        assert collapses([(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)])

    def test_a_single_point_collapses(self):
        assert collapses([(0.0, 0.0)])

    def test_empty_collapses(self):
        assert collapses([])


class TestSplitRing:
    """A self-loop is real ground that routing cannot enter. Halving it keeps
    every metre; the first version of the repair deleted these and lost 26.3 km
    of network, including a 640 m loop way."""

    RING: ClassVar[list[tuple[float, float]]] = [
        (0.0, 0.0),
        (0.0, 0.01),
        (0.01, 0.01),
        (0.01, 0.0),
        (0.0, 0.0),
    ]

    def test_halves_share_the_new_midpoint(self):
        halves = split_ring(self.RING)
        assert halves is not None
        first, second, midpoint = halves
        assert first[-1] == midpoint
        assert second[0] == midpoint

    def test_the_ring_still_starts_and_ends_where_it_did(self):
        """Both ends stay on the original vertex, so the loop still closes and
        no connection is lost by splitting it."""
        halves = split_ring(self.RING)
        assert halves is not None
        first, second, _ = halves
        assert first[0] == self.RING[0]
        assert second[-1] == self.RING[-1]

    def test_neither_half_collapses(self):
        halves = split_ring(self.RING)
        assert halves is not None
        first, second, _ = halves
        assert not collapses(first)
        assert not collapses(second)

    def test_refuses_a_ring_too_small_to_halve(self):
        assert split_ring([(0.0, 0.0), (0.0, 0.0)]) is None

    def test_refuses_a_degenerate_ring(self):
        """A zero-length ring connects nothing to nothing; the short-edge rule
        deletes it rather than splitting a point in two."""
        assert split_ring([(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]) is None


class TestSplitAtPoint:
    def test_splits_at_the_projection_and_shares_the_loose_end(self):
        halves = split_at_point(LINE, (9.41, 45.8601))
        assert halves is not None
        first, second = halves
        # The loose end itself is the shared coordinate: the join is exact, not
        # "within tolerance of each other".
        assert first[-1] == (9.41, 45.8601)
        assert second[0] == (9.41, 45.8601)
        assert first[0] == LINE[0]
        assert second[-1] == LINE[-1]

    def test_refuses_when_the_projection_lands_on_the_start(self):
        """That is the pair or junction case, which another rule owns —
        splitting there would create a zero-length piece."""
        assert split_at_point(LINE, (9.399, 45.86)) is None

    def test_refuses_when_the_projection_lands_on_the_end(self):
        assert split_at_point(LINE, (9.421, 45.86)) is None

    def test_refuses_a_line_with_fewer_than_two_points(self):
        assert split_at_point([(9.4, 45.86)], (9.4, 45.86)) is None

    def test_both_halves_keep_the_original_shape(self):
        """A split is a cut, not a redraw: every original coordinate survives in
        one half or the other."""
        halves = split_at_point(LINE, (9.415, 45.8601))
        assert halves is not None
        first, second = halves
        assert LINE[0] in first
        assert LINE[-1] in second
        assert LINE[1] in first + second
