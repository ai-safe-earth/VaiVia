"""Apply pipeline/sql migrations in filename order against PostGIS.

Every migration must be idempotent — re-running is the normal case, not an
error (the same rule backend/scripts/apply_migrations.py enforces for the
Supabase schema). Each file runs in its own transaction, so a failure rolls
that file back whole.

Run from pipeline/:
    uv run python migrate.py
    uv run python migrate.py --dry-run
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core import connect

MIGRATIONS = Path(__file__).resolve().parent / "sql"


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
                f"would apply {path.name} ({len(path.read_text(encoding='utf-8'))} bytes)"
            )
        return

    with connect() as conn:
        for path in files:
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
            print(f"applied {path.name}")

        rows = conn.execute("""
            SELECT n.nspname, count(c.relname)
            FROM pg_namespace n
            LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relkind = 'r'
            WHERE n.nspname IN ('staging', 'curated', 'qa', 'public')
            GROUP BY n.nspname ORDER BY n.nspname
            """).fetchall()
        print("\nschema now:")
        for schema, tables in rows:
            print(f"  {schema:<10} {tables} table(s)")


if __name__ == "__main__":
    main()
