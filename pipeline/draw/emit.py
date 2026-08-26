"""Emit route documents for the generated catalogue.

A generated route is a different `kind`, not a different document
(docs/route-document.md): same schema, same attribution, same rules. What
differs is honest absence — no OSM relation identity, `matched_fraction: null`
(there is no relation to match), and no name until trailhead naming is solved.

The document's measures are REASSEMBLED from the stored walked sequence rather
than copied from catalogue.route, so the emitter exercises the same pure rules
the tests pin, and a drift between table and document is impossible — the
sequence is the single source.

Only what the gate PASSED is emitted (curate.gate): a document under
review/routes/ is a served route, and the served set is the one Neo4j loads.
The manifest deletion above is what makes that reversible — a route demoted
by a tightened ruleset loses its document on the next run, without anything
having to remember it was ever published.

Run from pipeline/ (after draw.generate and curate.gate):
    uv run python -m draw.emit
"""

from __future__ import annotations

import json
from pathlib import Path

from core import connect
from draw.assemble import assemble
from draw.divergence import Step, divergence
from draw.generate import walked_sequence
from export.document import build_document
from export.route_documents import PLACES_M, SOURCES
from export.terminals import terminal_for_vertex
from ids import DIRECTED_SHAPES, forward_is_stored
from ids import route_id as v2_route_id

OUT = Path(__file__).resolve().parents[2] / "review" / "routes"

ROUTES = """
SELECT r.route_id, r.activity, r.shape, r.name, r.destination_id, r.destination_kind,
       r.destination_name, r.start_vertex, r.target_m, r.score, r.seed, r.run_id,
       ST_AsGeoJSON(r.geom),
       ARRAY[ST_XMin(r.geom), ST_YMin(r.geom), ST_XMax(r.geom), ST_YMax(r.geom)]
FROM catalogue.route r
WHERE r.gate_verdict = 'pass'
ORDER BY r.route_id
"""

SEQUENCE = """
SELECT re.seq, re.edge_id, re.forward, e.source, e.target, e.length_m
FROM catalogue.route_edge re
JOIN source_map.edge e ON e.edge_id = re.edge_id
WHERE re.route_id = %(route_id)s
ORDER BY re.seq
"""

REGIONS = """
SELECT array_agg(DISTINCT region ORDER BY region)
FROM catalogue.route_edge re
JOIN source_map.edge e ON e.edge_id = re.edge_id, unnest(e.regions) AS region
WHERE re.route_id = %(route_id)s
"""

PLACES = """
WITH line AS (
    SELECT geom FROM catalogue.route WHERE route_id = %(route_id)s
)
SELECT p.source_id, p.kind, p.name, p.ele_m,
       ST_X(p.geom), ST_Y(p.geom),
       ST_Distance(p.geom::geography, l.geom::geography) AS offset_m,
       ST_LineLocatePoint(l.geom, p.geom) * ST_Length(l.geom::geography) AS along_m,
       p.is_start
FROM line l, source_map.place p
WHERE ST_DWithin(ST_Transform(p.geom, 32632), ST_Transform(l.geom, 32632), %(radius)s)
ORDER BY along_m, offset_m
"""

VERTEX_POINT = """
SELECT ST_AsGeoJSON(geom) FROM source_map.vertex WHERE vertex_id = %(vertex_id)s
"""


