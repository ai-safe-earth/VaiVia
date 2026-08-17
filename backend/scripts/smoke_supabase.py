"""Live round-trip of PostgresStore against the real Supabase database.

Everything runs inside one transaction that is always rolled back, so the check
exercises the real schema, real foreign keys and real SQL without leaving a
test user, conversation or ledger row behind. Run from ``backend/``::

    uv run python -m scripts.smoke_supabase

Requires ``DATABASE_URL`` (env or ``backend/.env``). Exits non-zero on the first
failed assertion.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import asyncpg

from chat.store import PostgresStore
from scripts.apply_migrations import load_database_url, redact


class SingleConnectionPool:
    """Adapts one asyncpg Connection to the small pool surface the store uses.

    PostgresStore calls ``fetch``/``fetchval``/``execute`` directly and opens
    ``async with pool.acquire() as conn, conn.transaction()`` in record_usage.
    Handing back the same connection keeps every statement inside the caller's
    outer transaction, so the rollback covers all of it (the inner
    ``transaction()`` degrades to a savepoint, which is exactly what we want).
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:
        return await self._conn.fetch(*args, **kwargs)

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        return await self._conn.fetchval(*args, **kwargs)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return await self._conn.execute(*args, **kwargs)

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


class SmokeFailure(Exception):
    pass


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    print(f"  FAIL  {label} {detail}")
    raise SmokeFailure(label)


async def run(url: str) -> int:
    print(f"target : {redact(url)}\n")
    conn = await asyncpg.connect(url, ssl="require", statement_cache_size=0)
    tx = conn.transaction()
    await tx.start()
    try:
        store = PostgresStore(SingleConnectionPool(conn))

        # A conversation's user_id references auth.users, so the FK needs a real
        # row. It disappears with the rollback.
        user_id = await conn.fetchval(
            """
            INSERT INTO auth.users (id, aud, role, email)
            VALUES (gen_random_uuid(), 'authenticated', 'authenticated', $1)
            RETURNING id::text
            """,
            f"smoke-{uuid4().hex[:8]}@example.test",
        )
        check("created a throwaway auth user", bool(user_id))

        conversation_id = await store.ensure_conversation(None, user_id)
        check("ensure_conversation creates a conversation", bool(conversation_id))

        same = await store.ensure_conversation(conversation_id, user_id)
        check(
            "ensure_conversation is idempotent for the owner",
            same == conversation_id,
        )

        user_msg_id = await store.add_message(
            conversation_id, "user", "any easy trails near Lecco?"
        )
        assistant_id = await store.add_message(
            conversation_id,
            "assistant",
            "Two easy loops nearby.",
            intent={"type": "trail_search", "difficulty": "easy"},
            result_refs={"trail_ids": [1, 2]},
        )
        check("add_message returns an id", bool(assistant_id))

        # created_at defaults to now(), which in Postgres is *transaction start*
        # time -- so both rows above share a timestamp here, while in production
        # each insert autocommits separately and gets a distinct one. Spread them
        # apart so the ordering assertion tests the query, not this transaction.
        for offset, message_id in ((2, user_msg_id), (1, assistant_id)):
            await conn.execute(
                "UPDATE messages SET created_at = now() - make_interval(secs => $1)"
                " WHERE id = $2::uuid",
                offset,
                message_id,
            )

        history = await store.history(conversation_id)
        check("history returns both turns", len(history) == 2, f"got {len(history)}")
        check(
            "history is chronological, not reversed",
            [m.role for m in history] == ["user", "assistant"],
            f"got {[m.role for m in history]}",
        )

        before = await store.tokens_used_today(user_id)
        check("a fresh user has used no tokens", before == 0, f"got {before}")

        await store.record_usage(user_id, assistant_id, "gpt-4o-mini", 100, 50)
        after = await store.tokens_used_today(user_id)
        check("record_usage sums input+output", after == 150, f"got {after}")

        await store.record_usage(user_id, assistant_id, "gpt-4o-mini", 10, 5)
        accumulated = await store.tokens_used_today(user_id)
        check(
            "a second call accumulates rather than replacing",
            accumulated == 165,
            f"got {accumulated}",
        )

        requests = await conn.fetchval(
            "SELECT requests FROM daily_quotas WHERE user_id = $1::uuid"
            " AND day = CURRENT_DATE",
            user_id,
        )
        check("daily_quotas counts requests", requests == 2, f"got {requests}")

        # The ownership guard is the load-bearing security check here: one user
        # must never read another's conversation.
        stranger = str(uuid4())
        try:
            await store.ensure_conversation(conversation_id, stranger)
            check("a stranger cannot claim someone else's conversation", False)
        except PermissionError:
            check("a stranger cannot claim someone else's conversation", True)

        try:
            await store.ensure_conversation(str(uuid4()), user_id)
            check("an unknown conversation id raises LookupError", False)
        except LookupError:
            check("an unknown conversation id raises LookupError", True)

        return 0
    except SmokeFailure:
        return 1
    finally:
        await tx.rollback()
        left = await conn.fetchval("SELECT count(*) FROM conversations")
        print(f"\nrolled back - conversations left in the table: {left}")
        await conn.close()


def main() -> int:
    return asyncio.run(run(load_database_url()))


if __name__ == "__main__":
    sys.exit(main())
