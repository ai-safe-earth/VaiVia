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
                "regions": regions,
            }
        )

    with connect() as conn:
        conn.execute(
            "INSERT INTO build_run (run_id, stage, parameters) VALUES (%s, 'load', %s)",
            (run_id, json.dumps({"feed": args.feed, "loader": "gtfs"})),
        )
        conn.execute("DELETE FROM staging.gtfs_stop WHERE feed = %s", (args.feed,))
        with (
            conn.cursor() as cur,
            cur.copy(
                "COPY staging.gtfs_stop (feed, stop_id, name, geom, n_trips,"
                " regions, run_id) FROM STDIN"
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
                        r["regions"],
                        run_id,
                    )
                )
        served = sum(1 for r in rows if r["n_trips"] > 0)
        counts = {"stops_in_region": len(rows), "with_service": served}
        conn.execute(
            "UPDATE build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
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
