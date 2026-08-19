"""Apply the Supabase SQL migrations in order against ``DATABASE_URL``.

The Supabase CLI expects its own ``supabase/migrations`` layout and a linked
project; this repo keeps migrations under ``infra/supabase/migrations/`` and
talks to the database directly, so this runner applies them with asyncpg.

Every migration must be idempotent -- re-running it is the normal case, not an
error. Run from ``backend/``::

    uv run python -m scripts.apply_migrations
    uv run python -m scripts.apply_migrations --dry-run

Reads ``DATABASE_URL`` from the environment or ``backend/.env``. Note that the
direct connection host (``db.<ref>.supabase.co``) publishes an AAAA record only
and is unreachable without IPv6; use the Supavisor pooler in session mode.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

from core.pg import asyncpg_ssl

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "supabase" / "migrations"


def load_database_url() -> str:
    """DATABASE_URL from the process env, falling back to backend/.env."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "DATABASE_URL":
                return value.strip()
    raise SystemExit("DATABASE_URL is not set (env or backend/.env)")


def redact(url: str) -> str:
    """Connection string with the password removed, safe to log."""
    if "@" not in url or "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    creds, _, host = rest.rpartition("@")
    user = creds.partition(":")[0]
    return f"{scheme}://{user}:***@{host}"


async def apply(url: str, dry_run: bool) -> int:
    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        print(f"No .sql files under {MIGRATIONS}")
        return 1

    print(f"target : {redact(url)}")
    print(f"source : {MIGRATIONS}")
    if dry_run:
        for path in files:
            size = len(path.read_text(encoding="utf-8"))
            print(f"  would apply {path.name} ({size} bytes)")
        return 0

    conn = await asyncpg.connect(url, ssl=asyncpg_ssl(url), statement_cache_size=0)
    try:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            # Each migration runs in its own transaction: a failure rolls that
            # file back whole rather than leaving the schema half-built.
            async with conn.transaction():
                await conn.execute(sql)
            print(f"  applied {path.name}")

        tables = await conn.fetch("""
            select c.relname as name,
                   c.relrowsecurity as rls,
                   (select count(*) from pg_policies p
                     where p.schemaname = 'public'
                       and p.tablename = c.relname) as policies
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind = 'r'
            order by c.relname
            """)
        print("\nschema now:")
        for row in tables:
            print(
                f"  {row['name']:<16} rls={'on' if row['rls'] else 'OFF':<3}"
                f" policies={row['policies']}"
            )
        if not tables:
            print("  (no tables in public)")
    finally:
        await conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="list migrations without applying"
    )
    args = parser.parse_args(argv)
    return asyncio.run(apply(load_database_url(), args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
