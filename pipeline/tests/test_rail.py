"""The rail matrix's pure half: leg extraction and the all-pairs closure."""

from curate.rail import all_pairs, leg_minutes, parse_hms


def st(trip, seq, stop, arr, dep):
    return {
        "trip_id": trip,
        "stop_sequence": str(seq),
        "stop_id": stop,
        "arrival_time": arr,
        "departure_time": dep,
    }


def test_parse_hms_handles_gtfs_quirks():
    assert parse_hms("08:30:00") == 8 * 3600 + 30 * 60
    assert parse_hms("25:10:00") == 25 * 3600 + 10 * 60  # past-midnight service
    assert parse_hms("") is None and parse_hms("8h30") is None


def test_fastest_service_speaks_for_a_leg():
    legs = leg_minutes(
        [
            st("t1", 1, "A", "08:00:00", "08:00:00"),
            st("t1", 2, "B", "08:12:00", "08:13:00"),
            st("t2", 1, "A", "09:00:00", "09:00:00"),
            st("t2", 2, "B", "09:09:00", "09:09:00"),  # the express
        ]
    )
    assert legs[("A", "B")] == 9.0


def test_all_pairs_closes_a_change_of_trains():
    legs = {("A", "B"): 10.0, ("B", "C"): 15.0}
    matrix = all_pairs(["A", "B", "C"], legs)
    assert matrix[("A", "C")] == 25.0  # via B, no transfer penalty by design
    assert ("C", "A") not in matrix  # the timetable is directed
