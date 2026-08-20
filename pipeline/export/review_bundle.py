"""Export a review bundle: every layer as a GeoPackage, plus a README that is
generated from the database rather than written by hand.

The database is the product, so the authoritative way to review is a QGIS
connection straight to PostGIS (pipeline/README.md). This exists for the other
case — reviewing away from the machine that runs the stack, or keeping a
snapshot of what a decision was made against.

Two rules this file exists to keep:

**Run it after every step that changes the store.** A bundle that lags the
database is worse than no bundle: it looks current and is not. curate.routes,
curate.elevation, curate.places, topology.repair — each of them ends with a
store somebody has to be able to look at.

**The README is generated, never hand-written.** Every count in it comes from a
query run at export time, so it cannot drift from what the layers hold. The one
thing not generated is what each field MEANS, which is FIELD_NOTES below and is
the only part a person maintains.

`REVIEW.md` beside it is the opposite: written by hand, per round, saying what is
being asked of this bundle. A rebuild preserves it — the questions asked against
a set of layers must outlive a rebuild of those layers.

Run from pipeline/:
    uv run python -m export.review_bundle
    uv run python -m export.review_bundle --out ../review --zip
"""

from __future__ import annotations

import argparse
import shutil
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import geopandas as gpd
import pandas as pd

from core import connect, sqlalchemy_url

REPO_ROOT = Path(__file__).resolve().parents[2]


class Layer(NamedTuple):
    """A layer, and how to review it.

    `style_by` and `sort_by` are the point of this table: they turn "here are
    sixteen layers" into "open this one, colour it by that, sort by the other".
    They are written into the README so the guidance travels with the file.
    """

    name: str
    sql: str
    what: str
    style_by: str | None = None
    sort_by: str | None = None


