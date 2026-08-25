"""Audit the catalogue against the document store — the desync detector.

For every ``:Route`` in Neo4j, assert the document store holds a file named
after its id whose contents agree with the catalogue row: the document's own
``id``, its ``schema_version``, and which export run produced it. The API now
refuses to serve a disagreement (``document_mismatch`` / ``build_mismatch``
in ``api/routes/routing.py``); this script is the fleet-wide version of the
same checks, run after any export or reload — a stale store fails HERE, not
under a user's click.

Quarantined routes (``warnings > 0``) are audited too: they answer no user,
but a desynced quarantined row is still a desync.

Usage (live Neo4j + ROUTE_DOCUMENTS_DIR required)::

    uv run python -m scripts.audit_catalogue_documents

Exit codes: 0 clean, 1 mismatches found, 2 not configured.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from api.routes.routing import SUPPORTED_SCHEMA_VERSIONS
from core.config import get_settings
from graph.neo4j_client import Neo4jClient


async def _catalogue_index() -> list[dict]:
    async with Neo4jClient() as client:
        return await client.run_named("catalogue_audit_index")


def audit(rows: list[dict], store: Path) -> int:
    """Compare the catalogue rows against the files on disk. Sync on purpose:
    the graph read is done, and an offline audit may block on the disk."""
    problems: list[str] = []
    unstamped = 0
    for row in rows:
        route_id = row["route_id"]
        path = store / f"{route_id}.json"
        if not path.is_file():
            problems.append(f"{route_id}: no document in the store")
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            problems.append(f"{route_id}: unreadable document ({error})")
            continue
        if document.get("id") != route_id:
            problems.append(f"{route_id}: document carries id {document.get('id')!r}")
        version = document.get("schema_version")
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            problems.append(f"{route_id}: unsupported schema_version {version!r}")
        document_run = document.get("provenance", {}).get("run_id")
        catalogue_run = row.get("doc_run_id")
        if catalogue_run is None:
            # The graph predates the doc_run_id stamp: nothing to compare.
            # Counted and named, because "cannot check" must not read as
            # "checked and fine" — a reload closes it.
            unstamped += 1
        elif document_run != catalogue_run:
            problems.append(
                f"{route_id}: catalogued from export {catalogue_run!r} but the "
                f"document is from {document_run!r}"
            )

    # The other direction, informational: files no :Route names. Expected
    # for a store that outlived a partial load; a flood of them after a full
    # export means the loader dropped rows.
    catalogued = {row["route_id"] for row in rows}
    # vv2-*.json only: the store also holds the emitters' dot-prefixed
    # ownership manifests, and pathlib's glob matches hidden files.
    documents = list(store.glob("vv2-*.json"))
    orphans = [p.stem for p in documents if p.stem not in catalogued]

    print(f"routes in catalogue : {len(rows)}")
    print(f"documents in store  : {len(documents)}")
    print(f"desyncs             : {len(problems)}")
    print(f"unstamped (no doc_run_id on the node — reload to close): {unstamped}")
    print(f"documents no route names (informational): {len(orphans)}")
    for line in problems[:50]:
        print(f"  MISMATCH {line}")
    if len(problems) > 50:
        print(f"  … and {len(problems) - 50} more")

    return 1 if problems else 0


def main() -> int:
    settings = get_settings()
    if not settings.route_documents_dir:
        print("ROUTE_DOCUMENTS_DIR is unset — nothing to audit against")
        return 2
    rows = asyncio.run(_catalogue_index())
    return audit(rows, Path(settings.route_documents_dir))


if __name__ == "__main__":
    sys.exit(main())