def emit_generated() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Ownership by manifest: both emitters write vv2-*.json now, so delete
    # what the previous run of THIS emitter listed, plus the legacy pattern
    # from before the id cutover.
    manifest = OUT / ".generated_documents.json"
    if manifest.exists():
        for stale in json.loads(manifest.read_text(encoding="utf-8")):
            (OUT / stale).unlink(missing_ok=True)
    for stale in OUT.glob("generated-*.json"):
        stale.unlink()

    written = 0
    features = []
    owned: list[str] = []
    with connect() as conn:
        routes = conn.execute(ROUTES).fetchall()
        if not routes:
            # An empty emit is indistinguishable from a successful one on
            # disk, and the gate holds every route until something judges
            # it — so say which of the two it is.
            (total,) = conn.execute("SELECT count(*) FROM catalogue.route").fetchone()
            raise SystemExit(
                f"no route passed the gate ({total:,} in catalogue.route) — "
                "run `uv run python -m curate.gate` first"
                if total
                else "catalogue.route is empty — run `uv run python -m draw.generate`"
            )

        # The shared-corridor pass: every walked sequence first, so each
        # route's divergence is measured against its whole sibling set —
        # same start, same activity (a bike and a hike from one trailhead
        # are not near-duplicates of each other in any answer).
        step_rows: dict[str, list] = {}
        groups: dict[tuple[int, str], dict[str, list[Step]]] = {}
        for row in routes:
            rid, activity, start_vertex = row[0], row[1], row[7]
            rows = conn.execute(SEQUENCE, {"route_id": rid}).fetchall()
            step_rows[rid] = rows
            walk = [
                Step(
                    edge_id,
                    forward,
                    float(length_m),
                    target if forward else source,
                )
                for _seq, edge_id, forward, source, target, length_m in rows
            ]
            groups.setdefault((start_vertex, activity), {})[rid] = walk
        parted: dict[str, tuple[int, float] | None] = {}
        for siblings in groups.values():
            parted.update(divergence(siblings))

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
                for _seq, edge_id, forward, _source, _target, _length in step_rows[rid]
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
            # The id is verified against the ground, never trusted from the
            # table: rekey_v2.py and the generator both mint through
            # pipeline/ids.py, and a drift here is a cutover bug surfacing.
            coords = json.loads(geometry)["coordinates"]
            direction = None
            if shape in DIRECTED_SHAPES:
                direction = "fwd" if forward_is_stored(coords) else "rev"
            expected = v2_route_id([coords], shape, direction or "fwd")
            if rid != expected:
                raise SystemExit(
                    f"catalogue.route {rid!r} does not match its ground "
                    f"({expected!r}). INSPECT the row first — shape, "
                    "direction, geometry: a well-formed row that moved with "
                    "a repair is rekey_v2's job, but a malformed one (a "
                    "corrupt shape once put urban floats here) must be "
                    "deleted, and renaming it would only launder it"
                )

            (point,) = conn.execute(
                VERTEX_POINT, {"vertex_id": start_vertex}
            ).fetchone()
            terminals = [terminal_for_vertex(conn, start_vertex, point)]

            surface_spans = [(e.surface, e.length_m) for e in sequence]
            sac_spans = [(e.sac_scale, e.length_m) for e in sequence]
            document = build_document(
                route_id=rid,
                kind="generated",
                # Constructed, not measured: the generator drew it this way.
                # provenance.generation.shape stays too — that is history,
                # where this is the reader contract.
                shape=shape,
                direction=direction,
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
                terminals=terminals,
                divergence=(
                    {
                        "vertex_id": parted[rid][0],
                        "approach_m": parted[rid][1],
                    }
                    if parted.get(rid)
                    else None
                ),
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
                        "urban_share": (
                            None
                            if facts.urban_share is None
                            else round(facts.urban_share, 3)
                        ),
                    },
                    "sources": SOURCES,
                },
            )
            # Same ground as a MAPPED route folds INTO it, mapped identity
            # winning — the richer document stands, and the two emitters
            # must never fight over one filename (each deletes what its
            # manifest lists; a shared name would seesaw between runs).
            if (OUT / f"{rid}.json").exists() and f"{rid}.json" not in owned:
                print(f"  folded generated {rid} into the mapped document: same ground")
                continue
            (OUT / f"{rid}.json").write_text(
                json.dumps(document, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            owned.append(f"{rid}.json")
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

    manifest.write_text(json.dumps(sorted(owned), indent=1), encoding="utf-8")
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
