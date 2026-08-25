"""The terminal rules that need no database: seasons, months, endpoints."""

from __future__ import annotations

from datetime import date

from export.terminals import endpoints_of, months_of, seasons_block


def row(start_class, service=None):
    start, end = service or (None, None)
    return (start_class, "Somewhere", False, 120.0, start, end)


# ── seasons ──────────────────────────────────────────────────────────────────


def test_nothing_in_reach_is_reachable_never():
    assert seasons_block([]) == {
        "spring": False,
        "summer": False,
        "autumn": False,
        "winter": False,
        "unverified": False,
    }


def test_any_timeless_start_means_all_seasons_verified():
    # The inversion of the hazard rule: no gate tag is evidence of no gate.
    rows = [row("station", (date(2026, 7, 26), date(2026, 12, 12))), row("parking")]
    assert seasons_block(rows) == {
        "spring": True,
        "summer": True,
        "autumn": True,
        "winter": True,
        "unverified": False,
    }


def test_transit_only_takes_the_measured_span_and_stays_unverified():
    # trenord's real span: proves summer, autumn and (through December's
    # first weeks) winter — and says NOTHING either way about spring. The
    # feed's end is a publication horizon, not a closure: unverified.
    rows = [row("station", (date(2026, 7, 26), date(2026, 12, 12)))]
    assert seasons_block(rows) == {
        "spring": False,
        "summer": True,
        "autumn": True,
        "winter": True,
        "unverified": True,
    }


def test_a_station_never_bypasses_the_calendar():
    # The regression that motivated this file: 'station' sat in the timeless
    # set, so every rail stop read year-round-verified and the whole 0006
    # service-span work was dead code.
    rows = [row("station", (date(2026, 6, 1), date(2026, 8, 31)))]
    seasons = seasons_block(rows)
    assert seasons["winter"] is False
    assert seasons["unverified"] is True


def test_an_unclassified_start_proves_nothing():
    # NULL start_class is unclassified, not timeless: missing data must
    # never read better than measured data.
    assert seasons_block([row(None)]) == {
        "spring": False,
        "summer": False,
        "autumn": False,
        "winter": False,
        "unverified": True,
    }


def test_an_uncalendared_stop_proves_nothing_either():
    assert seasons_block([row("bus_stop")])["unverified"] is True
    assert seasons_block([row("bus_stop")])["summer"] is False


# ── months ───────────────────────────────────────────────────────────────────


def test_months_wrap_the_year_end():
    assert months_of(date(2026, 11, 20), date(2027, 2, 10)) == {11, 12, 1, 2}


def test_a_year_long_span_touches_every_month():
    assert months_of(date(2026, 3, 1), date(2027, 3, 1)) == set(range(1, 13))


# ── endpoints ────────────────────────────────────────────────────────────────

LINE = {
    "type": "LineString",
    "coordinates": [[9.30, 45.80], [9.35, 45.85], [9.40, 45.90]],
}


def test_a_single_line_traverse_keeps_its_own_ends():
    assert endpoints_of(LINE, "linear") == [(9.30, 45.80), (9.40, 45.90)]
    assert endpoints_of(LINE, "circular") == [(9.30, 45.80)]


def test_multipiece_terminals_are_the_farthest_pair_not_storage_order():
    # A traverse in three pieces, stored with the MIDDLE fragment first —
    # exactly how ST_LineMerge can emit it. Storage order once put both
    # terminals on interior break points (one live route tested the same
    # junction twice); the farthest pair is the true A and B.
    broken = {
        "type": "MultiLineString",
        "coordinates": [
            [[9.34, 45.84], [9.36, 45.86]],  # middle fragment first
            [[9.30, 45.80], [9.33, 45.83]],  # the true A end
            [[9.37, 45.87], [9.40, 45.90]],  # the true B end
        ],
    }
    assert endpoints_of(broken, "linear") == [(9.30, 45.80), (9.40, 45.90)]


def test_multipiece_terminals_ignore_piece_order():
    pieces = [
        [[9.30, 45.80], [9.33, 45.83]],
        [[9.34, 45.84], [9.36, 45.86]],
        [[9.37, 45.87], [9.40, 45.90]],
    ]
    a = {"type": "MultiLineString", "coordinates": pieces}
    b = {"type": "MultiLineString", "coordinates": list(reversed(pieces))}
    assert endpoints_of(a, "linear") == endpoints_of(b, "linear")


def test_a_ring_in_pieces_gets_one_deterministic_terminal():
    pieces = [
        [[9.35, 45.85], [9.30, 45.80]],
        [[9.30, 45.80], [9.40, 45.90]],
        [[9.40, 45.90], [9.35, 45.85]],
    ]
    a = {"type": "MultiLineString", "coordinates": pieces}
    b = {"type": "MultiLineString", "coordinates": list(reversed(pieces))}
    assert endpoints_of(a, "circular") == endpoints_of(b, "circular") == [(9.30, 45.80)]
