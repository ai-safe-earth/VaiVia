"""Thin async wrapper around the Neo4j driver.

Usage:
    async with Neo4jClient() as db:
        await db.run("MATCH (n) RETURN count(n) AS c")
"""

import asyncio
import logging
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from neo4j import AsyncDriver, AsyncGraphDatabase

from core.config import get_settings

logger = logging.getLogger(__name__)


class Neo4jClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.neo4j_password:
            raise RuntimeError(
                "NEO4J_PASSWORD is not set — copy .env.example to .env first"
            )
        self._database = settings.neo4j_database
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )

    async def connect(self) -> None:
        await self._driver.verify_connectivity()

    async def close(self) -> None:
        await self._driver.close()

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def run_named(self, name: str, /, **params: Any) -> list[dict[str, Any]]:
        """Run a template from graph/queries.cypher by name (parameters only).

        `name` is positional-only so a Cypher parameter may also be called
        $name (poi_by_name uses one).
        """
        from graph.query_loader import get_query

        return await self.run(get_query(name), **params)

    async def run(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
        """Execute one parameterized query, return records as dicts."""
        result = await self._driver.execute_query(
            query, params, database_=self._database
        )
        return [record.data() for record in result.records]

    async def run_batched(
        self, query: str, rows: list[dict[str, Any]], /, batch_size: int = 1000
    ) -> None:
        """Execute an UNWIND-style query over `rows` in batches.

        `query` must reference the batch as $rows, e.g.:
            UNWIND $rows AS row MERGE (n:Label {id: row.id}) SET n += row.props
        """
        for start in range(0, len(rows), batch_size):
            await self.run(query, rows=rows[start : start + batch_size])

    async def run_cypher_file(self, path: Path) -> int:
        """Run each ;-terminated statement in a .cypher file. Returns statement count.

        Comments are stripped before splitting. Statements themselves contain no
        literal semicolons, but *comments* do — schema.cypher's header alone has
        several — and splitting first cuts a comment in half, leaving its tail
        looking like a statement. That is how "durations are MINUTES." reached
        the server as Cypher.
        """
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        statements = split_statements(text)
        for stmt in statements:
            logger.info("cypher: %s...", stmt.splitlines()[-1][:80])
            await self.run(stmt)
        return len(statements)


def strip_line_comments(text: str) -> str:
    """Remove `//` comments, leaving `//` inside string literals alone."""
    kept = []
    for line in text.splitlines():
        quote: str | None = None
        cut = None
        for index, char in enumerate(line):
            if quote is not None:
                if char == quote and not _is_escaped(line, index):
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "/" and line[index + 1 : index + 2] == "/":
                cut = index
                break
        kept.append(line if cut is None else line[:cut])
    return "\n".join(kept)


def _is_escaped(line: str, index: int) -> bool:
    backslashes = 0
    while index - 1 - backslashes >= 0 and line[index - 1 - backslashes] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def split_statements(text: str) -> list[str]:
    """Statements from a .cypher file: comments removed, split on semicolons."""
    stripped = strip_line_comments(text)
    return [stmt.strip() for stmt in stripped.split(";") if stmt.strip()]