LAYERS: list[Layer] = [
    Layer(
        "network",
        """SELECT edge_id, way_id, length_m, highway, surface, sac_scale, name,
                  routable_foot, routable_bike, ascent_m, descent_m, net_m, gradient,
                  steepness_class, difficulty_class, surface_class, route_class,
                  access_class, profile_class, profile_points, start_m, end_m, geom
           FROM qa.v_network""",
        "The whole network with every derived attribute on it - tags, climb, "
        "steepness, whether it carries a named route. Open this first.",
        style_by="steepness_class",
        sort_by="length_m desc",
    ),
    Layer(
        "route",
        """SELECT rel_id, ref, name, route_kind, network, osmc_symbol, edges, km,
                  pieces, continuity_class, climb_class, length_class, scope_class, geom
           FROM qa.v_route""",
        "One line per named route: 752 features instead of 102,000.",
        style_by="continuity_class",
        sort_by="pieces desc",
    ),
    Layer(
        "route_coverage",
        """SELECT rel_id, ref, name, way_members, distinct_way_members, matched_ways,
                  matched_edges, matched_fraction, coverage_class
           FROM qa.v_route_coverage""",
        "How much of each relation this network holds. No geometry - an attribute "
        "table, joined to `route` on rel_id.",
        style_by="coverage_class",
        sort_by="matched_fraction asc",
    ),
    Layer(
        "route_elevation",
        """SELECT rel_id, ref, name, route_kind, km, ascent_m, descent_m, lowest_m,
                  highest_m, edges_without_profile, climb_class, geom
           FROM qa.v_route_elevation""",
        "The same routes with height on them.",
        style_by="climb_class",
        sort_by="ascent_m desc",
    ),
    Layer(
        "place",
        """SELECT source, source_id, kind, name, ele_m, vertex_id, distance_m,
                  is_start, start_note, n_trips, distance_band, role_class,
                  reachability_class, geom
           FROM qa.v_place""",
        "Every snapped POI, settlement and transit stop.",
        style_by="kind",
        sort_by="distance_m desc",
    ),
    Layer(
        "place_link",
        """SELECT source, source_id, kind, name, distance_m, is_start, distance_band,
                  role_class, geom
           FROM qa.v_place_link""",
        "THE layer for judging a snap: a line from each place to the vertex it "
        "attached to. A wrong snap is invisible as a number and obvious as a line "
        "reaching across a valley.",
        style_by="distance_band",
        sort_by="distance_m desc",
    ),
    Layer(
        "start",
        """SELECT vertex_id, component_id, degree, anchors, nearest_m, trips,
                  car_free, access_class, reachability_class, naming_class, geom
           FROM qa.v_start""",
        "Where a walk can begin: one point per vertex, with what makes it a start.",
        style_by="reachability_class",
        sort_by="anchors desc",
    ),
    Layer(
        "draw",
        """SELECT route_id, start_vertex, target_km, km, ascent_m, sac_scale,
                  mtb_rideable, off_road_share, retrace_share, score,
                  difficulty_class, climb_class, offroad_class, shape_class,
                  mtb_class, geom
           FROM qa.v_draw""",
        "The generated catalogue: bounded loops drawn from real starts over our "
        "own edges. Score is descriptive, never a filter.",
        style_by="offroad_class",
        sort_by="score desc",
    ),
    Layer(
        "overlap",
        "SELECT finding_id, shared_m, edge_a, edge_b, geom FROM qa.v_overlap",
        "THE OPEN QUEUE. The same ground mapped twice: a duplicate, a bridge, or two "
        "ways that legitimately share a stretch. Only judgement tells them apart.",
        sort_by="shared_m desc",
    ),
    Layer(
        "island",
        "SELECT finding_id, vertices, component_id, geom FROM qa.v_island",
        "Components too small to hold a route. A coverage fact, not a defect - "
        "welding one to something would invent a connection.",
        sort_by="vertices desc",
    ),
    Layer(
        "gap_dangle_pair",
        """SELECT finding_id, distance_m, vertex_a, vertex_b, geom
           FROM qa.v_gap_dangle_pair""",
        "Two loose ends within tolerance, not joined. Empty means repaired.",
    ),
    Layer(
        "gap_dangle_edge",
        """SELECT finding_id, distance_m, vertex_id, edge_id, geom
           FROM qa.v_gap_dangle_edge""",
        "A loose end near another edge's interior: an under- or overshoot. Empty "
        "means repaired.",
    ),
    Layer(
        "gap_dangle_junction",
        """SELECT finding_id, distance_m, vertex_id, junction_id, geom
           FROM qa.v_gap_dangle_junction""",
        "A loose end stopping just short of an existing junction. Empty means repaired.",
    ),
    Layer(
        "degenerate",
        "SELECT finding_id, length_m, self_loop, geom FROM qa.v_degenerate",
        "Sub-metre edges and self-loops. Empty means repaired.",
    ),
    Layer(
        "fix",
        """SELECT fix_id, rule, target, note, start_moved_m, end_moved_m, geom
           FROM qa.v_fix""",
        "What the last repair pass changed, with how far each end moved. Nothing "
        "should have moved more than the 2 m tolerance.",
        style_by="rule",
        sort_by="end_moved_m desc",
    ),
    Layer(
        "poi",
        """SELECT osm_type, osm_id, poi_type, name, ele_m, regions, geom
           FROM staging.osm_poi""",
        "The raw POIs as staged, points and polygons both - the shape `place` "
        "measured from before it kept a marker point.",
        style_by="poi_type",
    ),
]

# Edges near the findings that need a human decision. The other rules are
# counted, not judged one by one. Overlap joined that list once the gap rules
# were repairable and it became the standing queue.
CONTEXT = """
SELECT DISTINCT e.edge_id, e.way_id, e.length_m,
       e.tags ->> 'highway' AS highway,
       e.tags ->> 'surface' AS surface,
       e.tags ->> 'sac_scale' AS sac_scale,
       e.tags ->> 'name' AS name,
       e.routable_foot, e.routable_bike, e.geom
FROM curated.edge e
JOIN qa.finding f
  ON ST_DWithin(e.geom::geography, f.geom::geography, %(context_m)s)
-- Latest run only, like every qa.v_* view. Without it the context accumulates
-- the neighbourhoods of every run ever made: after five runs it was carrying
-- 7,219 edges as "context" for nine findings.
JOIN qa.latest_run r ON r.run_id = f.run_id
WHERE f.rule IN ('gap_dangle_pair', 'gap_dangle_edge', 'gap_dangle_junction',
                 'overlap')
"""

