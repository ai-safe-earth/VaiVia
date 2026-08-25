"""Load GTFS stops with evidence of service into staging.gtfs_stop.

The start rule needs "reachable without a car", and a stop only proves that if
something actually stops there — so each stop carries its stop_time count, and
zero-service stops are loaded but flagged by that zero rather than dropped
(a count of them is a data-quality fact about the feed).

Plain zipfile+csv rather than gtfs-kit's full frame model: the two questions
asked here (where are the stops, how many stop_times each) do not need one.

Run from pipeline/:
    uv run python -m load.gtfs --zip data/trenord_gtfs.zip --feed trenord
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import uuid
import zipfile

from shapely.geometry import Point

from core import REGIONS, connect
from load.osm import ewkb4326, regions_for


def read(z: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    if name not in z.namelist():
        return []
    with z.open(name) as fh:
        return list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True)
    parser.add_argument("--feed", required=True, help="feed label, e.g. trenord")
    args = parser.parse_args()

    run_id = f"load-gtfs-{uuid.uuid4().hex[:8]}"
    z = zipfile.ZipFile(args.zip)
    stops = read(z, "stops.txt")
    stop_times = read(z, "stop_times.txt")

    trips_per_stop: dict[str, int] = {}
    for st in stop_times:
        sid = st["stop_id"]
        trips_per_stop[sid] = trips_per_stop.get(sid, 0) + 1

    # WHEN the service runs, not only whether. n_trips alone was
    # calendar-blind: a stop served daily and one served two summer months
    # counted identically, and "you can get here by bus" is precisely the
    # claim a user would strand themselves on. Per stop: the date span over
    # every service its trips run under — calendar.txt ranges widened by
    # calendar_dates.txt added-service exceptions (type 1; removals narrow
    # nothing, an off day inside a season is still that season's service).
    trips = read(z, "trips.txt")
    calendar = read(z, "calendar.txt")
    calendar_dates = read(z, "calendar_dates.txt")
    service_span: dict[str, tuple[str, str]] = {
        c["service_id"]: (c["start_date"], c["end_date"]) for c in calendar
    }
    for cd in calendar_dates:
        if cd.get("exception_type") != "1":
            continue
        day = cd["date"]
        lo, hi = service_span.get(cd["service_id"], (day, day))
        service_span[cd["service_id"]] = (min(lo, day), max(hi, day))
    service_of_trip = {t["trip_id"]: t["service_id"] for t in trips}
    span_per_stop: dict[str, tuple[str, str]] = {}
    for st in stop_times:
        span = service_span.get(service_of_trip.get(st["trip_id"], ""), None)
        if span is None:
            continue
        sid = st["stop_id"]
        lo, hi = span_per_stop.get(sid, span)
        span_per_stop[sid] = (min(lo, span[0]), max(hi, span[1]))

    def as_date(yyyymmdd: str) -> str:
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"

    rows = []
    for s in stops:
        lat, lon = float(s["stop_lat"]), float(s["stop_lon"])
        regions = regions_for(lat, lon)
        if not regions:
            continue
        rows.append(
            {
                "stop_id": s["stop_id"],
                "name": s.get("stop_name"),
                "geom": ewkb4326(Point(lon, lat).wkb_hex),
                "n_trips": trips_per_stop.get(s["stop_id"], 0),
                "service_start": (
                    as_date(span_per_stop[s["stop_id"]][0])
                    if s["stop_id"] in span_per_stop
                    else None
                ),
                "service_end": (
                    as_date(span_per_stop[s["stop_id"]][1])
                    if s["stop_id"] in span_per_stop
                    else None
                ),
                "regions": regions,
            }
        )

    with connect() as conn:
        conn.execute(
            "INSERT INTO provenance.build_run (run_id, stage, parameters) VALUES (%s, 'load', %s)",
            (run_id, json.dumps({"feed": args.feed, "loader": "gtfs"})),
        )
        conn.execute("DELETE FROM staging.gtfs_stop WHERE feed = %s", (args.feed,))
        with (
            conn.cursor() as cur,
            cur.copy(
                "COPY staging.gtfs_stop (feed, stop_id, name, geom, n_trips,"
                " service_start, service_end, regions, run_id) FROM STDIN"
            ) as copy,
        ):
            for r in rows:
                copy.write_row(
                    (
                        args.feed,
                        r["stop_id"],
                        r["name"],
                        r["geom"],
                        r["n_trips"],
                        r["service_start"],
                        r["service_end"],
                        r["regions"],
                        run_id,
                    )
                )
        served = sum(1 for r in rows if r["n_trips"] > 0)
        dated = sum(1 for r in rows if r["service_start"])
        counts = {
            "stops_in_region": len(rows),
            "with_service": served,
            "with_service_dates": dated,
        }
        conn.execute(
            "UPDATE provenance.build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (json.dumps(counts), run_id),
        )

    print(f"{args.feed}: {len(rows)} stops in-region, {served} with service")
    for name in sorted({r["name"] for r in rows if r["n_trips"] > 0})[:12]:
        print("   ", name)
    unused = [k for k in REGIONS if not any(k in r["regions"] for r in rows)]
    if unused:
        print(f"regions with no {args.feed} stop at all: {unused}")


if __name__ == "__main__":
    main()
