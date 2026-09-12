"""The rail matrix: minutes by train between in-region stations, from GTFS.

The timetable is the measurement — no speeds, no guesses. Consecutive stop
pairs across every trip give per-leg times (the fastest service speaks for
a leg); Floyd–Warshall over the in-region stations closes journeys that
change trains, WITHOUT a transfer penalty — a known simplification, noted
in the pack manifest: the figure is track time, a floor on the journey.

Pure functions do the work so the tests need no database or zip.

Run from pipeline/ (load.gtfs done, zip still on disk):
    uv run python -m curate.rail --zip data/trenord_gtfs.zip --feed trenord
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import time
import uuid
import zipfile

from core import connect


def parse_hms(value: str) -> int | None:
    """GTFS HH:MM:SS (HH may exceed 23) as seconds; None for blank/junk."""
    parts = value.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = (int(p) for p in parts)
    except ValueError:
        return None
    return h * 3600 + m * 60 + s


def leg_minutes(stop_times: list[dict[str, str]]) -> dict[tuple[str, str], float]:
    """Fastest observed minutes per DIRECTED consecutive-stop leg."""
    by_trip: dict[str, list[tuple[int, str, int | None, int | None]]] = {}
    for st in stop_times:
        try:
            seq = int(st["stop_sequence"])
        except (KeyError, ValueError):
            continue
        by_trip.setdefault(st["trip_id"], []).append(
            (
                seq,
                st["stop_id"],
                parse_hms(st.get("arrival_time", "")),
                parse_hms(st.get("departure_time", "")),
            )
        )
    legs: dict[tuple[str, str], float] = {}
    for stops in by_trip.values():
        stops.sort()
        for (_s1, a, _arr1, dep1), (_s2, b, arr2, _dep2) in zip(
            stops, stops[1:], strict=False
        ):
            if dep1 is None or arr2 is None or arr2 < dep1 or a == b:
                continue
            minutes = (arr2 - dep1) / 60.0
            key = (a, b)
            if key not in legs or minutes < legs[key]:
                legs[key] = minutes
    return legs


def all_pairs(
    stations: list[str], legs: dict[tuple[str, str], float]
) -> dict[tuple[str, str], float]:
    """Floyd–Warshall over the station set — small by construction."""
    index = {s: i for i, s in enumerate(stations)}
    n = len(stations)
    inf = float("inf")
    d = [[inf] * n for _ in range(n)]
    for i in range(n):
        d[i][i] = 0.0
    for (a, b), minutes in legs.items():
        if a in index and b in index and minutes < d[index[a]][index[b]]:
            d[index[a]][index[b]] = minutes
    for k in range(n):
        dk = d[k]
        for i in range(n):
            dik = d[i][k]
            if dik == inf:
                continue
            row = d[i]
            for j in range(n):
                if dik + dk[j] < row[j]:
                    row[j] = dik + dk[j]
    return {
        (stations[i], stations[j]): d[i][j]
        for i in range(n)
        for j in range(n)
        if i != j and d[i][j] != inf
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True)
    parser.add_argument("--feed", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_id = f"rail-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()

    with zipfile.ZipFile(args.zip) as z, z.open("stop_times.txt") as fh:
        stop_times = list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))
    legs = leg_minutes(stop_times)
    print(f"{len(legs):,} directed legs from {len(stop_times):,} stop_times")

    with connect() as conn:
        rows = conn.execute(
            "SELECT stop_id FROM staging.gtfs_stop WHERE feed = %s", (args.feed,)
        ).fetchall()
        stations = sorted(stop_id for (stop_id,) in rows)
        matrix = all_pairs(stations, legs)
        print(
            f"{len(stations)} in-region stations, {len(matrix):,} reachable pairs, "
            f"{time.monotonic() - started:.0f}s"
        )
        if args.dry_run:
            print("--dry-run: nothing written.")
            return
        conn.execute(
            "INSERT INTO provenance.build_run (run_id, stage, parameters) "
            "VALUES (%s, 'rail', %s)",
            (run_id, json.dumps({"builder": "curate.rail", "feed": args.feed})),
        )
        conn.execute("TRUNCATE staging.rail_min")
        with (
            conn.cursor() as cur,
            cur.copy(
                "COPY staging.rail_min (feed_stop_a, feed_stop_b, minutes, run_id)"
                " FROM STDIN"
            ) as copy,
        ):
            for (a, b), minutes in sorted(matrix.items()):
                copy.write_row(
                    (f"{args.feed}:{a}", f"{args.feed}:{b}", minutes, run_id)
                )
        conn.execute(
            "UPDATE provenance.build_run SET finished_at = now(), counts = %s "
            "WHERE run_id = %s",
            (json.dumps({"pairs": len(matrix), "stations": len(stations)}), run_id),
        )
    print(f"done (run {run_id})")


if __name__ == "__main__":
    main()