# The only hand-maintained part of the README. Everything else is queried.
FIELD_NOTES: dict[str, str] = {
    "edge_id": "the network edge; stable within one build, not across builds",
    "way_id": "provenance: the parent OSM way",
    "length_m": "geodesic length of this piece (WGS84 ellipsoid)",
    "highway": "raw OSM highway tag",
    "surface": "raw OSM surface tag (~62% of edges carry one)",
    "sac_scale": "raw OSM hiking grade (~14%); a few carry junk - see difficulty_class",
    "name": "the feature's own name tag; often empty where a route carries the name",
    "routable_foot": "walkable, decided at load from access tags by load/legality.py",
    "routable_bike": "bikeable, same rule per mode",
    "ascent_m": "metres climbed ALONG THE STORED DIRECTION; reversing a piece swaps "
    "it with descent_m",
    "descent_m": "metres dropped along the stored direction",
    "net_m": "ascent minus descent: the signed rise end to end",
    "gradient": "net_m / length_m, signed. NOT the mean local slope - a switchbacked "
    "edge climbs 40 m at a 5% gradient",
    "profile_points": "samples in the stored altitude profile; equals the geometry's "
    "point count",
    "start_m": "elevation at the first point of the geometry",
    "end_m": "elevation at the last point",
    "rel_id": "the OSM route relation",
    "ref": "the CAI sentiero number (650 of 752 relations carry one)",
    "route_kind": "hiking | bicycle | mtb | foot",
    "network": "OSM network scope: lwn/rwn/nwn/iwn (walking), lcn/rcn/ncn (cycling)",
    "osmc_symbol": "the painted waymark, as OSM encodes it",
    "edges": "edges this route uses (distinct - a way listed twice counts once)",
    "km": "route length; sums DISTINCT edges, so an out-and-back leg is not doubled",
    "pieces": "how many disconnected parts the route's edges merge into. 1 is continuous",
    "way_members": "way entries in the relation's member list, duplicates included",
    "distinct_way_members": "distinct member ways - the denominator of matched_fraction",
    "matched_ways": "member ways this network actually holds",
    "matched_edges": "edges those ways became after noding",
    "matched_fraction": "matched_ways / distinct_way_members. Near 0 means a route "
    "this network cannot hold, not a broken join",
    "lowest_m": "lowest point on the route, over the whole profile",
    "highest_m": "highest point on the route",
    "edges_without_profile": "edges whose climb is unknown, north of the DEM tile edge",
    "source": "poi | settlement | gtfs_stop",
    "source_id": "the source's own key (n123 / w456 / trenord:S01)",
    "kind": "poi_type, settlement kind, or 'stop'",
    "ele_m": "the OSM ele tag - never the DEM, which reads summits ~23 m low",
    "vertex_id": "the network vertex this place snapped to",
    "distance_m": "geodesic distance from the WHOLE geometry to that vertex. A polygon "
    "touching a lane is 0 m",
    "is_start": "can a walk begin here (curate/anchors.py)",
    "start_note": "why not, when it cannot",
    "n_trips": "GTFS stop_times count: evidence of actual service",
    "component_id": "connected component. Everything outside the largest is an island",
    "degree": "edges meeting at this vertex. 1 is a loose end",
    "anchors": "how many places make this vertex a start - several car parks share one "
    "lane end",
    "nearest_m": "distance to the closest of those anchors",
    "trips": "total GTFS trips across this vertex's stops",
    "car_free": "reachable by train or bus, not only by car",
    "finding_id": "one QA finding",
    "shared_m": "metres of ground the two edges have in common",
    "edge_a": "one side of the overlap",
    "edge_b": "the other side",
    "vertices": "vertices in this island component",
    "vertex_a": "one loose end",
    "vertex_b": "the other loose end",
    "junction_id": "the existing junction the loose end stops short of",
    "self_loop": "source and target are the same vertex",
    "fix_id": "one automated repair",
    "rule": "which QA rule produced it",
    "target": "the row that changed (edge:1234)",
    "start_moved_m": "how far the start point moved. Must be under the 2 m tolerance",
    "end_moved_m": "how far the end point moved",
    "note": "the rule's own fields, as JSON",
    "poi_type": "peak | hut | parking | lake | ... one class per POI",
    "regions": "which configured province(s) the feature falls in",
    "osm_type": "n | w | r",
    "osm_id": "the OSM id within that type",
    # The categorised twins. Each one exists so a QGIS Categorized renderer
    # needs no expression; the values themselves are listed under each layer.
    "steepness_class": "gradient, banded. Colour the network by this to see where "
    "the climbing is",
    "difficulty_class": "sac_scale, grouped - including `9 invalid tag`, which is "
    "raw OSM junk surfaced rather than hidden",
    "surface_class": "paved / unpaved / untagged, from ~200 raw surface values",
    "route_class": "does this edge carry a named route relation",
    "access_class": "on `network`: which modes may use it. On `start`: whether the "
    "start is reachable without a car",
    "profile_class": "whether this edge has an altitude profile, and why not if it "
    "does not",
    "continuity_class": "does the route merge into one line, or come out in pieces",
    "climb_class": "total ascent, banded",
    "length_class": "route length, banded to what a walker plans around",
    "scope_class": "local / regional / national / international, from the OSM "
    "network tag",
    "coverage_class": "how much of the relation this network holds. `3 fragment` "
    "means a route generator must exclude it",
    "distance_band": "how far the place is from the network. Bands only - nothing "
    "is filtered on this",
    "role_class": "can a walk begin here, or is it somewhere a walk goes",
    "reachability_class": "on the main network, or on an island that goes nowhere",
    "naming_class": "does this start have a name to offer a user",
}

