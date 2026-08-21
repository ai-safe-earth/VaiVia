"""Emit route documents for the generated catalogue.

A generated route is a different `kind`, not a different document
(docs/route-document.md): same schema, same attribution, same rules. What
differs is honest absence — no OSM relation identity, `matched_fraction: null`
(there is no relation to match), and no name until trailhead naming is solved.

The document's measures are REASSEMBLED from the stored walked sequence rather
than copied from curated.route, so the emitter exercises the same pure rules
the tests pin, and a drift between table and document is impossible — the
sequence is the single source.

Run from pipeline/ (after draw.generate):
    uv run python -m draw.emit
"""

from __future__ import annotations

import json
from pathlib import Path

from core import connect
from draw.assemble import assemble
from draw.generate import walked_sequence
from export.document import build_document
from export.route_documents import PLACES_M, SOURCES

OUT = Path(__file__).resolve().parents[2] / "review" / "routes"

ROUTES = """
SELECT r.route_id, r.activity, r.shape, r.name, r.destination_id, r.destination_kind,
       r.destination_name, r.start_vertex, r.target_m, r.score, r.seed, r.run_id,
       ST_AsGeoJSON(r.geom),
       ARRAY[ST_XMin(r.geom), ST_YMin(r.geom), ST_XMax(r.geom), ST_YMax(r.geom)]
FROM curated.route r
ORDER BY r.route_id
"""

SEQUENCE = """
SELECT re.seq, re.edge_id, re.forward, e.source
FROM curated.route_edge re
JOIN curated.edge e ON e.edge_id = re.edge_id
WHERE re.route_id = %(route_id)s
ORDER BY re.seq
"""

REGIONS = """
SELECT array_agg(DISTINCT region ORDER BY region)
FROM curated.route_edge re
JOIN curated.edge e ON e.edge_id = re.edge_id, unnest(e.regions) AS region
WHERE re.route_id = %(route_id)s
"""

PLACES = """
WITH line AS (
    SELECT geom FROM curated.route WHERE route_id = %(route_id)s
)
SELECT p.source_id, p.kind, p.name, p.ele_m,
       ST_X(p.geom), ST_Y(p.geom),
       ST_Distance(p.geom::geography, l.geom::geography) AS offset_m,
       ST_LineLocatePoint(l.geom, p.geom) * ST_Length(l.geom::geography) AS along_m,
       p.is_start
FROM line l, curated.place p
WHERE ST_DWithin(ST_Transform(p.geom, 32632), ST_Transform(l.geom, 32632), %(radius)s)
ORDER BY along_m, offset_m
"""

START = """
SELECT s.vertex_id, s.names, s.anchors, s.nearest_m, s.car_free,
       ST_AsGeoJSON(s.geom)
FROM qa.v_start s WHERE s.vertex_id = %(vertex_id)s
"""


def emit_generated() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    features = []
    with connect() as conn:
        routes = conn.execute(ROUTES).fetchall()
        for (
            rid,
            activity,
            shape,
            name,
            destination_id,
            destination_kind,
            destination_name,
            start_vertex,
            target_m,
            score,
            seed,
            run_id,
            geometry,
            bbox,
        ) in routes:
            steps = [
                (edge_id, forward)
                for _seq, edge_id, forward, _source in conn.execute(
                    SEQUENCE, {"route_id": rid}
                )
            ]
            sequence = walked_sequence(conn, steps)
            facts = assemble(sequence)

            (regions,) = conn.execute(REGIONS, {"route_id": rid}).fetchone()
            places = [
                {
                    "id": source_id,
                    "kind": kind,
                    "name": name,
                    "ele_m": ele_m,
                    "lon": round(lon, 6),
                    "lat": round(lat, 6),
                    "offset_m": round(offset_m, 1),
                    "distance_along_m": round(along_m, 1),
                    "is_start": is_start,
                }
                for source_id, kind, name, ele_m, lon, lat, offset_m, along_m, is_start in conn.execute(
                    PLACES, {"route_id": rid, "radius": PLACES_M}
                )
            ]
            start_row = conn.execute(START, {"vertex_id": start_vertex}).fetchone()
            start = None
            if start_row:
                vertex_id, names, anchors, nearest_m, car_free, point = start_row
                start = {
                    "vertex_id": vertex_id,
                    "names": names or [],
                    "anchors": anchors,
                    "nearest_m": float(nearest_m),
                    "car_free": car_free,
                    "point": json.loads(point),
                }

            surface_spans = [(e.surface, e.length_m) for e in sequence]
            sac_spans = [(e.sac_scale, e.length_m) for e in sequence]
            document = build_document(
                route_id=rid,
                kind="generated",
                # Constructed, not measured: the generator drew it this way.
                # provenance.generation.shape stays too — that is history,
                # where this is the reader contract.
                shape=shape,
                identity={
                    "name": name,
                    "ref": None,
                    # The document's activity vocabulary is OSM's route= one.
                    "activity": "mtb" if activity == "mtb" else "hiking",
                    "network": None,
                    "waymark": None,
                    "from": None,
                    "to": destination_name,
                    "operator": None,
                    "regions": regions or [],
                    "osm_relation_id": None,
                },
                geometry=json.loads(geometry),
                bbox=bbox,
                distance_m=facts.distance_m,
                ascent_m=facts.ascent_m,
                descent_m=facts.descent_m,
                lowest_m=(min(facts.profile["elevation_m"]) if facts.profile else None),
                highest_m=(
                    max(facts.profile["elevation_m"]) if facts.profile else None
                ),
                profile=facts.profile,
                surface_spans=surface_spans,
                sac_spans=sac_spans,
                pieces=1,  # a generated loop is one walked line by construction
                edges_without_profile=sum(1 for e in sequence if e.ascent_m is None),
                matched_fraction=None,
                places=places,
                start=start,
                provenance={
                    "run_id": run_id,
                    "producer": "pipeline/draw/emit.py",
                    "generation": {
                        "activity": activity,
                        "shape": shape,
                        "destination": (
                            {
                                "id": destination_id,
                                "kind": destination_kind,
                                "name": destination_name,
                            }
                            if destination_id
                            else None
                        ),
                        "target_m": target_m,
                        "seed": seed,
                        "score": score,
                        "mtb_rideable": facts.mtb_rideable,
                        "mtb_scale": facts.mtb_scale,
                        "bike_blocked_m": round(facts.bike_blocked_m, 1),
                        "off_road_share": round(facts.off_road_share, 3),
                        "retrace_share": round(facts.retrace_share, 3),
                    },
                    "sources": SOURCES,
                },
            )
            (OUT / f"{rid}.json").write_text(
                json.dumps(document, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            written += 1
            features.append(
                {
                    "type": "Feature",
                    "id": rid,
                    "geometry": document["geometry"],
                    "properties": {
                        "id": rid,
                        "name": name,
                        "activity": activity,
                        "shape": shape,
                        "destination": destination_name,
                        "km": round(facts.distance_m / 1000, 2),
                        "ascent_m": facts.ascent_m,
                        "sac_scale": facts.sac_scale,
                        "sac_max": facts.sac_max,
                        "mtb_rideable": facts.mtb_rideable,
                        "off_road_share": round(facts.off_road_share, 2),
                        "retrace_share": round(facts.retrace_share, 2),
                        "score": score,
                        "places": len(places),
                    },
                }
            )

    (OUT / "generated.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": features,
                "attribution": "; ".join(s["attribution"] for s in SOURCES),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"wrote {written} generated route documents -> {OUT}")
    print(f"map: {OUT / 'generated.geojson'}")


if __name__ == "__main__":
    emit_generated()
