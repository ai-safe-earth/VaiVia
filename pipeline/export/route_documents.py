"""Emit the route documents: the product, one JSON file per route, plus a map.

docs/route-document.md is the contract. PostGIS holds the value and answers the
geometry questions; this turns it into the artefact every reader consumes — the
API, the Neo4j export, the frontend, and whatever holds photos and comments
later. None of them redefines a route; they read this.

Today the routes are the 752 OSM route relations, because those are the routes
that exist. `pipeline/draw/` will generate its own, and it emits through this
same module: a generated route is a different `kind`, not a different document.

What a route PASSES is computed HERE and nowhere earlier. metadata-rules.md
puts it at assembly for a reason — a place's position is `ST_LineLocatePoint`
against the MERGED line, which does not exist until the route does. Precomputing
it per edge would have needed a radius nobody chose.

The 100 m bound on places is the one threshold in this file, and it is where
`qa.distance_band` already puts "near" (measured: median 7 places per route,
p90 34, against 12 and 59 at 250 m). `offset_m` travels with every place, so a
reader wanting 30 m filters on it.

Run from pipeline/ (network built, routes joined, elevation sampled, places snapped):
    uv run python -m export.route_documents --limit 5
    uv run python -m export.route_documents
"""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path

from core import connect
from export.document import Span, build_document

REPO_ROOT = Path(__file__).resolve().parents[2]

# Places within this distance of the merged line are "passed". See the module
# docstring: a bound, not a filter, and offset_m travels so a reader can be
# stricter.
PLACES_M = 100.0

# Attribution is not optional and does not belong only in the frontend footer.
# docs/licensing.md: ODbL requires attribution of any Produced Work, and the
# document IS the produced work — so the obligation travels inside it, where a
# consumer cannot strip it by rendering the geometry somewhere else.
SOURCES = [
    {
        "name": "OpenStreetMap",
        "licence": "ODbL 1.0",
        "attribution": "© OpenStreetMap contributors",
        "url": "https://www.openstreetmap.org/copyright",
        "provides": [
            "geometry",
            "names",
            "waymarks",
            "surface",
            "difficulty",
            "places",
        ],
    },
    {
        "name": "Copernicus GLO-30 DEM",
        "licence": "Free, full and open (Regulation 1159/2013)",
        "attribution": "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH "
        "2014-2018 provided under COPERNICUS by the European Union and ESA",
        "url": "https://spacedata.copernicus.eu/",
        "provides": ["elevation", "ascent", "descent", "profile"],
    },
]

# Every route's merged line, built ONCE for the whole run. Each route needs its
# line four times (geometry, places, start, profile) and ST_LineMerge over a
# route's edges is the expensive part -- rebuilding it per query cost about a
# second per route, and all 752 materialise in one. It is also the safer shape:
# every measure in a document now hangs off the same stored line, so the
# geometry a document reports and the geometry its places were positioned
# against cannot disagree.
ROUTE_LINES = """
CREATE TEMP TABLE route_line ON COMMIT DROP AS
SELECT er.rel_id,
       ST_LineMerge(ST_Collect(e.geom)) AS geom,
       ST_Transform(ST_LineMerge(ST_Collect(e.geom)), 32632) AS utm
FROM (SELECT DISTINCT rel_id, edge_id FROM curated.edge_route) er
JOIN curated.edge e ON e.edge_id = er.edge_id
GROUP BY er.rel_id
"""

ROUTE_LINES_INDEX = "CREATE INDEX ON route_line (rel_id)"

# One statement per route, reading the stored line.
ROUTE = """
WITH member AS (
    SELECT DISTINCT rel_id, edge_id FROM curated.edge_route WHERE rel_id = %(rel)s
),
edges AS (
    SELECT e.* FROM member m JOIN curated.edge e ON e.edge_id = m.edge_id
),
line AS (
    SELECT geom FROM route_line WHERE rel_id = %(rel)s
)
SELECT
    (SELECT ST_AsGeoJSON(geom) FROM line),
    (SELECT ST_NumGeometries(ST_Multi(geom)) FROM line),
    (SELECT ARRAY[ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)]
       FROM line),
    (SELECT sum(length_m) FROM edges),
    (SELECT CASE WHEN count(*) FILTER (WHERE ascent_m IS NULL) > 0 THEN NULL
                 ELSE sum(ascent_m) END FROM edges),
    (SELECT CASE WHEN count(*) FILTER (WHERE descent_m IS NULL) > 0 THEN NULL
                 ELSE sum(descent_m) END FROM edges),
    (SELECT min(z) FROM edges, LATERAL unnest(profile_m) z),
    (SELECT max(z) FROM edges, LATERAL unnest(profile_m) z),
    (SELECT count(*) FILTER (WHERE ascent_m IS NULL) FROM edges),
    (SELECT array_agg(ARRAY[coalesce(length_m, 0)]::text[]) FROM edges)
"""