DOCS = [
    ("pipeline/docs/data-sources.md", "1-data-sources.md"),
    ("pipeline/docs/metadata-rules.md", "2-metadata-rules.md"),
    ("pipeline/data/near_miss.png", "3-near-miss-histogram.png"),
    ("pipeline/README.md", "4-pipeline-README.md"),
]

# Everything numeric in the README comes from here. (label, sql, note)
STATE = [
    ("Network", "SELECT count(*) FROM curated.edge", "edges"),
    (
        "Network length",
        "SELECT round((sum(length_m)/1000)::numeric,1) FROM curated.edge",
        (
            "km - **the integrity check**: a pass that moves this has either invented "
            "ground or thrown some away"
        ),
    ),
    ("Vertices", "SELECT count(*) FROM curated.vertex", "routing nodes"),
    (
        "Connected",
        """SELECT round((100.0 * max(n) / sum(n))::numeric, 1) FROM
           (SELECT count(*) n FROM curated.vertex GROUP BY component_id) s""",
        "% of vertices in the largest component",
    ),
    (
        "Named routes",
        "SELECT count(DISTINCT rel_id) FROM curated.edge_route",
        "route relations joined onto the network",
    ),
    (
        "Edges carrying a route",
        "SELECT count(DISTINCT edge_id) FROM curated.edge_route",
        "edges with a name from a relation",
    ),
    (
        "Elevation",
        "SELECT count(*) FROM curated.edge WHERE ascent_m IS NOT NULL",
        "edges with ascent and descent",
    ),
    (
        "Total climb",
        "SELECT round(sum(ascent_m)::numeric,0) FROM curated.edge",
        "metres of ascent across the network",
    ),
    (
        "Places",
        "SELECT count(*) FROM curated.place",
        "POIs, settlements and stops snapped",
    ),
    (
        "Starts",
        "SELECT count(DISTINCT vertex_id) FROM curated.place WHERE is_start",
        "vertices a walk can begin at",
    ),
]

