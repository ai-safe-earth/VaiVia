"""Run the provider spike: fetch, enrich identically, and lay the results side
by side where a decision can be made.

Outputs, all under review/spike-providers/ (gitignored with the rest of review/):

    results.json       every candidate with its enrichment — the raw comparison
    comparison.geojson all candidate lines, for QGIS or any map
    dashboard.html     THE decision surface: map + table + per-provider verdicts,
                       self-contained, generated from the same data
    README.md          the summary, generated not written

Elevation is deliberately not computed here (owner: it can be added later);
difficulty and the MTB verdict are the point.

Run from pipeline/ (network built, routes joined, places snapped):
    uv run python -m spike_providers.run
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core import connect
from spike_providers.common import Candidate
from spike_providers.dashboard import render_dashboard
from spike_providers.enrich import enrich
from spike_providers.providers import (
    freeroute_candidates,
    ors_candidates,
    osm_candidates,
    trailsplits_candidates,
    trailsplits_pois,
)

OUT = Path(__file__).resolve().parents[2] / "review" / "spike-providers"

# The OSM baseline: one clean single-line route, one short one, and the DOL —
# deliberately including the route TrailSplits also serves, so the two
# geometries of the same relation sit on the same map.
OSM_BASELINE = [74613, 74619, 1601198]

# Anchors for the routing engines: two real start vertices from the place snap
# (Rongio lane end, and across the Grigna flank) — the engines are asked for
# routes a walker would actually ask for.
ANCHOR = (9.3304428, 45.9276882)  # Rongio, sentiero 14's trailhead
ACROSS = (9.3876756, 45.9533979)  # below Grigna Settentrionale


def candidate_result(conn, candidate: Candidate) -> dict:
    started = time.monotonic()
    enrichment = enrich(conn, candidate)
    return {
        "id": candidate.candidate_id,
        "provider": candidate.provider,
        "name": candidate.name,
        "activity": candidate.activity,
        "points": candidate.points,
        "pieces": len(candidate.paths),
        "extra": candidate.extra,
        "enrichment": {
            "line_length_m": round(enrichment.line_length_m, 1),
            "matched_edges": enrichment.matched_edges,
            "matched_length_m": round(enrichment.matched_length_m, 1),
            "matched_share": round(enrichment.matched_share, 3),
            "surface": enrichment.surface,
            "surface_dominant": enrichment.surface_dominant,
            "sac_scale": enrichment.sac_scale,
            "mtb": enrichment.mtb,
            "places": enrichment.places,
        },
        "enrich_seconds": round(time.monotonic() - started, 1),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    notes: dict[str, list[str]] = {
        "osm": [],
        "trailsplits": [],
        "ors": [],
        "freeroute": [],
    }
    candidates: list[Candidate] = []

    with connect() as conn:
        # The planar index 0010 built for measuring and then dropped, rebuilt
        # for the spike: the corridor match transforms every edge to 32632, and
        # without the index that is a 102k-edge seqscan with a reprojection
        # PER CANDIDATE — measured at ~3 minutes each. With it: seconds. Left
        # in place; the main branch can adopt it as a migration if it stays
        # useful (same lesson as 0012, third time now).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS edge_utm_idx"
            " ON curated.edge USING gist (ST_Transform(geom, 32632))"
        )

        print("osm: reading baseline relations from the store")
        osm = osm_candidates(conn, OSM_BASELINE)
        notes["osm"].append(f"{len(osm)} baseline relations from curated.edge_route")
        candidates += osm

        print("trailsplits: fetching trails over Lecco")
        ts, ts_notes = trailsplits_candidates()
        notes["trailsplits"] += ts_notes
        candidates += ts

        pois, poi_error = trailsplits_pois()
        notes["trailsplits"].append(
            poi_error
            or f"their POI layer over Lecco: {len(pois)} features "
            "(ours: 10,422 in staging.osm_poi)"
        )

        print("ors: requesting routes (foot-hiking, cycling-mountain, round trip)")
        ors, ors_notes = ors_candidates(ANCHOR, ACROSS)
        notes["ors"] += ors_notes
        candidates += ors

        print("freeroute: attempting the ORS-compatible endpoint")
        fr, fr_notes = freeroute_candidates(ANCHOR, ACROSS)
        notes["freeroute"] += fr_notes
        candidates += fr

        print(f"\nenriching {len(candidates)} candidates against the curated network")
        results = []
        for candidate in candidates:
            result = candidate_result(conn, candidate)
            e = result["enrichment"]
            print(
                f"  {result['id']:<34} {e['line_length_m'] / 1000:6.1f} km  "
                f"matched {e['matched_share']:>5.0%}  "
                f"sac={e['sac_scale'] or '—':<26} "
                f"mtb={'?' if e['mtb']['rideable'] is None else e['mtb']['rideable']}"
            )
            results.append(result)

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "bbox": "Lecco (45.8,9.3 → 46.0,9.6)",
        "results": results,
        "notes": notes,
    }
    (OUT / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    features = []
    by_id = {c.candidate_id: c for c in candidates}
    for result in results:
        candidate = by_id[result["id"]]
        geometry = (
            {
                "type": "LineString",
                "coordinates": [[x, y] for x, y in candidate.paths[0]],
            }
            if len(candidate.paths) == 1
            else {
                "type": "MultiLineString",
                "coordinates": [[[x, y] for x, y in p] for p in candidate.paths],
            }
        )
        e = result["enrichment"]
        features.append(
            {
                "type": "Feature",
                "id": result["id"],
                "geometry": geometry,
                "properties": {
                    "id": result["id"],
                    "provider": result["provider"],
                    "name": result["name"],
                    "activity": result["activity"],
                    "km": round(e["line_length_m"] / 1000, 2),
                    "matched_share": e["matched_share"],
                    "sac_scale": e["sac_scale"],
                    "mtb_rideable": e["mtb"]["rideable"],
                    "mtb_scale": e["mtb"]["mtb_scale"],
                    "surface": e["surface_dominant"],
                    "places": len(e["places"]),
                },
            }
        )
    collection = {
        "type": "FeatureCollection",
        "features": features,
        "attribution": "© OpenStreetMap contributors (ODbL); "
        "candidates via TrailSplits and openrouteservice.org as marked",
    }
    (OUT / "comparison.geojson").write_text(
        json.dumps(collection, ensure_ascii=False), encoding="utf-8"
    )

    (OUT / "dashboard.html").write_text(
        render_dashboard(payload, collection), encoding="utf-8"
    )
    write_readme(payload)

    print(
        f"\nwrote {len(results)} results, comparison.geojson, dashboard.html -> {OUT}"
    )


def write_readme(payload: dict) -> None:
    """The summary, generated from the same data the dashboard shows."""
    lines = [
        "# Provider spike — results",
        "",
        (
            f"Generated **{payload['generated']}** over {payload['bbox']}. "
            "Open `dashboard.html` for the comparison; this file is the text of it."
        ),
        "",
        "| candidate | provider | km | matched | SAC | MTB | surface | places |",
        "|---|---|---:|---:|---|---|---|---:|",
    ]
    for r in payload["results"]:
        e = r["enrichment"]
        mtb = e["mtb"]["rideable"]
        lines.append(
            f"| {r['name'] or r['id']} | {r['provider']} "
            f"| {e['line_length_m'] / 1000:.1f} | {e['matched_share']:.0%} "
            f"| {e['sac_scale'] or '—'} "
            f"| {'?' if mtb is None else ('yes' if mtb else 'no')}"
            f"{' (' + e['mtb']['mtb_scale'] + ')' if e['mtb']['mtb_scale'] else ''} "
            f"| {e['surface_dominant'] or '—'} | {len(e['places'])} |"
        )
    lines += ["", "## Provider notes", ""]
    for provider, provider_notes in payload["notes"].items():
        lines.append(f"### {provider}")
        lines += [f"- {n}" for n in provider_notes] or ["- (none)"]
        lines.append("")
    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
