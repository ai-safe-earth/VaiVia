"""Chat orchestration: message -> intent -> graph query -> grounded answer.

The pipeline, and why it is shaped this way:

  1. Quota check BEFORE any model call — the cheapest refusal is the one that
     spends nothing (the gateway pre-checks too; this is the authoritative one).
  2. Intent extraction — the model's only structured output, schema-validated.
  3. Template dispatch — a Python dict maps intent kind to a named template.
     The model never names a template and never sees Cypher.
  4. Grounded answer — the model writes prose over results it is handed. Every
     trail id in the response also appears in result_refs, so the frontend can
     render exactly what the graph returned.

Events are emitted as an async stream so the API layer can serve SSE without
knowing anything about the LLM.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from chat.intents import (
    ClarifyIntent,
    RouteIntent,
    TrailSearchIntent,
)
from chat.llm import LLMClient, results_to_json
from chat.store import ConversationStore
from core.config import get_settings
from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

SEARCH_RESULT_LIMIT = 5


@dataclass
class ChatEvent:
    event: str
    data: dict[str, Any]


class QuotaExceeded(Exception):
    def __init__(self, used: int, limit: int) -> None:
        super().__init__(f"daily token quota exhausted ({used}/{limit})")
        self.used = used
        self.limit = limit


class ChatOrchestrator:
    def __init__(
        self, db: Neo4jClient, llm: LLMClient, store: ConversationStore
    ) -> None:
        self._db = db
        self._llm = llm
        self._store = store

    async def run(
        self, user_id: str, message: str, conversation_id: str | None = None
    ) -> AsyncIterator[ChatEvent]:
        settings = get_settings()

        used = await self._store.tokens_used_today(user_id)
        if used >= settings.daily_token_quota_per_user:
            raise QuotaExceeded(used, settings.daily_token_quota_per_user)

        conversation_id = await self._store.ensure_conversation(
            conversation_id, user_id
        )
        yield ChatEvent("conversation", {"conversation_id": conversation_id})

        history = [
            {"role": m.role, "content": m.content}
            for m in await self._store.history(conversation_id)
        ]
        await self._store.add_message(conversation_id, "user", message)

        intent_result = await self._llm.extract_intent(message, history)
        intent = intent_result.envelope.intent
        yield ChatEvent("intent", intent.model_dump())
        logger.info("intent extracted", extra={"kind": intent.kind})

        results, refs = await self._dispatch(intent)
        yield ChatEvent("results", {"kind": intent.kind, **results})

        answer_parts: list[str] = []
        if isinstance(intent, ClarifyIntent):
            # No model call: the clarification is the model's own structured
            # output, so streaming it back costs nothing extra.
            answer_parts.append(intent.question)
            yield ChatEvent("token", {"delta": intent.question})
        else:
            async for delta in self._llm.stream_answer(
                message, results_to_json(results), history
            ):
                answer_parts.append(delta)
                yield ChatEvent("token", {"delta": delta})

        answer = "".join(answer_parts)
        message_id = await self._store.add_message(
            conversation_id,
            "assistant",
            answer,
            intent=intent.model_dump(),
            result_refs=refs,
        )

        answer_usage = self._llm.last_answer_usage()
        total_in = intent_result.usage.input_tokens + answer_usage.input_tokens
        total_out = intent_result.usage.output_tokens + answer_usage.output_tokens
        await self._store.record_usage(
            user_id, message_id, settings.intent_model, total_in, total_out
        )

        yield ChatEvent(
            "done",
            {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "usage": {"input_tokens": total_in, "output_tokens": total_out},
            },
        )

    async def _dispatch(
        self, intent: TrailSearchIntent | RouteIntent | ClarifyIntent
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Intent -> named template. The only place intents touch the graph."""
        if isinstance(intent, TrailSearchIntent):
            return await self._search(intent)
        if isinstance(intent, RouteIntent):
            return await self._route(intent)
        return {"clarification": intent.question}, {}

    async def _search(
        self, intent: TrailSearchIntent
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        max_level = intent.max_difficulty_level
        if intent.family_friendly:
            max_level = min(max_level or 1, 1)

        rows = await self._db.run_named(
            "search_trails",
            activity=intent.activity,
            min_difficulty_level=intent.min_difficulty_level,
            max_difficulty_level=max_level,
            min_distance_m=intent.min_distance_m,
            max_distance_m=intent.max_distance_m,
            max_elevation_gain_m=intent.max_elevation_gain_m,
            poi_types=list(intent.poi_types),
            surface_exclusions=list(intent.surface_exclusions),
            season=intent.season,
            exclude_hazards=list(intent.exclude_hazards),
            region=intent.region,
            limit=SEARCH_RESULT_LIMIT,
        )
        # Duration is a post-filter: the graph stores per-activity durations, and
        # which one applies depends on the requested activity.
        if intent.max_duration_min is not None:
            key = (
                "duration_hike_min" if intent.activity == "hike" else "duration_mtb_min"
            )
            rows = [r for r in rows if (r.get(key) or 0) <= intent.max_duration_min]

        return {"trails": rows}, {"trail_ids": [r["id"] for r in rows]}

    async def _route(
        self, intent: RouteIntent
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        settings = get_settings()
        start = await self._db.run_named("poi_by_name", name=intent.start, limit=1)
        end = await self._db.run_named("poi_by_name", name=intent.end, limit=1)
        if not start or not end:
            missing = intent.start if not start else intent.end
            return {"route": None, "unknown_place": missing}, {}

        snapped = []
        for poi in (start[0], end[0]):
            hit = await self._db.run_named(
                "nearest_intersection",
                lat=poi["lat"],
                lon=poi["lon"],
                radius_m=settings.snap_radius_m,
            )
            if not hit:
                return {"route": None, "off_network": poi["name"]}, {}
            snapped.append(hit[0]["osm_node_id"])

        rows = await self._db.run_named(
            "route_between_intersections",
            start_node=snapped[0],
            end_node=snapped[1],
            max_distance_m=min(
                intent.max_distance_m or settings.max_route_distance_m,
                settings.max_route_distance_m,
            ),
        )
        if not rows:
            return {"route": None, "no_path": True}, {}

        row = rows[0]
        return (
            {
                "route": {
                    "total_distance_m": row["total_m"],
                    "elevation_gain_m": row.get("gain_m"),
                    "start": start[0]["name"],
                    "end": end[0]["name"],
                },
                "geometry": {"type": "LineString", "coordinates": row["coordinates"]},
            },
            {"start_poi": start[0]["osm_id"], "end_poi": end[0]["osm_id"]},
        )
