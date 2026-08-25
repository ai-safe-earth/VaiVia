"""Apply the pipeline/sql/v2 migrations in filename order against PostGIS.

Every migration must be idempotent — re-running is the normal case, not an
error (the same rule backend/scripts/apply_migrations.py enforces for the
Supabase schema). Each file runs in its own transaction, so a failure rolls
that file back whole.

Two chains exist and must never cross:

* ``sql/v2/`` is the live chain. Its baseline creates the v2 layout
  (staging / source_map / catalogue / qa / provenance) from scratch and
  stamps ``provenance.chain_version``.
* ``sql/v1/`` is frozen history — the chain that built the store when one
  ``curated`` schema did both the source-map and catalogue jobs. It is
  never applied by this script again: replaying it against a v2 store
  would resurrect empty ``curated.*`` tables beside the renamed full ones,
  which is how a store quietly becomes two stores.

A store built by v1 carries data but no chain stamp; this script refuses it
and points at ``convert_v2.py``, the one-shot rename that brings it here.

Run from pipeline/:
    uv run python migrate.py
    uv run python migrate.py --dry-run
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core import connect

MIGRATIONS = Path(__file__).resolve().parent / "sql" / "v2"
CHAIN_VERSION = 2


def store_state(conn) -> str:
    """'v2', 'v1', or 'fresh' — what chain this store belongs to."""
    stamped = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
        " WHERE table_schema = 'provenance' AND table_name = 'chain_version')"
    ).fetchone()[0]
    if stamped:
        return "v2"
    v1 = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.schemata"
        " WHERE schema_name = 'curated')"
    ).fetchone()[0]
    return "v1" if v1 else "fresh"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        raise SystemExit(f"no .sql files under {MIGRATIONS}")

    if args.dry_run:
        for path in files:
            print(
                f"would apply v2/{path.name} "
                f"({len(path.read_text(encoding='utf-8'))} bytes)"
            )
        return

    with connect() as conn:
        state = store_state(conn)
        if state == "v1":
            raise SystemExit(
                "this store was built by the v1 chain (a `curated` schema and no "
                "chain stamp). Run `uv run python convert_v2.py` once — the "
                "in-place rename to the v2 layout — and then this script."
            )

        for path in files:
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
            print(f"applied v2/{path.name}")

        version = conn.execute(
            "SELECT max(version) FROM provenance.chain_version"
        ).fetchone()[0]
        if version != CHAIN_VERSION:
            raise SystemExit(f"chain stamp reads {version}, expected {CHAIN_VERSION}")

        rows = conn.execute("""
            SELECT n.nspname, count(c.relname)
            FROM pg_namespace n
            LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relkind = 'r'
            WHERE n.nspname IN
                ('staging', 'source_map', 'catalogue', 'qa', 'provenance')
            GROUP BY n.nspname ORDER BY n.nspname
            """).fetchall()
        print("\nschema now:")
        for schema, tables in rows:
            print(f"  {schema:<11} {tables} table(s)")


if __name__ == "__main__":
    main()
