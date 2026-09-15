"""Apply the pipeline/sql/v2 migrations in filename order against PostGIS.

Every migration must be idempotent — re-running is the normal case, not an
error (the same rule backend/scripts/apply_migrations.py enforces for the
Supabase schema). Each file runs in its own transaction, so a failure rolls
that file back whole.

``sql/v2/`` is the only chain. Its baseline creates the layout (staging /
source_map / catalogue / qa / provenance) from scratch and stamps
``provenance.chain_version``, which the run checks afterwards.

There was a v1 chain, and a one-shot ``convert_v2.py`` that renamed a v1 store
into this layout. Both were deleted once every store had been converted; git
holds them if a pre-cutover backup ever surfaces.

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
