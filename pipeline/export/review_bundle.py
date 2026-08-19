"""Export a review bundle: QA layers as a GeoPackage, plus what explains them.

The database is the product, so the authoritative way to review is a QGIS
connection straight to PostGIS (pipeline/README.md). This exists for the other
case — reviewing away from the machine that runs the stack, or keeping a
snapshot of what a decision was made against.

One GeoPackage holds every layer, so QGIS opens the whole review with one
drag-and-drop and each layer keeps its own geometry type and attributes.

Context is deliberately clipped. The full network is ~102,000 edges; a third of
it lies within 200 m of some finding, which is not "context" but a copy of the
database. Only the neighbourhoods of the findings that need JUDGEMENT — the gap
rules and overlap — are carried, because a gap layer with no surrounding lines
is nine unexplained marks on white.

If you need the whole network, connect QGIS to PostGIS and add `curated.edge`
(pipeline/README.md). This bundle is for reviewing findings away from the
machine, not for holding the network.

Run from pipeline/:
    uv run python -m export.review_bundle
    uv run python -m export.review_bundle --out ../review --context-m 150
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import geopandas as gpd

from core import sqlalchemy_url

REPO_ROOT = Path(__file__).resolve().parents[2]

# (layer name in the GeoPackage, SQL). Order matters only for the reader.
LAYERS: list[tuple[str, str]] = [
    (
        "gap_dangle_pair",
        """SELECT finding_id, distance_m, vertex_a, vertex_b, geom
           FROM qa.v_gap_dangle_pair""",
    ),
    (
        "gap_dangle_edge",
        """SELECT finding_id, distance_m, vertex_id, edge_id, geom
           FROM qa.v_gap_dangle_edge""",
    ),
    (
        "gap_dangle_junction",
        """SELECT finding_id, distance_m, vertex_id, junction_id, geom
           FROM qa.v_gap_dangle_junction""",
    ),
    (
        "overlap",
        "SELECT finding_id, shared_m, edge_a, edge_b, geom FROM qa.v_overlap",
    ),
    (
        "degenerate",
        "SELECT finding_id, length_m, self_loop, geom FROM qa.v_degenerate",
    ),
    ("island", "SELECT finding_id, vertices, component_id, geom FROM qa.v_island"),
    # What the last repair pass changed, so a repair is reviewable off-machine
    # too. Deletions are in qa.fix with geometry before only; this layer is the
    # edits, with how far each end moved.
    (
        "fix",
        """SELECT fix_id, rule, target, note, start_moved_m, end_moved_m, geom
           FROM qa.v_fix""",
    ),
    (
        "poi",
        """SELECT osm_type, osm_id, poi_type, name, ele_m, regions, geom
           FROM staging.osm_poi""",
    ),
]

# Edges near the findings that need a human decision. The other rules are
# counted, not judged one by one. Overlap joined that list once the gap rules
# were repairable and it became the standing queue: it is the rule that cannot
# be automated, because the same ground mapped twice can be a duplicate, a
# bridge, or two ways that legitimately share a stretch.
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

DOCS = [
    ("pipeline/docs/data-sources.md", "1-data-sources.md"),
    ("pipeline/docs/metadata-rules.md", "2-metadata-rules.md"),
    ("pipeline/data/near_miss.png", "3-near-miss-histogram.png"),
    ("pipeline/README.md", "4-pipeline-README.md"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "review"))
    parser.add_argument("--context-m", type=float, default=150.0)
    parser.add_argument(
        "--zip", action="store_true", help="also write <out>.zip beside the folder"
    )
    args = parser.parse_args()

    out = Path(args.out)
    # REVIEW.md is written per round and says what each layer is FOR; a rebuild
    # of the layers must not silently delete the questions asked against them.
    review_note = out / "REVIEW.md"
    kept = review_note.read_text(encoding="utf-8") if review_note.is_file() else None
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    if kept is not None:
        review_note.write_text(kept, encoding="utf-8")
    gpkg = out / "vaivia-qa.gpkg"
    url = sqlalchemy_url()

    for name, sql in LAYERS:
        frame = gpd.read_postgis(sql, url, geom_col="geom")
        # GeoPackage cannot hold a Postgres array; regions becomes a string.
        for column in frame.columns:
            if frame[column].dtype == object and column != "geom":
                frame[column] = frame[column].map(
                    lambda v: ", ".join(v) if isinstance(v, list) else v
                )
        frame.to_file(gpkg, layer=name, driver="GPKG")
        print(f"  {name:<18} {len(frame):>7,} features")

    context = gpd.read_postgis(
        CONTEXT, url, geom_col="geom", params={"context_m": args.context_m}
    )
    context.to_file(gpkg, layer="network_context", driver="GPKG")
    print(f"  {'network_context':<18} {len(context):>7,} features")

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
