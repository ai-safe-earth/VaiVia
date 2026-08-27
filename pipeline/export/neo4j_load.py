"""Load the route documents into Neo4j: the reader-side of the product.

The inversion the pipeline exists for. The app selects routes from Neo4j; this
loads what selection needs — identity, measures, difficulty (character AND
exigent), the MTB verdict, route↔place relationships — from the emitted route
documents, which stay canonical (docs/route-document.md). Geometry and the
profile are deliberately NOT copied in: the document is fetched by route_id,
and a second home for geometry is how two truths start.

Cypher lives in catalogue.cypher as named templates and runs with parameters
only — the backend/graph discipline, applied here. The export owns :Route,
:Place and :Start, and replaces them wholesale each run; the rest of the graph
is the backend's.

Run from pipeline/ (documents emitted; compose Neo4j up):
    uv run python -m export.neo4j_load --dry-run
    uv run python -m export.neo4j_load
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

from core import connect, env_value
from export.document import PUBLISHED_KINDS, SAC_ORDER, published
from export.route_documents import MULTI_PIECE_FLOOR, RELATIONS


def sac_rank(grade: str | None) -> int | None:
    """T1..T6 as 1..6, because Cypher cannot order the strings."""
    return SAC_ORDER.index(grade) + 1 if grade in SAC_ORDER else None


def mtb_rank(grade: str | None) -> int | None:
    """mtb:scale '0'..'6' as ints, same reason."""
    return int(grade) if grade is not None and grade.isdigit() else None


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = REPO_ROOT / "review" / "routes"
CYPHER = Path(__file__).with_name("catalogue.cypher")

BATCH = 500


def templates() -> dict[str, str]:
    """The named statements in catalogue.cypher."""
    out: dict[str, str] = {}
    name = None
    lines: list[str] = []
    for line in CYPHER.read_text(encoding="utf-8").splitlines():
        match = re.match(r"//\s*name:\s*(\w+)", line)
        if match:
            if name and any(ln.strip() for ln in lines):
                out[name] = "\n".join(lines).strip()
            name = match.group(1)
            lines = []
        elif name is not None and not line.startswith("//"):
            lines.append(line)
    if name and any(ln.strip() for ln in lines):
        out[name] = "\n".join(lines).strip()
    return out


def document_rows(document: dict) -> dict[str, Any]:
    """One document -> the rows the loader UNWINDs. Pure, tested.

    Selection properties only. `None` values are kept (SET writes null, which
    in Neo4j removes the property — absent is not zero, in graph form).
    """
    identity = document["identity"]
    measures = document["measures"]
    difficulty = document["difficulty"]
    generation = document.get("provenance", {}).get("generation") or {}
    quality = document["quality"]

    route = {
        "route_id": document["id"],
        "props": {
            "kind": document["kind"],
            # Schema 1.2 carries shape top-level (measured for OSM routes,
            # constructed for generated ones). The fallback chain serves
            # legacy 1.1 documents only: generation shape, then 'named'.
            "shape": document.get("shape", generation.get("shape", "named")),
            "activity": identity.get("activity"),
            "name": identity.get("name"),
            "ref": identity.get("ref"),
            "network": identity.get("network"),
            "waymark": identity.get("waymark"),
            "from": identity.get("from"),
            "to": identity.get("to"),
            "operator": identity.get("operator"),
            "regions": identity.get("regions") or [],
            "osm_relation_id": identity.get("osm_relation_id"),
            "destination_name": (generation.get("destination") or {}).get("name"),
            "destination_kind": (generation.get("destination") or {}).get("kind"),
            "distance_m": measures["distance_m"],
            "ascent_m": measures["ascent_m"],
            "descent_m": measures["descent_m"],
            "lowest_m": measures.get("lowest_m"),
            "highest_m": measures.get("highest_m"),
            "sac_scale": difficulty["sac_scale"],
            "sac_scale_rank": sac_rank(difficulty["sac_scale"]),
            "sac_max": difficulty["sac_max"],
            "sac_max_rank": sac_rank(difficulty["sac_max"]),
            "graded_share": difficulty["graded_share"],
            "surface_dominant": document["surface"]["dominant"],
            "continuous": document["continuity"]["continuous"],
            "pieces": document["continuity"]["pieces"],
            "mtb_rideable": generation.get("mtb_rideable"),
            "mtb_scale": generation.get("mtb_scale"),
            "mtb_scale_rank": mtb_rank(generation.get("mtb_scale")),
            "bike_blocked_m": generation.get("bike_blocked_m"),
            "off_road_share": generation.get("off_road_share"),
            "urban_share": generation.get("urban_share"),
            "retrace_share": generation.get("retrace_share"),
            "score": generation.get("score"),
            "matched_fraction": quality.get("matched_fraction"),
            "warnings": len(quality["warnings"]),
            "places": len(document["places"]),
            "bbox": document["bbox"],
            # The cross-layer contract fields (docs/route-document.md): the
            # API compares these against the document it serves, so a :Route
            # from export N wearing a file from export N-1 fails visibly
            # instead of serving another build's shape under this id.
            "schema_version": document["schema_version"],
            "doc_run_id": document.get("provenance", {}).get("run_id"),
            # Schema 2.0 selection fields: which direction this document
            # walks, its sibling, the corridor it shares with siblings from
            # its terminal, why a broken route is broken, and the class
            # twins the cards and legends style by.
            "direction": document.get("direction"),
            "reverse_of": document.get("reverse_of"),
            # 2.1: of a direction pair, suggest the steep-up walk.
            "recommended": document.get("recommended"),
            "continuity_reason": (document.get("continuity") or {}).get("reason"),
            "divergence_vertex": (document.get("divergence") or {}).get("vertex_id"),
            "approach_m": (document.get("divergence") or {}).get("approach_m"),
            "terminals_reachable": sum(
                1 for term in document.get("terminals") or [] if term.get("reachable")
            ),
            "terminals_total": len(document.get("terminals") or []),
            "distance_class": (document.get("categories") or {}).get("distance_class"),
            "climb_class": (document.get("categories") or {}).get("climb_class"),
            "difficulty_class": (document.get("categories") or {}).get(
                "difficulty_class"
            ),
            "surface_class": (document.get("categories") or {}).get("surface_class"),
        },
    }

    places = []
    passes = []
    for seq, place in enumerate(document["places"]):
        if place.get("lat") is None or place.get("lon") is None:
            continue  # spike-era documents carried no coordinates; current do
        places.append(
            {
                "place_id": place["id"],
                "kind": place["kind"],
                "name": place.get("name"),
                "ele_m": place.get("ele_m"),
                "lon": place["lon"],
                "lat": place["lat"],
            }
        )
        passes.append(
            {
                "route_id": document["id"],
                "place_id": place["id"],
                "seq": seq,
                "offset_m": place["offset_m"],
                "distance_along_m": place.get("distance_along_m"),
                "is_start": bool(place.get("is_start")),
            }
        )

    # Schema 2.0: `start` became `terminals` (1-2 per route, each carrying
    # its own reachability and seasons). The selection surface anchors on the
    # FIRST terminal — the one a loop leaves from, a traverse's A end — and
    # carries the second's reachability as properties, not as a second
    # :Start, until a query needs it.
    terminals = document.get("terminals") or []
    # Anchor on the first REACHABLE terminal: 106 live traverses had an
    # unreachable A end and a reachable B end, and anchoring on [0] blindly
    # gave them a nameless, seasonless :Start while the real trailhead — the
    # one "routes starting near <village>" must match — went unindexed.
    start = next(
        (term for term in terminals if term.get("reachable")),
        terminals[0] if terminals else None,
    )
    start_row = None
    start_link = None
    if start and start.get("vertex_id") is not None:
        lon, lat = start["point"]["coordinates"]
        start_row = {
            "vertex_id": start["vertex_id"],
            "car_free": start["car_free"],
            "names": start.get("names") or [],
            "start_classes": start.get("start_classes") or [],
            "reachable_spring": start["seasons"]["spring"],
            "reachable_summer": start["seasons"]["summer"],
            "reachable_autumn": start["seasons"]["autumn"],
            "reachable_winter": start["seasons"]["winter"],
            "seasons_unverified": start["seasons"]["unverified"],
            "lon": lon,
            "lat": lat,
        }
        start_link = {
            "route_id": document["id"],
            "vertex_id": start["vertex_id"],
            "nearest_m": start["nearest_start_m"],
        }

    return {
        "route": route,
        "places": places,
        "passes": passes,
        "start": start_row,
        "start_link": start_link,
    }


def batches(rows: list, size: int = BATCH):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="map and count, write nothing"
    )
    args = parser.parse_args()

    # vv2-*.json only: the directory also holds the emitters' ownership
    # manifests (dot-prefixed JSON lists), and pathlib's glob matches those.
    files = sorted(DOCUMENTS.glob("vv2-*.json"))
    if not files:
        raise SystemExit(
            f"no route documents under {DOCUMENTS} — emit them first "
            "(export.route_documents, draw.emit)"
        )

    routes, passes, start_links = [], [], []
    places: dict[str, dict] = {}
    starts: dict[int, dict] = {}
    skipped_placeless = 0
    unpublished: dict[str, int] = {}
    for path in files:
        document = json.loads(path.read_text(encoding="utf-8"))
        # The gate. A document on disk is not automatically a route the
        # catalogue serves: export.route_documents can be asked to write the
        # mapped relations for inspection, and they must not reach the graph
        # from there. One rule, read by both publishers (export.document).
        if not published(document["kind"]):
            unpublished[document["kind"]] = unpublished.get(document["kind"], 0) + 1
            continue
        rows = document_rows(document)
        routes.append(rows["route"])
        skipped_placeless += len(document["places"]) - len(rows["passes"])
        passes.extend(rows["passes"])
        for place in rows["places"]:
            places[place["place_id"]] = place
        if rows["start"]:
            starts[rows["start"]["vertex_id"]] = rows["start"]
            start_links.append(rows["start_link"])

    print(
        f"{len(routes):,} routes, {len(places):,} distinct places, "
        f"{len(passes):,} PASSES, {len(starts):,} starts"
    )
    for kind, n in sorted(unpublished.items()):
        print(f"{n:,} {kind} documents skipped: not a published kind")
    if not routes:
        raise SystemExit(
            f"no PUBLISHED documents under {DOCUMENTS} — the catalogue serves "
            f"{sorted(PUBLISHED_KINDS)} (export.document.PUBLISHED_KINDS)"
        )
    if skipped_placeless:
        print(f"{skipped_placeless:,} place references without coordinates skipped")
    if args.dry_run:
        print("--dry-run: nothing written")
        return

    cypher = templates()
    uri = env_value("NEO4J_URI") or (
        f"bolt://127.0.0.1:{env_value('NEO4J_BOLT_PORT') or 7687}"
    )
    run_id = f"neo4j-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    driver = GraphDatabase.driver(
        uri, auth=(env_value("NEO4J_USER"), env_value("NEO4J_PASSWORD"))
    )
    exported_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    with driver.session() as session:
        for name in ("constraints_route", "constraints_place", "constraints_start"):
            session.run(cypher[name])

        owned = session.run(cypher["count_owned"]).single()["owned"]
        # Bounded bites, each its own auto-commit transaction, so the wipe
        # stays under the server's 10s transaction timeout on any cache.
        while session.run(cypher["wipe_owned_batch"], limit=1000).single()["deleted"]:
            pass
        print(f"replaced {owned:,} previously exported/legacy catalogue nodes")

        for batch in batches(routes):
            session.run(
                cypher["load_routes"],
                rows=batch,
                run_id=run_id,
                exported_at=exported_at,
            )
        for batch in batches(list(places.values())):
            session.run(cypher["load_places"], rows=batch, run_id=run_id)
        for batch in batches(list(starts.values())):
            session.run(cypher["load_starts"], rows=batch, run_id=run_id)
        for batch in batches(passes):
            session.run(cypher["link_passes"], rows=batch)
        for batch in batches(start_links):
            session.run(cypher["link_starts"], rows=batch)

        verify = session.run(cypher["verify_counts"]).single()
        print(
            f"in the graph now: {verify['routes']:,} routes "
            f"({verify['generated']:,} generated, {verify['named']:,} named), "
            f"{verify['passes']:,} PASSES"
        )
        if verify["routes"] != len(routes):
            raise SystemExit(
                f"loaded {len(routes)} documents but the graph holds "
                f"{verify['routes']} routes — refusing to call this done"
            )

        print("\nthe selection smoke test - clean routes 8-16 km passing a peak:")
        for record in session.run(
            cypher["sample_selection"], min_m=8000, max_m=16000, limit=5
        ):
            print(
                f"  {record['name'] or record['ref'] or '(unnamed)':<40} "
                f"{record['km']:>5} km  up {record['ascent_m'] or '?':>6}  "
                f"{record['sac_max']:<26} passes {record['peak'] or '(unnamed peak)'}"
            )
    driver.close()

    with connect() as conn:
        # The count assertion the 627-of-1,379 gap earned (2026-08-27): the
        # graph held only the generated routes because only their documents
        # were on disk, and nothing said so. Count what the store DESCRIBES —
        # generated routes in catalogue.route plus the mapped relations the
        # document emitter would emit (its own query, so the criterion cannot
        # drift), minus what policy deliberately holds (the multi-piece
        # matched floor, so held-by-policy never reads as missing-by-
        # accident) — against the GROUNDS actually loaded: a direction pair
        # is two documents over one ground, so documents stopped being the
        # unit the moment pairs landed.
        generated = conn.execute("SELECT count(*) FROM catalogue.route").fetchone()[0]
        mapped = conn.execute(
            f"SELECT count(*) FROM ({RELATIONS}) mapped_routes"
        ).fetchone()[0]
        held = conn.execute(
            """SELECT count(*) FROM qa.v_route v
               JOIN qa.v_route_coverage c USING (rel_id)
               WHERE v.pieces > 1
                 AND (c.matched_fraction IS NULL OR c.matched_fraction < %s)""",
            (MULTI_PIECE_FLOOR,),
        ).fetchone()[0]
        grounds = len(
            {r["route_id"].removesuffix("-fwd").removesuffix("-rev") for r in routes}
        )
        expected = generated + mapped - held
        if grounds != expected:
            print(
                f"\nNOTE: the store describes {generated + mapped:,} routes "
                f"({generated:,} generated + {mapped:,} mapped, of which "
                f"{held:,} are held below the {MULTI_PIECE_FLOOR} multi-piece "
                f"matched floor) but {grounds:,} distinct grounds were on "
                "disk to load. A small residual is usually same-ground "
                "relations folded into one document (the export run's notes "
                "name them); anything larger is a missing set — emit it "
                "(draw.emit for generated, export.route_documents for "
                "mapped) and re-run this load."
            )
        conn.execute(
            "INSERT INTO provenance.build_run (run_id, stage, parameters, counts, finished_at)"
            " VALUES (%s, 'export', %s, %s, now())",
            (
                run_id,
                json.dumps({"builder": "export.neo4j_load", "uri": uri}),
                json.dumps(
                    {
                        "routes": len(routes),
                        "places": len(places),
                        "passes": len(passes),
                        "starts": len(starts),
                        "unpublished": unpublished,
                    }
                ),
            ),
        )
    print(f"\nrun {run_id} in {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    main()