# Each row is a real, open question. The count comes from the database; the
# sentence says what it means and what to do about it.
ISSUES = [
    (
        "overlap findings",
        "SELECT count(*) FROM qa.v_overlap",
        (
            "**The one open QA queue.** The same ground mapped twice. Cannot be "
            "automated: a duplicate, a bridge, and two ways legitimately sharing a "
            "stretch look identical to a rule. Layer `overlap`, sort by `shared_m`."
        ),
    ),
    (
        "routes in more than one piece",
        "SELECT count(*) FROM qa.v_route WHERE pieces > 1",
        (
            "Partly bbox clipping, partly real gaps along a named route - a defect the "
            "topology rules cannot see, because they look at loose ends, not at whether "
            "a route runs through. Layer `route`, `continuity_class`."
        ),
    ),
    (
        "routes matching under 20% of their ways",
        (
            "SELECT count(*) FROM qa.v_route_coverage "
            "WHERE coverage_class = '3 fragment (<20%)'"
        ),
        (
            "Long-distance routes that only clip the provinces - BI-12 Trieste-Savona "
            "matches 2 of 646 ways. Route generation must filter on `matched_fraction`, "
            "or a fragment ships under a famous name."
        ),
    ),
    (
        "start vertices on an island",
        "SELECT count(*) FROM qa.v_start WHERE reachability_class = '1 island'",
        "Begin there and get nowhere. Layer `start`, `reachability_class`.",
    ),
    (
        "starts with no name",
        "SELECT count(*) FROM qa.v_start WHERE naming_class = '1 unnamed'",
        (
            'Car parks are rarely named in OSM. "Start from vertex 43128" is not an '
            "answer, and naming a trailhead from a nearby feature is unsolved."
        ),
    ),
    (
        "places over 100 m from the network",
        "SELECT count(*) FROM curated.place WHERE distance_m > 100",
        (
            "Mostly trackless summits, which is a coverage fact, not an error. But some "
            "are car parks, which is either a missing access road in OSM or a polygon "
            "somewhere odd. Layer `place_link`, sort by `distance_m`."
        ),
    ),
    (
        "edges with no altitude profile",
        "SELECT count(*) FROM curated.edge WHERE ascent_m IS NULL",
        (
            "North of 46.0001, where the single GLO-30 tile ends. Their climb is NULL "
            "rather than a partial sum. Fetching tile N46 E009 closes it."
        ),
    ),
    (
        "edges with a junk sac_scale",
        "SELECT count(*) FROM qa.v_network WHERE difficulty_class = '9 invalid tag'",
        (
            "Raw OSM values outside the enum. Surfaced rather than folded into "
            '"ungraded", which would hide them for good.'
        ),
    ),
    (
        "islands",
        "SELECT count(*) FROM qa.v_island",
        (
            "**Deliberately not repaired.** A component too small to hold a route is "
            "usually a genuinely isolated fragment at the edge of the bbox. Welding it "
            "to something would invent a connection."
        ),
    ),
]

SETTLED = [
    (
        "Every repairable topology defect is at zero",
        (
            "SELECT count(*) FROM qa.finding f JOIN qa.latest_run r USING (run_id) "
            "WHERE f.rule IN ('gap_dangle_pair','gap_dangle_edge','gap_dangle_junction',"
            "'degenerate')"
        ),
        (
            "findings across the four repairable rules - and the network kept every "
            "metre while they were fixed"
        ),
    ),
    (
        "Route relations joined",
        "SELECT count(DISTINCT rel_id) FROM curated.edge_route",
        "of 752, with 10,361 edges that had no name of their own now carrying one",
    ),
    (
        "The DEM agrees with the ground",
        (
            "SELECT count(*) FROM staging.osm_poi "
            "WHERE poi_type='saddle' AND ele_m IS NOT NULL"
        ),
        (
            "saddles checked against their OSM `ele` tag: median error 4.1 m. Judge the "
            "DEM on saddles, never on peaks - a 30 m cell reads a summit ~23 m low by design"
        ),
    ),
    (
        "Every staged source is read by something",
        (
            "SELECT count(DISTINCT parameters ->> 'builder') FROM build_run "
            "WHERE parameters ? 'builder'"
        ),
        (
            "distinct builders have run over this store - ways, relations, the DEM, "
            "POIs, settlements and stops are all joined onto the network"
        ),
    ),
]


