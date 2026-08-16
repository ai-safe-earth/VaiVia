"""Chat orchestration: message -> plan -> composed graph queries -> answer.

The pipeline, and why it is shaped this way:

  1. Quota check BEFORE any model call — the cheapest refusal is the one that
     spends nothing (the gateway pre-checks too; this is the authoritative one).
  2. Plan extraction — the model's only structured output: the message
     decomposed into atomic subqueries, schema-validated.
  3. Composition — chat/composer.py (Python, not the model) merges the atoms
     into at most one search, one semantic theme, and a bounded route list, or
     a clarification with suggestions when there is too little to search well.
  4. Template dispatch — each composed piece maps to a named template. The
     model never names a template and never sees Cypher.
  5. Grounded answer — the model writes prose over results it is handed. Every
     trail id in the response also appears in result_refs, so the frontend can
     render exactly what the graph returned.

Events are emitted as an async stream so the API layer can serve SSE without
knowing anything about the LLM.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from chat.composer import ComposedPlan, TrailSearchIntent, compose
from chat.intents import RouteIntent
from chat.llm import LLMClient, results_to_json
from chat.store import ConversationStore
from core.config import get_settings
from core.embeddings import Embedder
from core.text import lucene_escape
from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

SEARCH_RESULT_LIMIT = 5
# The vector index scores this many candidates before the structured filters
# cut them down, so a filtered semantic search still has enough to choose from.
SEMANTIC_CANDIDATE_POOL = 25


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
        self,
        db: Neo4jClient,
        llm: LLMClient,
        store: ConversationStore,
        embedder: Embedder | None = None,
    ) -> None:
        self._db = db
        self._llm = llm
        self._store = store
        self._embedder = embedder

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

        plan_result = await self._llm.extract_plan(message, history)
        subqueries = plan_result.envelope.subqueries
        plan = compose(subqueries)
        yield ChatEvent("intent", {"subqueries": [s.model_dump() for s in subqueries]})
        logger.info(
            "plan composed",
            extra={
                "subqueries": len(subqueries),
                "clarify": plan.is_clarify,
                "routes": len(plan.routes),
                "theme": bool(plan.theme),
            },
        )

        results, refs = await self._execute(plan)
        yield ChatEvent("results", {"kind": self._result_kind(plan), **results})

        answer_parts: list[str] = []
        if plan.is_clarify:
            # No model call: the clarification is the model's own structured
            # output, so streaming it back costs nothing extra.
            assert plan.clarify is not None
            answer_parts.append(plan.clarify.question)
            yield ChatEvent("token", {"delta": plan.clarify.question})
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
            intent={"subqueries": [s.model_dump() for s in subqueries]},
            result_refs=refs,
        )

        answer_usage = self._llm.last_answer_usage()
        total_in = plan_result.usage.input_tokens + answer_usage.input_tokens
        total_out = plan_result.usage.output_tokens + answer_usage.output_tokens
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

    @staticmethod
    def _result_kind(plan: ComposedPlan) -> str:
        if plan.is_clarify:
            return "clarify"
        if plan.routes and not (plan.search or plan.theme):
            return "route"
        return "trail_search"

    async def _execute(
        self, plan: ComposedPlan
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Composed plan -> named templates. The only place plans touch the graph."""
        if plan.is_clarify:
            assert plan.clarify is not None
            return {
                "clarification": plan.clarify.question,
                "suggestions": plan.clarify.suggestions,
            }, {}

        results: dict[str, Any] = {}
        refs: dict[str, Any] = {}

        if plan.search is not None or plan.theme is not None:
            trails, semantic_unavailable = await self._search(
                plan.search or TrailSearchIntent(), plan.theme
            )
            results["trails"] = trails
            if semantic_unavailable:
                results["semantic_unavailable"] = True
            refs["trail_ids"] = [r["id"] for r in trails]

        if plan.routes:
            routes, route_refs = await self._routes(plan.routes)
            results["routes"] = routes
            refs.update(route_refs)
            # Legacy single-route shape: the first resolved route also appears
            # as `route` + `geometry`, so existing clients keep working.
            resolved = next((r for r in routes if r.get("route")), None)
            if resolved:
                results.setdefault("route", resolved["route"])
                results.setdefault("geometry", resolved["geometry"])
            elif routes:
                results.setdefault("route", None)
                for key in ("unknown_place", "off_network", "no_path"):
                    if key in routes[0]:
                        results.setdefault(key, routes[0][key])

        return results, refs

    async def _search(
        self, intent: TrailSearchIntent, theme: str | None
    ) -> tuple[list[dict[str, Any]], bool]:
        """One structured or semantic+structured search; returns (rows, degraded)."""
        max_level = intent.max_difficulty_level
        if intent.family_friendly:
            max_level = min(max_level or 1, 1)

        params: dict[str, Any] = dict(
            activity=intent.activity,
            min_difficulty_level=intent.min_difficulty_level,
            max_difficulty_level=max_level,
            min_distance_m=intent.min_distance_m,
            max_distance_m=intent.max_distance_m,
            min_elevation_gain_m=intent.min_elevation_gain_m,
            max_elevation_gain_m=intent.max_elevation_gain_m,
            poi_types=list(intent.poi_types),
            surface_exclusions=list(intent.surface_exclusions),
            season=intent.season,
            exclude_hazards=list(intent.exclude_hazards),
            region=intent.region,
        )

        semantic_unavailable = False
        rows: list[dict[str, Any]]
        if theme is not None and self._embedder is not None:
            status = await self._db.run_named("count_embedded_trails")
            embedded = status[0]["embedded"] if status else 0
            if embedded:
                [embedding] = await self._embedder.embed_texts([theme])
                rows = await self._db.run_named(
                    "semantic_search_trails_filtered",
                    embedding=embedding,
                    candidate_pool=SEMANTIC_CANDIDATE_POOL,
                    limit=SEARCH_RESULT_LIMIT,
                    **params,
                )
            else:
                # 503-until-populated (CLAUDE.md) — in a chat turn that means
                # saying so, then answering from the structured filters alone.
                semantic_unavailable = True
                rows = await self._db.run_named(
                    "search_trails", limit=SEARCH_RESULT_LIMIT, **params
                )
        else:
            if theme is not None:
                semantic_unavailable = True
            rows = await self._db.run_named(
                "search_trails", limit=SEARCH_RESULT_LIMIT, **params
            )

        # Duration is a post-filter: the graph stores per-activity durations, and
        # which one applies depends on the requested activity.
        if intent.max_duration_min is not None:
            key = (
                "duration_hike_min" if intent.activity == "hike" else "duration_mtb_min"
            )
            rows = [r for r in rows if (r.get(key) or 0) <= intent.max_duration_min]

        return rows, semantic_unavailable

    async def _routes(
        self, intents: list[RouteIntent]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        routes: list[dict[str, Any]] = []
        refs: dict[str, Any] = {}
        for index, intent in enumerate(intents):
            result, route_refs = await self._route(intent)
            routes.append(result)
            for key, value in route_refs.items():
                refs[f"{key}_{index}" if len(intents) > 1 else key] = value
        return routes, refs

    async def _find_poi(self, name: str) -> list[dict[str, Any]]:
        """Relevance-ranked full-text lookup, CONTAINS as the fallback."""
        query = lucene_escape(name).strip()
        if query:
            rows = await self._db.run_named(
                "poi_by_name_fulltext", query=query, limit=1
            )
            if rows:
                return rows
        return await self._db.run_named("poi_by_name", name=name, limit=1)

    async def _route(
        self, intent: RouteIntent
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        settings = get_settings()
        start = await self._find_poi(intent.start)
        end = await self._find_poi(intent.end)
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
