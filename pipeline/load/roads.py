"""Load drivable OSM ways into staging.car_road — the drive-time network.

The trail loader deliberately drops everything a car drives on; the drive
matrix (docs/route-design.md, decision 3) needs exactly those ways. One
streaming pass over the same PBF, keeping `highway` classes a car may use,
clipped to the region bboxes. REPLACE, never merge, like every staging load.

Run from pipeline/:
    uv run python -m load.roads --pbf data/nord-ovest-latest.osm.pbf
"""

from __future__ import annotations

import argparse
import time
import uuid

import osmium
import osmium.filter

from core import connect
from load.osm import ewkb4326, regions_for_bounds

#: What a car drives on. `residential`/`unclassified` are the capillaries a
#: trailhead is reached through; `service` is deliberately OUT (driveways
#: and car parks add noise, and the start's own parking is the destination,
#: not a through-road). `track` is out too: legality is unknowable per tag
#: and the matrix must not route cars down a forest track.
DRIVABLE_HIGHWAYS = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
    }
)


def stream_roads(pbf_path: str) -> list[dict]:
    wkb = osmium.geom.WKBFactory()
    rows: list[dict] = []
    processor = (
        osmium.FileProcessor(pbf_path)
        .with_locations()
        .with_filter(osmium.filter.EmptyTagFilter())
        .with_filter(osmium.filter.KeyFilter("highway"))
    )
    started = time.monotonic()
    count = 0
    for obj in processor:
        count += 1
        if count % 2_000_000 == 0:
            print(
                f"  ... {count / 1e6:.0f}M objects, {time.monotonic() - started:.0f}s"
            )
        if not obj.is_way():
            continue
        tags = dict(obj.tags)
        if tags.get("highway") not in DRIVABLE_HIGHWAYS:
            continue
        # Motor access explicitly forbidden is not drivable, whatever the class.
        if tags.get("motor_vehicle") in ("no", "private") or tags.get("access") in (
            "no",
            "private",
        ):
            continue
        try:
            lats = [n.lat for n in obj.nodes if n.location.valid()]
            lons = [n.lon for n in obj.nodes if n.location.valid()]
        except osmium.InvalidLocationError:
            continue
        if len(lats) < 2:
            continue
        if not regions_for_bounds(min(lats), min(lons), max(lats), max(lons)):
            continue
        try:
            geom = ewkb4326(wkb.create_linestring(obj))
        except (osmium.InvalidLocationError, RuntimeError):
            continue
        rows.append(
            {
                "way_id": obj.id,
                "highway": tags["highway"],
                "oneway": tags.get("oneway"),
                "maxspeed": tags.get("maxspeed"),
                "geom": geom,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_id = f"load-roads-{uuid.uuid4().hex[:8]}"
    rows = stream_roads(args.pbf)
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r["highway"]] = by_class.get(r["highway"], 0) + 1
    print(f"drivable ways in-region: {len(rows):,}")
    for cls, n in sorted(by_class.items(), key=lambda kv: -kv[1]):
        print(f"    {cls:<16} {n:,}")
    if args.dry_run:
        print("--dry-run: nothing written.")
        return

    with connect() as conn:
        conn.execute(
            "INSERT INTO provenance.build_run (run_id, stage, parameters) "
            "VALUES (%s, 'load-roads', %s)",
            (run_id, '{"builder": "load.roads"}'),
        )
        conn.execute("TRUNCATE staging.car_road")
        with (
            conn.cursor() as cur,
            cur.copy(
                "COPY staging.car_road (way_id, highway, oneway, maxspeed, geom,"
                " run_id) FROM STDIN"
            ) as copy,
        ):
            for r in rows:
                copy.write_row(
                    (
                        r["way_id"],
                        r["highway"],
                        r["oneway"],
                        r["maxspeed"],
                        r["geom"],
                        run_id,
                    )
                )
        conn.execute(
            "UPDATE provenance.build_run SET finished_at = now(), counts = %s "
            "WHERE run_id = %s",
            (f'{{"roads": {len(rows)}}}', run_id),
        )
    print(f"loaded staging.car_road (run {run_id})")


if __name__ == "__main__":
    main()