SPANS = """
SELECT e.tags ->> 'surface', e.tags ->> 'sac_scale', e.length_m
FROM (SELECT DISTINCT rel_id, edge_id FROM curated.edge_route WHERE rel_id = %(rel)s) m
JOIN curated.edge e ON e.edge_id = m.edge_id
"""

# Position along the MERGED line, which is what metadata-rules.md specifies and
# what nothing before this point could compute.
PLACES = """
WITH line AS (
    SELECT geom, utm FROM route_line WHERE rel_id = %(rel)s
),
-- ST_LineLocatePoint needs a single LineString. A route held in several pieces
-- has no single measure along it, so those routes get places with an offset and
-- a null position rather than a fabricated one.
single AS (
    SELECT CASE WHEN GeometryType(geom) = 'LINESTRING' THEN geom END AS geom FROM line
)
SELECT p.source_id, p.kind, p.name, p.ele_m,
       ST_Distance(p.geom::geography, l.geom::geography) AS offset_m,
       CASE WHEN s.geom IS NOT NULL
            THEN ST_LineLocatePoint(s.geom, p.geom) * ST_Length(s.geom::geography)
       END AS along_m,
       p.is_start
FROM line l, single s, curated.place p
WHERE ST_DWithin(ST_Transform(p.geom, 32632), l.utm, %(radius)s)
ORDER BY along_m NULLS LAST, offset_m
"""

# The starts on this route's own vertices: where a walk on it can begin.
START = """
SELECT p.vertex_id, min(p.distance_m), count(*),
       array_remove(array_agg(DISTINCT p.name), NULL),
       bool_or(p.source = 'gtfs_stop' OR p.kind = 'station'),
       ST_AsGeoJSON(v.geom)
FROM (SELECT DISTINCT rel_id, edge_id FROM curated.edge_route WHERE rel_id = %(rel)s) m
JOIN curated.edge e ON e.edge_id = m.edge_id
JOIN curated.place p ON p.vertex_id IN (e.source, e.target) AND p.is_start
JOIN curated.vertex v ON v.vertex_id = p.vertex_id
GROUP BY p.vertex_id, v.geom
ORDER BY min(p.distance_m)
LIMIT 1
"""

PROFILE = """
SELECT e.profile_m, e.length_m, er.member_index, e.piece_index
FROM curated.edge_route er
JOIN curated.edge e ON e.edge_id = er.edge_id
WHERE er.rel_id = %(rel)s
ORDER BY er.member_index, e.piece_index
"""

RELATIONS = """
SELECT r.rel_id, r.tags, r.regions, c.matched_fraction
FROM staging.osm_relation r
JOIN qa.v_route_coverage c ON c.rel_id = r.rel_id
WHERE EXISTS (SELECT 1 FROM curated.edge_route er WHERE er.rel_id = r.rel_id)
ORDER BY r.rel_id
"""


def build_profile(rows) -> dict[str, list[float]] | None:
    """Concatenate the edges' profiles into one series along the route.

    Distance is cumulative across edges in member order. A missing sample
    anywhere makes the whole profile absent, for the same reason ascent is
    null: a profile with a hole silently shortens the route it describes.
    """
    distances: list[float] = []
    elevations: list[float] = []
    travelled = 0.0
    for profile, length_m, _member, _piece in rows:
        if not profile or any(z is None for z in profile):
            return None
        step = (length_m or 0.0) / max(len(profile) - 1, 1)
        for i, z in enumerate(profile):
            distances.append(round(travelled + i * step, 1))
            elevations.append(round(z, 1))
        travelled += length_m or 0.0
    if len(distances) < 2:
        return None
    return {"distance_m": distances, "elevation_m": elevations}


