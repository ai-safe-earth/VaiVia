"""Load the Geofabrik PBF into staging: ways, route relations, POIs, settlements.

One streaming pass with pyosmium's FileProcessor (locations + assembled areas),
clipped to the configured region bboxes. Tags are kept WHOLE as jsonb — the
survey's finding was that today's ingestion discards sac_scale, incline and
access by choosing columns at the door, so here the door keeps everything and
columns exist only for what every consumer filters on.

Legality (load/legality.py) is computed here because it is deterministic from
the tags; counts of excluded ways are reported per reason, since "how much of
the network is private" is a coverage fact, not a log line.

Run from pipeline/ (PostGIS up, migrations applied, PBF downloaded):
    uv run python -m load.osm --pbf data/nord-ovest-latest.osm.pbf
    uv run python -m load.osm --pbf data/nord-ovest-latest.osm.pbf --dry-run
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from typing import Any

import osmium
import osmium.filter
from osmium.osm import Area, Node, Relation, Way

from core import REGIONS, connect
from load.legality import WALKABLE_HIGHWAYS, routable_bike, routable_foot
from load.poi_types import parse_ele_m, poi_type_for, settlement_kind

ROUTE_TYPES = frozenset({"hiking", "foot", "mtb", "bicycle"})


def ewkb4326(wkb_hex: str) -> str:
    """Stamp SRID 4326 into pyosmium's plain WKB hex.

    WKBFactory emits WKB with no SRID, and a geometry(..., 4326) column rejects
    SRID-0 input outright. EWKB marks the SRID by setting the 0x20000000 flag
    in the type word and appending the SRID little-endian right after it; the
    type word is little-endian too, so the flag is its LAST byte -- hex chars
    8..10 after the byte-order byte.
    """
    return wkb_hex[:8] + "20" + "E6100000" + wkb_hex[10:]


def regions_for(lat: float, lon: float) -> list[str]:
    return [
        name
        for name, (a, b, c, d) in REGIONS.items()
        if a <= lat <= c and b <= lon <= d
    ]


def regions_for_bounds(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float
) -> list[str]:
    """Regions whose bbox intersects the feature's bounds — a feature that
    crosses the boundary belongs to both, not to neither."""
    return [
        name
        for name, (a, b, c, d) in REGIONS.items()
        if not (max_lat < a or min_lat > c or max_lon < b or min_lon > d)
    ]


class Collector:
    """Accumulates staging rows from one streamed pass."""

    def __init__(self) -> None:
        self.wkb = osmium.geom.WKBFactory()
        self.ways: list[dict[str, Any]] = []
        self.relations: list[dict[str, Any]] = []
        self.pois: list[dict[str, Any]] = []
        self.settlements: list[dict[str, Any]] = []
        self.excluded: dict[str, int] = {}  # legality reason -> count
        self.seen_area_orig: set[tuple[str, int]] = set()

    # -- ways ---------------------------------------------------------------

    def way(self, w: Way) -> None:
        tags = dict(w.tags)
        highway = tags.get("highway")
        if not highway or highway not in WALKABLE_HIGHWAYS:
            return
        try:
            lats = [n.lat for n in w.nodes if n.location.valid()]
            lons = [n.lon for n in w.nodes if n.location.valid()]
        except osmium.InvalidLocationError:
            return
        if len(lats) < 2:
            return
        regions = regions_for_bounds(min(lats), min(lons), max(lats), max(lons))
        if not regions:
            return
        foot_ok, foot_why = routable_foot(tags)
        bike_ok, bike_why = routable_bike(tags)
        if not foot_ok and not bike_ok:
            self.excluded[foot_why or "?"] = self.excluded.get(foot_why or "?", 0) + 1
        try:
            geom = ewkb4326(self.wkb.create_linestring(w))
        except (osmium.InvalidLocationError, RuntimeError):
            return
        self.ways.append(
            {
                "way_id": w.id,
                "tags": json.dumps(tags),
                "geom": geom,
                "regions": regions,
                "routable_foot": foot_ok,
                "routable_bike": bike_ok,
                "legality_note": foot_why if not foot_ok else bike_why,
            }
        )

    # -- relations ----------------------------------------------------------

    def relation(self, r: Relation) -> None:
        tags = dict(r.tags)
        if tags.get("type") != "route" or tags.get("route") not in ROUTE_TYPES:
            return
        members = [{"type": m.type, "ref": m.ref, "role": m.role} for m in r.members]
        if not members:
            return
        self.relations.append(
            {
                "rel_id": r.id,
                "tags": json.dumps(tags),
                "members": json.dumps(members),
                # Region membership is resolved downstream against member ways;
                # staged as [] rather than guessed from nothing.
                "regions": [],
            }
        )

    # -- nodes: point POIs and settlements ------------------------------------

    def node(self, n: Node) -> None:
        if not n.location.valid():
            return
        lat, lon = n.location.lat, n.location.lon
        regions = regions_for(lat, lon)
        if not regions:
            return
        tags = dict(n.tags)
        poi_type = poi_type_for(tags)
        if poi_type:
            self.pois.append(
                {
                    "osm_type": "n",
                    "osm_id": n.id,
                    "poi_type": poi_type,
                    "name": tags.get("name"),
                    "ele_m": parse_ele_m(tags),
                    "tags": json.dumps(tags),
                    "geom": ewkb4326(self.wkb.create_point(n)),
                    "regions": regions,
                }
            )
        kind = settlement_kind(tags)
        if kind and kind != "residential":  # residential is an area concept
            self.settlements.append(
                {
                    "osm_type": "n",
                    "osm_id": n.id,
                    "kind": kind,
                    "name": tags.get("name"),
                    "geom": ewkb4326(self.wkb.create_point(n)),
                    "regions": regions,
                }
            )

    # -- areas: polygon POIs and residential landuse --------------------------

    def area(self, a: Area) -> None:
        tags = dict(a.tags)
        poi_type = poi_type_for(tags)
        kind = settlement_kind(tags)
        if not poi_type and kind != "residential":
            return
        # An area assembled from a way keeps the way's id; from a relation, the
        # relation's. orig_id disambiguates for the (osm_type, osm_id) key.
        osm_type = "w" if a.from_way() else "r"
        osm_id = a.orig_id()
        if (osm_type, osm_id) in self.seen_area_orig:
            return
        try:
            geom = ewkb4326(self.wkb.create_multipolygon(a))
        except RuntimeError:
            return
        # Bounds from the outer ring for region intersection.
        ring = next(iter(a.outer_rings()), None)
        if ring is None:
            return
        lats = [n.lat for n in ring]
        lons = [n.lon for n in ring]
        regions = regions_for_bounds(min(lats), min(lons), max(lats), max(lons))
        if not regions:
            return
        self.seen_area_orig.add((osm_type, osm_id))
        if poi_type:
            self.pois.append(
                {
                    "osm_type": osm_type,
                    "osm_id": osm_id,
                    "poi_type": poi_type,
                    "name": tags.get("name"),
                    "ele_m": parse_ele_m(tags),
                    "tags": json.dumps(tags),
                    "geom": geom,
                    "regions": regions,
                }
            )
        elif kind == "residential":
            self.settlements.append(
                {
                    "osm_type": osm_type,
                    "osm_id": osm_id,
                    "kind": "residential",
                    "name": tags.get("name"),
                    "geom": geom,
                    "regions": regions,
                }
            )


def stream(pbf_path: str) -> Collector:
    collector = Collector()
    processor = (
        osmium.FileProcessor(pbf_path)
        .with_locations()
        .with_areas()
        .with_filter(osmium.filter.EmptyTagFilter())
    )
    started = time.monotonic()
    count = 0
    for obj in processor:
        count += 1
        if count % 2_000_000 == 0:
            print(
                f"  ... {count / 1e6:.0f}M objects, {time.monotonic() - started:.0f}s"
            )
        if obj.is_node():
            collector.node(obj)
        elif obj.is_way():
            collector.way(obj)
        elif obj.is_relation():
            collector.relation(obj)
        elif obj.is_area():
            collector.area(obj)
    print(f"streamed {count:,} tagged objects in {time.monotonic() - started:.0f}s")
    return collector


def prune_relations(collector: Collector) -> None:
    """Keep only relations with at least one member way in the kept set, and
    resolve their regions from those ways."""
    kept_ways = {w["way_id"]: w["regions"] for w in collector.ways}
    pruned = []
    for rel in collector.relations:
        members = json.loads(rel["members"])
        regions: set[str] = set()
        for m in members:
            if m["type"] == "w" and m["ref"] in kept_ways:
                regions.update(kept_ways[m["ref"]])
        if regions:
            rel["regions"] = sorted(regions)
            pruned.append(rel)
    collector.relations = pruned


def load(collector: Collector, run_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connect() as conn:
        conn.execute(
            "INSERT INTO build_run (run_id, stage, parameters) VALUES (%s, 'load', %s)",
            (run_id, json.dumps({"source": "geofabrik nord-ovest", "loader": "osm"})),
        )
        # Staging is raw: a reload replaces it whole rather than merging.
        for table in ("osm_way", "osm_relation", "osm_poi", "settlement"):
            conn.execute(f"TRUNCATE staging.{table}")

        with conn.cursor() as cur:
            with cur.copy(
                "COPY staging.osm_way (way_id, tags, geom, regions, routable_foot,"
                " routable_bike, legality_note, run_id) FROM STDIN"
            ) as copy:
                for w in collector.ways:
                    copy.write_row(
                        (
                            w["way_id"],
                            w["tags"],
                            w["geom"],
                            w["regions"],
                            w["routable_foot"],
                            w["routable_bike"],
                            w["legality_note"],
                            run_id,
                        )
                    )
            with cur.copy(
                "COPY staging.osm_relation (rel_id, tags, members, regions, run_id)"
                " FROM STDIN"
            ) as copy:
                for r in collector.relations:
                    copy.write_row(
                        (r["rel_id"], r["tags"], r["members"], r["regions"], run_id)
                    )
            with cur.copy(
                "COPY staging.osm_poi (osm_type, osm_id, poi_type, name, ele_m,"
                " tags, geom, regions, run_id) FROM STDIN"
            ) as copy:
                for p in collector.pois:
                    copy.write_row(
                        (
                            p["osm_type"],
                            p["osm_id"],
                            p["poi_type"],
                            p["name"],
                            p["ele_m"],
                            p["tags"],
                            p["geom"],
                            p["regions"],
                            run_id,
                        )
                    )
            with cur.copy(
                "COPY staging.settlement (osm_type, osm_id, kind, name, geom,"
                " regions, run_id) FROM STDIN"
            ) as copy:
                for s in collector.settlements:
                    copy.write_row(
                        (
                            s["osm_type"],
                            s["osm_id"],
                            s["kind"],
                            s["name"],
                            s["geom"],
                            s["regions"],
                            run_id,
                        )
                    )

        counts = {
            "ways": len(collector.ways),
            "relations": len(collector.relations),
            "pois": len(collector.pois),
            "settlements": len(collector.settlements),
            "ways_fully_excluded": sum(collector.excluded.values()),
        }
        conn.execute(
            "UPDATE build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (json.dumps(counts), run_id),
        )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", required=True)
    parser.add_argument(
        "--dry-run", action="store_true", help="stream and report, write nothing"
    )
    args = parser.parse_args()

    run_id = f"load-osm-{uuid.uuid4().hex[:8]}"
    collector = stream(args.pbf)
    prune_relations(collector)

    print(f"\nways (routing candidates in-region): {len(collector.ways):,}")
    foot = sum(1 for w in collector.ways if w["routable_foot"])
    bike = sum(1 for w in collector.ways if w["routable_bike"])
    print(f"  routable on foot: {foot:,}  by bike: {bike:,}")
    if collector.excluded:
        print("  fully excluded (neither mode), by reason:")
        for reason, n in sorted(collector.excluded.items(), key=lambda kv: -kv[1]):
            print(f"    {reason:<24} {n:,}")
    print(f"route relations (with in-region members): {len(collector.relations):,}")
    by_type: dict[str, int] = {}
    for p in collector.pois:
        by_type[p["poi_type"]] = by_type.get(p["poi_type"], 0) + 1
    print(f"POIs: {len(collector.pois):,}")
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"    {t:<14} {n:,}")
    print(f"settlements: {len(collector.settlements):,}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return
    counts = load(collector, run_id)
    print(f"\nloaded into staging (run {run_id}): {counts}")


if __name__ == "__main__":
    main()
