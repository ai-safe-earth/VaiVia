"""Conversation history, cost ledger, and quota state (Supabase Postgres).

Schema: infra/supabase/migrations/0001_chat_and_quotas.sql. The in-memory
implementation is the test double and needs no database.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol
from uuid import uuid4

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 10


@dataclass
class StoredMessage:
    role: str
    content: str


class ConversationStore(Protocol):
    async def history(self, conversation_id: str) -> list[StoredMessage]: ...

    async def ensure_conversation(
        self, conversation_id: str | None, user_id: str
    ) -> str: ...

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        intent: dict[str, Any] | None = None,
        result_refs: dict[str, Any] | None = None,
    ) -> str: ...

    async def record_usage(
        self,
        user_id: str,
        message_id: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None: ...

    async def tokens_used_today(self, user_id: str) -> int: ...


@dataclass
class InMemoryStore:
    """Test/dev store. Mirrors the Postgres semantics, including daily reset."""

    conversations: dict[str, str] = field(default_factory=dict)  # id -> user_id
    messages: dict[str, list[StoredMessage]] = field(default_factory=dict)
    usage: dict[tuple[str, date], int] = field(default_factory=dict)

    async def history(self, conversation_id: str) -> list[StoredMessage]:
        return list(self.messages.get(conversation_id, []))[-MAX_HISTORY_TURNS * 2 :]

    async def ensure_conversation(
        self, conversation_id: str | None, user_id: str
    ) -> str:
        if conversation_id and conversation_id in self.conversations:
            if self.conversations[conversation_id] != user_id:
                raise PermissionError("conversation belongs to another user")
            return conversation_id
        new_id = conversation_id or str(uuid4())
        self.conversations[new_id] = user_id
        self.messages.setdefault(new_id, [])
        return new_id

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        intent: dict[str, Any] | None = None,
        result_refs: dict[str, Any] | None = None,
    ) -> str:
        self.messages.setdefault(conversation_id, []).append(
            StoredMessage(role=role, content=content)
        )
        return str(uuid4())

    async def record_usage(
        self,
        user_id: str,
        message_id: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        key = (user_id, date.today())
        self.usage[key] = self.usage.get(key, 0) + input_tokens + output_tokens

    async def tokens_used_today(self, user_id: str) -> int:
        return self.usage.get((user_id, date.today()), 0)


class PostgresStore:
    """asyncpg-backed store. Used in deployment; exercised by the live smoke test."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def history(self, conversation_id: str) -> list[StoredMessage]:
        rows = await self._pool.fetch(
            """
            SELECT role, content FROM messages
            WHERE conversation_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT $2
            """,
            conversation_id,
            MAX_HISTORY_TURNS * 2,
        )
        return [
            StoredMessage(role=r["role"], content=r["content"]) for r in reversed(rows)
        ]

    async def ensure_conversation(
        self, conversation_id: str | None, user_id: str
    ) -> str:
        if conversation_id:
            owner = await self._pool.fetchval(
                "SELECT user_id::text FROM conversations WHERE id = $1::uuid",
                conversation_id,
            )
            if owner is None:
                raise LookupError("conversation not found")
            if owner != user_id:
                raise PermissionError("conversation belongs to another user")
            return conversation_id
        return await self._pool.fetchval(
            "INSERT INTO conversations (user_id) VALUES ($1::uuid) RETURNING id::text",
            user_id,
        )

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        intent: dict[str, Any] | None = None,
        result_refs: dict[str, Any] | None = None,
    ) -> str:
        import json

        return await self._pool.fetchval(
            """
            INSERT INTO messages (conversation_id, role, content, intent, result_refs)
            VALUES ($1::uuid, $2, $3, $4::jsonb, $5::jsonb)
            RETURNING id::text
            """,
            conversation_id,
            role,
            content,
            json.dumps(intent) if intent else None,
            json.dumps(result_refs) if result_refs else None,
        )

    async def record_usage(
        self,
        user_id: str,
        message_id: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO usage_ledger
                    (user_id, message_id, model, input_tokens, output_tokens)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5)
                """,
                user_id,
                message_id,
                model,
                input_tokens,
                output_tokens,
            )
            await conn.execute(
                """
                INSERT INTO daily_quotas (user_id, day, tokens_used, requests)
                VALUES ($1::uuid, CURRENT_DATE, $2, 1)
                ON CONFLICT (user_id, day) DO UPDATE
                SET tokens_used = daily_quotas.tokens_used + EXCLUDED.tokens_used,
                    requests = daily_quotas.requests + 1
                """,
                user_id,
                input_tokens + output_tokens,
            )

    async def tokens_used_today(self, user_id: str) -> int:
        value = await self._pool.fetchval(
            """
            SELECT tokens_used FROM daily_quotas
            WHERE user_id = $1::uuid AND day = CURRENT_DATE
            """,
            user_id,
        )
        return int(value or 0)