def emit(
    conn, rel_id: int, tags: dict, regions: list[str], matched_fraction, run_id: str
):
    row = conn.execute(ROUTE, {"rel": rel_id}).fetchone()
    (
        geojson,
        pieces,
        bbox,
        distance_m,
        ascent_m,
        descent_m,
        lowest_m,
        highest_m,
        without_profile,
        _lengths,
    ) = row

    spans = conn.execute(SPANS, {"rel": rel_id}).fetchall()
    surface_spans = [Span(s, float(length)) for s, _sac, length in spans]
    sac_spans = [Span(sac, float(length)) for _s, sac, length in spans]

    places = [
        {
            "id": source_id,
            "kind": kind,
            "name": name,
            "ele_m": ele_m,
            "offset_m": round(offset_m, 1),
            "distance_along_m": None if along_m is None else round(along_m, 1),
            "is_start": is_start,
        }
        for source_id, kind, name, ele_m, offset_m, along_m, is_start in conn.execute(
            PLACES, {"rel": rel_id, "radius": PLACES_M}
        )
    ]

    start_row = conn.execute(START, {"rel": rel_id}).fetchone()
    start = None
    if start_row:
        vertex_id, nearest_m, anchors, names, car_free, point = start_row
        start = {
            "vertex_id": vertex_id,
            "names": names,
            "anchors": anchors,
            "nearest_m": round(nearest_m, 1),
            "car_free": car_free,
            "point": json.loads(point),
        }

    profile = build_profile(conn.execute(PROFILE, {"rel": rel_id}).fetchall())

    return build_document(
        route_id=f"osm-relation-{rel_id}",
        kind="osm_route",
        identity={
            "name": tags.get("name"),
            "ref": tags.get("ref"),
            "activity": tags.get("route"),
            "network": tags.get("network"),
            "waymark": tags.get("osmc:symbol"),
            "from": tags.get("from"),
            "to": tags.get("to"),
            "operator": tags.get("operator"),
            "regions": regions,
            "osm_relation_id": rel_id,
        },
        geometry=json.loads(geojson),
        bbox=bbox,
        distance_m=float(distance_m),
        ascent_m=None if ascent_m is None else float(ascent_m),
        descent_m=None if descent_m is None else float(descent_m),
        lowest_m=None if lowest_m is None else float(lowest_m),
        highest_m=None if highest_m is None else float(highest_m),
        profile=profile,
        surface_spans=surface_spans,
        sac_spans=sac_spans,
        pieces=pieces,
        edges_without_profile=without_profile,
        matched_fraction=None if matched_fraction is None else float(matched_fraction),
        places=places,
        start=start,
        provenance={
            "run_id": run_id,
            "producer": "pipeline/export/route_documents.py",
            "sources": SOURCES,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "review" / "routes"))
    parser.add_argument("--limit", type=int, help="emit only the first N routes")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    run_id = f"export-{uuid.uuid4().hex[:8]}"
    with connect() as conn:
        conn.execute(
            "INSERT INTO build_run (run_id, stage, parameters) VALUES (%s, 'export', %s)",
            (
                run_id,
                json.dumps(
                    {"builder": "export.route_documents", "places_radius_m": PLACES_M}
                ),
            ),
        )
        conn.execute(ROUTE_LINES)
        conn.execute(ROUTE_LINES_INDEX)
        conn.execute("ANALYZE route_line")

        relations = conn.execute(RELATIONS).fetchall()
        if args.limit:
            relations = relations[: args.limit]
        print(f"routes: {len(relations):,}")

        features = []
        warned = 0
        for rel_id, tags, regions, matched_fraction in relations:
            document = emit(conn, rel_id, tags, regions, matched_fraction, run_id)
            path = out / f"{document['id']}.json"
            path.write_text(
                json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            if document["quality"]["warnings"]:
                warned += 1
            # The map: one FeatureCollection of every route, for dropping onto a
            # map without opening 752 files. The geometry is the same object the
            # document carries, not a second rendering of it.
            features.append(
                {
                    "type": "Feature",
                    "id": document["id"],
                    "geometry": document["geometry"],
                    "properties": {
                        "id": document["id"],
                        "name": document["identity"]["name"],
                        "ref": document["identity"]["ref"],
                        "activity": document["identity"]["activity"],
                        "distance_m": document["measures"]["distance_m"],
                        "ascent_m": document["measures"]["ascent_m"],
                        "sac_scale": document["difficulty"]["sac_scale"],
                        "surface": document["surface"]["dominant"],
                        "places": len(document["places"]),
                        "continuous": document["continuity"]["continuous"],
                        "warnings": len(document["quality"]["warnings"]),
                    },
                }
            )

        collection = {
            "type": "FeatureCollection",
            "features": features,
            "attribution": "; ".join(s["attribution"] for s in SOURCES),
        }
        (out / "routes.geojson").write_text(
            json.dumps(collection, ensure_ascii=False), encoding="utf-8"
        )

        counts = {"routes": len(relations), "with_warnings": warned}
        conn.execute(
            "UPDATE build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (json.dumps(counts), run_id),
        )

    size = sum(p.stat().st_size for p in out.glob("*.json"))
    print(f"wrote {len(relations):,} documents to {out} ({size / 1e6:.1f} MB)")
    print(f"{warned:,} carry a quality warning - they are emitted, not filtered")
    print(f"map: {out / 'routes.geojson'}")
    print(f"run {run_id}")


if __name__ == "__main__":
    main()
