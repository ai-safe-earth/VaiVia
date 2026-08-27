"""Render a conversation as markdown: the turns, the plans, the notes.

The log already exists — every turn's user text, assistant prose, intents and
result ids are in Supabase (messages.intent, messages.result_refs); what was
missing was the review surface. This renders it for reading in a terminal or
pasting into a review: per turn the message, the standing plan re-spoken
through readback.describe (a deterministic recompute, so the composer's own
decisions — the widened band, the capped difficulty — are on the record), and
the returned route ids.

A user message starting with BUG / FIX / NOTE is flagged as a heading, so a
problem typed mid-use ("BUG: the routes disappeared") is findable later with
the surrounding turns as its evidence.

Run from backend/ (reads DATABASE_URL like apply_migrations):
    uv run python -m scripts.dump_conversation             # newest conversation
    uv run python -m scripts.dump_conversation --conversation <uuid>
    uv run python -m scripts.dump_conversation --all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from typing import Any

import asyncpg

from chat.composer import standing_load
from chat.readback import describe
from core.pg import asyncpg_ssl
from scripts.apply_migrations import load_database_url

NOTE_PATTERN = re.compile(r"^(BUG|FIX|NOTE)\b", re.IGNORECASE)

CONVERSATIONS = """
SELECT id::text, title, created_at
FROM conversations
ORDER BY created_at DESC
"""

MESSAGES = """
SELECT role, content, intent, result_refs, created_at
FROM messages
WHERE conversation_id = $1::uuid
ORDER BY created_at
"""


def _jsonb(value: Any) -> dict | None:
    """asyncpg returns jsonb as text (no codec registered); tolerate both."""
    if value is None or isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def render_message(role: str, content: str, intent: Any, refs: Any) -> list[str]:
    """One turn -> markdown lines. Pure, so it is tested."""
    lines: list[str] = []
    if role == "user":
        if NOTE_PATTERN.match(content.strip()):
            lines.append(f"### 🏷 {content.strip()}")
        else:
            lines.append(f"**user:** {content}")
        return lines

    lines.append(f"**assistant:** {content}")
    data = _jsonb(intent) or {}
    subqueries = data.get("subqueries") or []
    if subqueries:
        kinds = ", ".join(s.get("kind", "?") for s in subqueries)
        lines.append(f"- intents: {kinds}")
    plan = standing_load(data.get("standing"))
    if plan is not None:
        for row in describe(plan):
            lines.append(f"- {row['key']}: {row['value']}")
    refs_data = _jsonb(refs) or {}
    ids = (refs_data.get("loop_ids") or []) + (refs_data.get("trail_ids") or [])
    if ids:
        lines.append(f"- results: {', '.join(ids)}")
    return lines


def render_conversation(
    conversation: dict[str, Any], messages: list[dict[str, Any]]
) -> str:
    title = conversation.get("title") or "(untitled)"
    day = str(conversation["created_at"])[:19]
    out = [f"## {title} — {day}", ""]
    for message in messages:
        out.extend(
            render_message(
                message["role"],
                message["content"],
                message.get("intent"),
                message.get("result_refs"),
            )
        )
        out.append("")
    return "\n".join(out)


async def main() -> None:
    # Windows consoles default to a legacy codepage; the dump is UTF-8 prose.
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation", help="one conversation by id")
    parser.add_argument("--all", action="store_true", help="every conversation")
    args = parser.parse_args()

    url = load_database_url()
    conn = await asyncpg.connect(url, ssl=asyncpg_ssl(url), statement_cache_size=0)
    try:
        rows = [dict(r) for r in await conn.fetch(CONVERSATIONS)]
        if args.conversation:
            rows = [r for r in rows if r["id"] == args.conversation]
            if not rows:
                raise SystemExit(f"no conversation {args.conversation}")
        elif not args.all:
            rows = rows[:1]  # --latest is the default
        if not rows:
            raise SystemExit("no conversations")
        for conversation in rows:
            messages = [dict(r) for r in await conn.fetch(MESSAGES, conversation["id"])]
            print(render_conversation(conversation, messages))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