def scalar(conn, sql: str):
    row = conn.execute(sql).fetchone()
    return row[0] if row else None


def fmt(value) -> str:
    """Thousands separators, whatever numeric type the query came back as.

    Counts arrive as int and sums as Decimal, and `9238.0` beside `101,951`
    reads as a different kind of number when it is not.
    """
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (float, Decimal)):
        text = f"{value:,f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def category_fields(frame: pd.DataFrame) -> list[str]:
    """Fields meant for Categorized styling: the explicit classes, plus the
    low-cardinality text columns beside them."""
    out = []
    for column in frame.columns:
        if column == "geom":
            continue
        if (
            column.endswith(("_class", "_band"))
            or frame[column].dtype == object
            and 0 < frame[column].nunique() <= 20
        ):
            out.append(column)
    return out


def write_readme(path: Path, conn, exported: list[tuple[Layer, pd.DataFrame]]) -> None:
    """Generate README.md from the database. Every number here is queried."""
    stamp = scalar(conn, "SELECT to_char(now(), 'YYYY-MM-DD HH24:MI')")
    runs = conn.execute("""
        SELECT parameters ->> 'builder', max(started_at)::date, max(run_id)
        FROM build_run WHERE parameters ? 'builder'
        GROUP BY 1 ORDER BY 2 DESC, 1
    """).fetchall()

    lines: list[str] = []
    add = lines.append

    add("# VaiVia review bundle")
    add("")
    add(f"Generated **{stamp}** from the live PostGIS store. Every number below is a")
    add(
        "query run at export time, so this file cannot drift from the layers beside it."
    )
    add("")
    add("`REVIEW.md`, if present, is the opposite: hand-written, saying what is being")
    add("asked of this round. A rebuild preserves it.")
    add("")

    add("## State")
    add("")
    add("| | | |")
    add("|---|---:|---|")
    for label, sql, note in STATE:
        add(f"| {label} | **{fmt(scalar(conn, sql))}** | {note} |")
    add("")

    add("## What is settled")
    add("")
    for label, sql, note in SETTLED:
        add(f"- **{label}** — {fmt(scalar(conn, sql))} {note}")
    add("")

    add("## What is open")
    add("")
    add("Ordered by how much they matter, not by size. A zero here is good news.")
    add("")
    for label, sql, note in ISSUES:
        add(f"### {fmt(scalar(conn, sql))} {label}")
        add("")
        add(note)
        add("")

    add("## Layers")
    add("")
    add("| layer | features | what it is | colour by | sort by |")
    add("|---|---:|---|---|---|")
    for layer, frame in exported:
        style = f"`{layer.style_by}`" if layer.style_by else "—"
        order = f"`{layer.sort_by}`" if layer.sort_by else "—"
        add(f"| `{layer.name}` | {len(frame):,} | {layer.what} | {style} | {order} |")
    add("")
    add("**To style one in QGIS:** Layer Properties → Symbology → *Categorized*, pick")
    add("the field named in *colour by*, then *Classify*. The classes carry a leading")
    add("digit so the legend comes out in order — QGIS sorts categories by value, and")
    add("without it `flat` lands between `gentle` and `moderate`.")
    add("")
    add("**Two traps that have cost time before.** A view has no primary key, so QGIS")
    add("asks for a *Feature id* column in the Add Layer dialog and loads the layer")
    add("EMPTY if you skip it. And an italic layer name in the panel means")
    add("scale-dependent visibility is on — Layer Properties → Rendering.")
    add("")

    add("## Fields")
    add("")
    for layer, frame in exported:
        add(f"### `{layer.name}`")
        add("")
        add(layer.what)
        add("")
        add("| field | meaning |")
        add("|---|---|")
        for column in frame.columns:
            if column == "geom":
                continue
            # A literal pipe would close the markdown cell early: `source`
            # reads "poi | settlement | gtfs_stop" and split the table in three.
            note = FIELD_NOTES.get(column, "").replace("|", r"\|")
            add(f"| `{column}` | {note} |")
        add("")
        for column in category_fields(frame):
            counts = frame[column].value_counts(dropna=False)
            add(f"**`{column}`** — {len(counts)} categories")
            add("")
            add("| value | features |")
            add("|---|---:|")
            for value, n in counts.sort_index().items():
                label = "(null)" if pd.isna(value) else value
                add(f"| {label} | {n:,} |")
            add("")

    add("## How this was built")
    add("")
    add("| builder | last run | run id |")
    add("|---|---|---|")
    for builder, when, run_id in runs:
        add(f"| `{builder}` | {when} | `{run_id}` |")
    add("")
    add(
        "Every curated row carries the `run_id` that produced it, and `build_run` holds"
    )
    add(
        "each run's parameters and counts — so two runs are compared inside the database"
    )
    add("and there is deliberately no file artefact to diff.")
    add("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "review"))
    parser.add_argument("--context-m", type=float, default=150.0)
    parser.add_argument(
        "--zip", action="store_true", help="also write <out>.zip beside the folder"
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # Remove only what THIS exporter owns, never the whole directory. It used to
    # rmtree `out`, which was safe while the bundle was the only thing in
    # review/ and stopped being safe the moment route documents landed in
    # review/routes/ — a refresh of the layers would have deleted the product.
    # REVIEW.md survives for the older reason: it is written per round and says
    # what is being ASKED of these layers, and the questions must outlive a
    # rebuild of the thing they are about.
    for owned in [gpkg_name := "vaivia-qa.gpkg", "README.md", *(t for _s, t in DOCS)]:
        target = out / owned
        if target.is_file():
            target.unlink()
    gpkg = out / gpkg_name
    url = sqlalchemy_url()

    exported: list[tuple[Layer, pd.DataFrame]] = []
    for layer in LAYERS:
        has_geom = "geom" in layer.sql
        if has_geom:
            frame = gpd.read_postgis(layer.sql, url, geom_col="geom")
        else:
            frame = pd.read_sql(layer.sql, url)
        # GeoPackage cannot hold a Postgres array; regions becomes a string.
        for column in frame.columns:
            if frame[column].dtype == object and column != "geom":
                frame[column] = frame[column].map(
                    lambda v: ", ".join(map(str, v)) if isinstance(v, list) else v
                )
        if has_geom:
            frame.to_file(gpkg, layer=layer.name, driver="GPKG")
        else:
            # An attribute-only table still belongs INSIDE the GeoPackage, joined
            # in QGIS on its id. A sidecar CSV would put half the review outside
            # the file the review opens.
            gpd.GeoDataFrame(
                frame, geometry=gpd.GeoSeries([None] * len(frame))
            ).to_file(gpkg, layer=layer.name, driver="GPKG")
        exported.append((layer, frame))
        print(f"  {layer.name:<22} {len(frame):>7,} features")

    context = gpd.read_postgis(
        CONTEXT, url, geom_col="geom", params={"context_m": args.context_m}
    )
    context.to_file(gpkg, layer="network_context", driver="GPKG")
    print(f"  {'network_context':<22} {len(context):>7,} features")

    with connect() as conn:
        write_readme(out / "README.md", conn, exported)
    print(f"  {'README.md':<22} generated from the database")

    for source, target in DOCS:
        path = REPO_ROOT / source
        if path.is_file():
            shutil.copy(path, out / target)
            print(f"  copied {target}")

    print(f"\ngeopackage: {gpkg} ({gpkg.stat().st_size / 1e6:.1f} MB)")

    if args.zip:
        archive = shutil.make_archive(str(out), "zip", root_dir=out)
        print(f"zip: {archive} ({Path(archive).stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
