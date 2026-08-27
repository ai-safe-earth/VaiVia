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
import uuid
from pathlib import Path

from core import connect
from export.document import Span, build_document
from export.orientation import (
    Edge,
    Piece,
    Step,
    climb,
    climbing_gradients,
    profile_steps,
    walk_route,
    walked_distance,
)
from export.shape import classify_osm_shape
from export.terminals import endpoints_of, terminal_for_point
from ids import DIRECTED_SHAPES, forward_is_stored, route_id

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
FROM (SELECT DISTINCT rel_id, edge_id FROM source_map.edge_route) er
JOIN source_map.edge e ON e.edge_id = er.edge_id
GROUP BY er.rel_id
"""

ROUTE_LINES_INDEX = "CREATE INDEX ON route_line (rel_id)"

# One statement per route, reading the stored line.
ROUTE = """
WITH member AS (
    SELECT DISTINCT rel_id, edge_id FROM source_map.edge_route WHERE rel_id = %(rel)s
),
edges AS (
    SELECT e.* FROM member m JOIN source_map.edge e ON e.edge_id = m.edge_id
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
    (SELECT min(z) FROM edges, LATERAL unnest(profile_m) z),
    (SELECT max(z) FROM edges, LATERAL unnest(profile_m) z),
    (SELECT count(*) FILTER (WHERE ascent_m IS NULL) FROM edges),
    -- The line's geodesic length: the basis distance_along_m was measured
    -- against, so a reversed sibling can flip a place's position on the
    -- same ruler.
    (SELECT ST_Length(geom::geography) FROM line),
    -- Endpoint gap in metres, for the shape classifier (export/shape.py).
    -- Only a single LineString has two endpoints to measure; a route held in
    -- pieces reports NULL and the classifier stays conservative.
    (SELECT CASE WHEN GeometryType(geom) = 'LINESTRING'
                 THEN ST_Distance(ST_StartPoint(utm), ST_EndPoint(utm)) END
       FROM route_line WHERE rel_id = %(rel)s)
"""

SPANS = """
SELECT e.tags ->> 'surface', e.tags ->> 'sac_scale', e.length_m
FROM (SELECT DISTINCT rel_id, edge_id FROM source_map.edge_route WHERE rel_id = %(rel)s) m
JOIN source_map.edge e ON e.edge_id = m.edge_id
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
       ST_X(p.geom), ST_Y(p.geom),
       ST_Distance(p.geom::geography, l.geom::geography) AS offset_m,
       CASE WHEN s.geom IS NOT NULL
            THEN ST_LineLocatePoint(s.geom, p.geom) * ST_Length(s.geom::geography)
       END AS along_m,
       p.is_start
FROM line l, single s, source_map.place p
WHERE ST_DWithin(ST_Transform(p.geom, 32632), l.utm, %(radius)s)
ORDER BY along_m NULLS LAST, offset_m
"""

# Every walked occurrence of every edge, located along its piece of the
# merged line — the raw material export/orientation.py turns into a walk.
# LEFT JOIN, so an edge that ST_Covers puts on no piece arrives with piece
# -1 and the orientation honestly refuses rather than guessing. Repeats
# (the same way twice in one relation) arrive as repeated rows.
EDGE_WALK = """
WITH pieces AS (
    SELECT (d).path[1] AS piece_no, (d).geom AS pgeom
    FROM (SELECT ST_Dump(ST_Multi(geom)) AS d
          FROM route_line WHERE rel_id = %(rel)s) x
)
SELECT coalesce(p.piece_no, -1),
       ST_X(ST_StartPoint(p.pgeom)), ST_Y(ST_StartPoint(p.pgeom)),
       ST_X(ST_EndPoint(p.pgeom)),   ST_Y(ST_EndPoint(p.pgeom)),
       coalesce(ST_Equals(ST_StartPoint(p.pgeom), ST_EndPoint(p.pgeom)), false),
       er.member_index,
       e.edge_id,
       coalesce(ST_LineLocatePoint(p.pgeom, ST_StartPoint(e.geom)), 0),
       coalesce(ST_LineLocatePoint(p.pgeom, ST_EndPoint(e.geom)), 0),
       e.length_m, e.ascent_m, e.descent_m, e.profile_m
FROM source_map.edge_route er
JOIN source_map.edge e ON e.edge_id = er.edge_id
LEFT JOIN pieces p ON ST_Covers(p.pgeom, e.geom)
WHERE er.rel_id = %(rel)s
ORDER BY er.member_index
"""

RELATIONS = """
SELECT r.rel_id, r.tags, r.regions, c.matched_fraction
FROM staging.osm_relation r
JOIN qa.v_route_coverage c ON c.rel_id = r.rel_id
WHERE EXISTS (SELECT 1 FROM source_map.edge_route er WHERE er.rel_id = r.rel_id)
ORDER BY r.rel_id
"""


def build_profile(
    steps: list[tuple[list[float], float]] | None,
) -> dict[str, list[float]] | None:
    """Concatenate the walked steps' profiles into one series along the route.

    Steps come from orientation.profile_steps — WALK order, each series
    already reversed where the walk runs against the stored edge. A missing
    sample anywhere makes the whole profile absent, for the same reason
    ascent is null: a profile with a hole silently shortens the route it
    describes.
    """
    if steps is None:
        return None
    distances: list[float] = []
    elevations: list[float] = []
    travelled = 0.0
    for series, length_m in steps:
        step = (length_m or 0.0) / max(len(series) - 1, 1)
        for i, z in enumerate(series):
            distances.append(round(travelled + i * step, 1))
            elevations.append(round(z, 1))
        travelled += length_m or 0.0
    if len(distances) < 2:
        return None
    return {"distance_m": distances, "elevation_m": elevations}


def reversed_profile(
    profile: dict[str, list[float]] | None,
) -> dict[str, list[float]] | None:
    """The same ground walked the other way: elevations reversed, distances
    re-measured from the other end."""
    if profile is None:
        return None
    total = profile["distance_m"][-1]
    return {
        "distance_m": [round(total - d, 1) for d in reversed(profile["distance_m"])],
        "elevation_m": list(reversed(profile["elevation_m"])),
    }


def route_walk(rows) -> list[Step] | None:
    """EDGE_WALK rows -> the inferred walk (export/orientation.py)."""
    if not rows:
        return None
    occurrences: dict[int, int] = {}
    for row in rows:
        occurrences[row[7]] = occurrences.get(row[7], 0) + 1
    edges: dict[int, Edge] = {}
    pieces: dict[int, Piece] = {}
    for (
        piece_no,
        sx,
        sy,
        ex,
        ey,
        closed,
        member,
        edge_id,
        f_start,
        f_end,
        length_m,
        ascent_m,
        descent_m,
        profile_m,
    ) in rows:
        if edge_id not in edges:
            edges[edge_id] = Edge(
                edge_id=edge_id,
                piece_no=piece_no,
                f_start=float(f_start),
                f_end=float(f_end),
                length_m=float(length_m or 0.0),
                ascent_m=None if ascent_m is None else float(ascent_m),
                descent_m=None if descent_m is None else float(descent_m),
                profile_m=profile_m,
                occurrences=occurrences[edge_id],
            )
        if piece_no >= 0:
            known = pieces.get(piece_no)
            pieces[piece_no] = Piece(
                piece_no=piece_no,
                start=(sx, sy),
                end=(ex, ey),
                min_member=min(member, known.min_member) if known else member,
                closed=closed,
            )
    return walk_route(list(edges.values()), list(pieces.values()))


# A multi-piece route below this matched share is a fragment wearing a full
# route's name (BI-12: 2 of 646 ways) — clipped at the coverage edge rather
# than gapped. Owner-ratified 2026-08-27: at or above the floor a gapped
# route is offered with its gaps visible; below it, held until coverage
# grows. Single-line routes are untouched — their coverage shows in
# matched_fraction like everyone else's.
MULTI_PIECE_FLOOR = 0.9


def emit(
    conn, rel_id: int, tags: dict, regions: list[str], matched_fraction, run_id: str
) -> list[dict] | None:
    """The documents for one relation: one, two for a single-line circular
    (both directions, owner rule 2026-08-27), or None when the multi-piece
    floor holds it back."""
    row = conn.execute(ROUTE, {"rel": rel_id}).fetchone()
    (
        geojson,
        pieces,
        bbox,
        edge_sum_m,
        lowest_m,
        highest_m,
        without_profile,
        line_geo_m,
        endpoint_gap_m,
    ) = row

    if pieces > 1 and (
        matched_fraction is None or float(matched_fraction) < MULTI_PIECE_FLOOR
    ):
        return None

    spans = conn.execute(SPANS, {"rel": rel_id}).fetchall()
    surface_spans = [Span(s, float(length)) for s, _sac, length in spans]
    sac_spans = [Span(sac, float(length)) for _s, sac, length in spans]

    places = [
        {
            "id": source_id,
            "kind": kind,
            "name": name,
            "ele_m": ele_m,
            "lon": round(lon, 6),
            "lat": round(lat, 6),
            "offset_m": round(offset_m, 1),
            "distance_along_m": None if along_m is None else round(along_m, 1),
            "is_start": is_start,
        }
        for source_id, kind, name, ele_m, lon, lat, offset_m, along_m, is_start in conn.execute(
            PLACES, {"rel": rel_id, "radius": PLACES_M}
        )
    ]

    # The inferred walk (export/orientation.py): direction-aware climb,
    # walked distance (an out-and-back stretch counts both passes), and the
    # profile in walk order. When the walk cannot be honestly known, all
    # three are absent — never the direction-blind sums that stood here and
    # violated the join rule (metadata-rules.md: never summed per piece).
    walk = route_walk(conn.execute(EDGE_WALK, {"rel": rel_id}).fetchall())
    climbed = climb(walk)
    ascent_m, descent_m = climbed if climbed is not None else (None, None)
    distance_m = walked_distance(walk)
    if distance_m is None:
        distance_m = float(edge_sum_m)
    profile = build_profile(profile_steps(walk))

    geometry = json.loads(geojson)
    # Measured, never declared: the mapper's roundtrip tag wins, then the
    # merged-endpoint gap against the calibrated ratio (export/shape.py).
    shape = classify_osm_shape(
        None if endpoint_gap_m is None else float(endpoint_gap_m),
        float(distance_m),
        tags.get("roundtrip"),
    )
    coordinate_pieces = (
        [geometry["coordinates"]]
        if geometry["type"] == "LineString"
        else geometry["coordinates"]
    )
    # This document describes the STORED orientation — whichever way the
    # mapper drew it. Its direction label says which of the pair that is,
    # from geometry alone.
    direction = None
    if shape in DIRECTED_SHAPES:
        direction = "fwd" if forward_is_stored(coordinate_pieces[0]) else "rev"

    def one_document(
        doc_direction: str | None,
        doc_geometry: dict,
        doc_places: list[dict],
        doc_ascent: float | None,
        doc_descent: float | None,
        doc_profile: dict | None,
        recommended: bool | None,
    ) -> dict:
        # One terminal for a circular route, the two outermost endpoints of
        # the merged geometry for a traverse — each independently tested for
        # network reachability.
        terminals = [
            terminal_for_point(conn, lon, lat)
            for lon, lat in endpoints_of(doc_geometry, shape)
        ]
        return build_document(
            route_id=route_id(coordinate_pieces, shape, doc_direction or "fwd"),
            kind="osm_route",
            shape=shape,
            direction=doc_direction,
            recommended=recommended,
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
            geometry=doc_geometry,
            bbox=bbox,
            distance_m=float(distance_m),
            ascent_m=doc_ascent,
            descent_m=doc_descent,
            lowest_m=None if lowest_m is None else float(lowest_m),
            highest_m=None if highest_m is None else float(highest_m),
            profile=doc_profile,
            surface_spans=surface_spans,
            sac_spans=sac_spans,
            pieces=pieces,
            edges_without_profile=without_profile,
            matched_fraction=(
                None if matched_fraction is None else float(matched_fraction)
            ),
            places=doc_places,
            terminals=terminals,
            # Which breaks are our bbox and which are real holes is a
            # judgement (the QGIS queue) — 'unknown' until judged.
            divergence=None,
            provenance={
                "run_id": run_id,
                "producer": "pipeline/export/route_documents.py",
                "sources": SOURCES,
            },
        )

    # A single-line circular is walkable either way, so it is emitted BOTH
    # ways (owner rule 2026-08-27), recommending the direction that takes
    # the steep side up — the higher mean climbing gradient. Without a
    # profile there is nothing to recommend from, and null is honest.
    if shape == "circular" and pieces == 1:
        recommended_stored: bool | None = None
        recommended_other: bool | None = None
        if profile is not None:
            grads = climbing_gradients(profile["distance_m"], profile["elevation_m"])
            if grads is not None:
                recommended_stored = grads[0] >= grads[1]
                recommended_other = not recommended_stored
        stored = one_document(
            direction,
            geometry,
            places,
            ascent_m,
            descent_m,
            profile,
            recommended_stored,
        )
        other_geometry = {
            "type": geometry["type"],
            "coordinates": list(reversed(geometry["coordinates"])),
        }
        basis = None if line_geo_m is None else float(line_geo_m)
        other_places = sorted(
            (
                {
                    **place,
                    "distance_along_m": (
                        None
                        if place["distance_along_m"] is None or basis is None
                        else round(basis - place["distance_along_m"], 1)
                    ),
                }
                for place in places
            ),
            key=lambda p: (
                p["distance_along_m"] is None,
                p["distance_along_m"],
                p["offset_m"],
            ),
        )
        other = one_document(
            "rev" if direction == "fwd" else "fwd",
            other_geometry,
            other_places,
            descent_m,
            ascent_m,
            reversed_profile(profile),
            recommended_other,
        )
        return [stored, other]

    return [
        one_document(direction, geometry, places, ascent_m, descent_m, profile, None)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "review" / "routes"))
    parser.add_argument("--limit", type=int, help="emit only the first N routes")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Remove only what THIS emitter owns. Both emitters write vv2-*.json now,
    # so ownership lives in a manifest instead of the filename: delete what
    # the previous run of THIS emitter listed, plus the legacy patterns from
    # before the id cutover.
    manifest = out / ".osm_documents.json"
    if manifest.exists():
        for stale in json.loads(manifest.read_text(encoding="utf-8")):
            (out / stale).unlink(missing_ok=True)
    for stale in out.glob("osm-relation-*.json"):
        stale.unlink()
    (out / "routes.geojson").unlink(missing_ok=True)

    run_id = f"export-{uuid.uuid4().hex[:8]}"
    with connect() as conn:
        conn.execute(
            "INSERT INTO provenance.build_run (run_id, stage, parameters) VALUES (%s, 'export', %s)",
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
        owned: list[str] = []
        emitted: dict[str, int] = {}
        folded: dict[int, int] = {}
        held = 0
        for rel_id, tags, regions, matched_fraction in relations:
            documents = emit(conn, rel_id, tags, regions, matched_fraction, run_id)
            if documents is None:
                held += 1
                continue
            # Two relations over the SAME canonical ground share one id — a
            # duplicate mapping, or a foot and a bike relation on one rail
            # trail. Same ground is same route (the rule that already folds
            # an mtb ask into a bike-legal foot loop), so the id knows: the
            # first relation (lowest rel_id — the iteration is ordered)
            # keeps the document, the rest are FOLDED, out loud, and
            # recorded in the run's counts. A silent overwrite here once
            # cost a relation its identity with nobody told.
            primary = documents[0]
            if primary["id"] in emitted:
                folded[rel_id] = emitted[primary["id"]]
                print(
                    f"  folded relation {rel_id} into {emitted[primary['id']]} "
                    f"({primary['id']}): same canonical ground"
                )
                continue
            emitted[primary["id"]] = rel_id
            for document in documents:
                path = out / f"{document['id']}.json"
                path.write_text(
                    json.dumps(document, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                owned.append(path.name)
            if primary["quality"]["warnings"]:
                warned += 1
            # The map: one FeatureCollection of every route, for dropping onto a
            # map without opening 752 files. The geometry is the same object the
            # document carries, not a second rendering of it — and one feature
            # per GROUND: a direction pair would draw the same line twice.
            features.append(
                {
                    "type": "Feature",
                    "id": primary["id"],
                    "geometry": primary["geometry"],
                    "properties": {
                        "id": primary["id"],
                        "name": primary["identity"]["name"],
                        "ref": primary["identity"]["ref"],
                        "activity": primary["identity"]["activity"],
                        "distance_m": primary["measures"]["distance_m"],
                        "ascent_m": primary["measures"]["ascent_m"],
                        "sac_scale": primary["difficulty"]["sac_scale"],
                        "surface": primary["surface"]["dominant"],
                        "places": len(primary["places"]),
                        "continuous": primary["continuity"]["continuous"],
                        "warnings": len(primary["quality"]["warnings"]),
                    },
                }
            )

        manifest.write_text(json.dumps(sorted(owned), indent=1), encoding="utf-8")
        if folded:
            conn.execute(
                "UPDATE provenance.build_run SET notes = %s WHERE run_id = %s",
                (
                    "folded same-ground relations: "
                    + ", ".join(f"{a}->{b}" for a, b in sorted(folded.items())),
                    run_id,
                ),
            )

        collection = {
            "type": "FeatureCollection",
            "features": features,
            "attribution": "; ".join(s["attribution"] for s in SOURCES),
        }
        (out / "routes.geojson").write_text(
            json.dumps(collection, ensure_ascii=False), encoding="utf-8"
        )

        counts = {
            "routes": len(relations),
            "documents": len(owned),
            "held_below_floor": held,
            "with_warnings": warned,
        }
        conn.execute(
            "UPDATE provenance.build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (json.dumps(counts), run_id),
        )

    size = sum(p.stat().st_size for p in out.glob("*.json"))
    print(f"wrote {len(owned):,} documents to {out} ({size / 1e6:.1f} MB)")
    print(
        f"{held:,} multi-piece routes held below the {MULTI_PIECE_FLOOR} "
        "matched floor - clipped at the coverage edge, not offered"
    )
    print(f"{warned:,} carry a quality warning - they are emitted, not filtered")
    print(f"map: {out / 'routes.geojson'}")
    print(f"run {run_id}")


if __name__ == "__main__":
    main()
