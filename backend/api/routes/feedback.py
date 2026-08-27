"""Message feedback: a thumbs vote on an assistant answer, per user.

Storage and endpoint together, the favorites.py shape: rows in Supabase
Postgres (infra/supabase/migrations/0004_feedback.sql), writes only through
here as the owner with the user id the gateway verified. A downvote's
``message_id`` joins straight back to messages.content / intent /
result_refs, which is everything scripts/dump_conversation.py and the golden
dataset need to turn it into an eval entry.

``/feedback`` is its own gateway prefix, deliberately outside ``/chat``:
the quota pre-check matches on the ``/chat`` prefix, and a user whose budget
is spent must still be able to say the answer that spent it was bad.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import UserDep

router = APIRouter(tags=["feedback"])


class FeedbackStore(Protocol):
    async def set(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        vote: int,
        comment: str | None,
        expected: str | None,
    ) -> bool:
        """Upsert one vote. False means the message is not in this user's
        conversation — the route turns that into an honest 404."""
        ...


class InMemoryFeedback:
    """Dev/test double, mirroring InMemoryFavorites: real upsert semantics,
    no persistence. It skips the ownership check on purpose —
    chat.store.InMemoryStore.add_message returns ids it never stores, so
    there is nothing to check against; in production the foreign keys and
    the single-statement upsert in PostgresFeedback enforce it."""

    def __init__(self) -> None:
        self._votes: dict[tuple[str, str], tuple[int, str | None, str | None]] = {}

    async def set(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        vote: int,
        comment: str | None,
        expected: str | None,
    ) -> bool:
        self._votes[(user_id, message_id)] = (vote, comment, expected)
        return True


class PostgresFeedback:
    """The real store. Ownership lives in the SQL: the insert only selects a
    row when the message belongs to a conversation this user owns, so the
    check and the upsert are one statement with no race between them."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def set(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        vote: int,
        comment: str | None,
        expected: str | None,
    ) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchval(
                """
                INSERT INTO message_feedback
                    (user_id, message_id, conversation_id, vote, comment,
                     expected)
                SELECT c.user_id, m.id, c.id, $3, $4, $5
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE c.user_id = $1::uuid
                  AND m.id = $2::uuid
                ON CONFLICT (user_id, message_id) DO UPDATE
                    SET vote = excluded.vote,
                        comment = excluded.comment,
                        expected = excluded.expected,
                        updated_at = now()
                RETURNING message_id
                """,
                user_id,
                message_id,
                vote,
                comment,
                expected,
            )
        return row is not None


class FeedbackIn(BaseModel):
    message_id: str
    conversation_id: str
    vote: Literal[-1, 1]
    # A full overwrite on every upsert, last write wins: a vote-only re-vote
    # clears a stale comment, which is right — the comment was about the old
    # vote.
    comment: str | None = Field(default=None, max_length=2000)
    # "How should it be instead?" — the second half of the downvote's ask.
    expected: str | None = Field(default=None, max_length=2000)


class FeedbackState(BaseModel):
    message_id: str
    vote: int


def _store(request: Request) -> FeedbackStore:
    return request.app.state.feedback


@router.post("/feedback", response_model=FeedbackState)
async def set_feedback(
    body: FeedbackIn, request: Request, user_id: UserDep
) -> FeedbackState:
    """Idempotent upsert: a re-vote flips, a comment updates."""
    ok = await _store(request).set(
        user_id,
        body.conversation_id,
        body.message_id,
        body.vote,
        body.comment,
        body.expected,
    )
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"no message {body.message_id!r} for this user"
        )
    return FeedbackState(message_id=body.message_id, vote=body.vote)
