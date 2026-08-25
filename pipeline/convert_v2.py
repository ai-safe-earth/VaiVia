"""One-shot conversion of a v1 store to the v2 schema layout.

v1 grew one `curated` schema doing two jobs — the source map (network,
places, relation links) and the catalogue (generated routes) — with the
provenance ledger loose in `public`. v2 names the jobs:

    staging     (unchanged)      raw sources
    source_map  (was curated)    vertex, edge, edge_route, place, vertex_degree
    catalogue   (from curated)   route, route_edge — and the tables to come
    qa          (unchanged)      findings, fixes, review views
    provenance  (from public)    build_run, chain_version

Everything moves by ALTER, never copy: views, the matview, foreign keys and
indexes all reference tables by OID, so they track the rename without being
touched. The check that the move went right is the same one every pipeline
pass uses — the total network length must not move at all.

This script runs ONCE per store (it stamps `provenance.chain_version = 2`
and refuses a second run); a fresh database never runs it, because the v2
baseline migration creates the v2 layout directly. Rehearse against a
pg_dump restore before the live store:

    uv run python convert_v2.py --url postgresql://vaivia:...@127.0.0.1:5433/vaivia_geo_v2rehearsal
    uv run python convert_v2.py            # the live store, from core config
"""

from __future__ import annotations

import argparse

import psycopg

from core import database_url

CHECKS = {
    "edges": "SELECT count(*) FROM {sm}.edge",
    "network_km": "SELECT round(sum(length_m)::numeric/1000, 1) FROM {sm}.edge",
    "vertices": "SELECT count(*) FROM {sm}.vertex",
    "places": "SELECT count(*) FROM {sm}.place",
    "routes": "SELECT count(*) FROM {cat}.route",
    "route_edges": "SELECT count(*) FROM {cat}.route_edge",
    "edge_routes": "SELECT count(*) FROM {sm}.edge_route",
    "build_runs": "SELECT count(*) FROM {prov}.build_run",
    "qa_findings": "SELECT count(*) FROM qa.finding",
}

CONVERSION = """
CREATE SCHEMA provenance;
CREATE SCHEMA catalogue;
ALTER SCHEMA curated RENAME TO source_map;
ALTER TABLE source_map.route SET SCHEMA catalogue;
ALTER TABLE source_map.route_edge SET SCHEMA catalogue;
ALTER TABLE public.build_run SET SCHEMA provenance;
CREATE TABLE provenance.chain_version (
    version int PRIMARY KEY,
    converted_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO provenance.chain_version (version) VALUES (2);
"""


def measure(conn: psycopg.Connection, sm: str, cat: str, prov: str) -> dict:
    out = {}
    for name, sql in CHECKS.items():
        out[name] = conn.execute(sql.format(sm=sm, cat=cat, prov=prov)).fetchone()[0]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=None, help="override the configured store")
    args = parser.parse_args()

    with psycopg.connect(args.url or database_url()) as conn:
        already = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
            " WHERE table_schema = 'provenance' AND table_name = 'chain_version')"
        ).fetchone()[0]
        if already:
            raise SystemExit(
                "already v2 (provenance.chain_version exists) — nothing to do"
            )
        v1 = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata"
            " WHERE schema_name = 'curated')"
        ).fetchone()[0]
        if not v1:
            raise SystemExit(
                "no `curated` schema — not a v1 store; a fresh database "
                "gets v2 from migrate.py directly"
            )

        before = measure(conn, "curated", "curated", "public")
        print("before:", before)

        with conn.transaction():
            conn.execute(CONVERSION)

        after = measure(conn, "source_map", "catalogue", "provenance")
        print("after: ", after)
        if before != after:
            raise SystemExit(
                "conversion moved data, which a rename cannot do — restore the dump: "
                f"{ {k: (before[k], after[k]) for k in before if before[k] != after[k]} }"
            )
        # Views tracked the rename by OID; prove one from each family answers.
        for probe in (
            "SELECT count(*) FROM qa.v_network",
            "SELECT count(*) FROM qa.v_start",
            "SELECT count(*) FROM qa.v_draw",
            "SELECT count(*) FROM source_map.vertex_degree",
        ):
            conn.execute(probe).fetchone()
        print("v2 layout stamped; every qa view family answers")


if __name__ == "__main__":
    main()
