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

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from chat.compile import Constraints, compile_outing
from chat.composer import (
    ComposedPlan,
    TrailSearchIntent,
    apply_delta,
    capped_difficulty,
    compose,
    standing_dump,
    standing_load,
)
from chat.intents import ClarifyIntent, RouteIntent
from chat.llm import LLMClient, Usage, results_to_json
from chat.pack_state import PlannerState
from chat.planner import plan_outing
from chat.readback import readback
from chat.sanitize import strip_links_stream
from chat.store import ConversationStore
from core.config import get_settings
from core.embeddings import Embedder
from core.text import lucene_escape
from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# Two limits, deliberately different. The CARDS can show more than the PROSE
# should narrate: the graph returns up to CARD_RESULT_LIMIT rows (the client
# folds them behind "show more"), while the answer model is handed only the
# first ANSWER_RESULT_LIMIT so it writes five sentences, not twenty. The fold
# and the prose must reference the same ordering, so the trim is a prefix,
# never a re-sort.
ANSWER_RESULT_LIMIT = 5
CARD_RESULT_LIMIT = 20

# The vector index scores this many candidates before the structured filters
# cut them down, so a filtered semantic search still has enough to choose from.
SEMANTIC_CANDIDATE_POOL = 25

# ...and this is how far below the best match a candidate may still be called a
# match. db.index.vector.queryNodes has no notion of "nothing here is close":
# it returns its nearest neighbours however distant they are, so with the card
# limit at 20 against a pool of 25 nearly the whole pool shipped as "matching
# the theme" and the rows behind the fold were near-arbitrary.
#
# The cut is RELATIVE to the best match rather than an absolute floor. A
# normalized cosine score has no bright line to put a floor on, and a relative
# cut cannot empty a non-empty answer: the top match always survives and only
# the tail that falls away from it is dropped.
#
# Measured 2026-08-21 (scripts.calibrate_semantic_drop, 10 themes against the
# live index) and NOT yet calibratable, for a reason worth knowing: (:Trail)
# holds 5 rows. The OSM data is 104,812 segments and 3,195 POIs, but trails are
# still the Trailforks-shaped stub, so the semantic path searches five fixture
# documents and a 25-candidate pool comes back with five. Any floor fitted to
# that describes the fixture. 0.05 keeps a median of 2 of the 5 and behaves
# sanely, so it stays until there is a corpus to measure.
#
# The run did settle one thing the constant cannot fix. Off-corpus themes score
# LOW in absolute terms -- "a coral reef dive with sea turtles" tops out at
# 0.58 where a real match reaches 0.75-0.83 -- and a relative cut always keeps
# the top row, so "nothing here matches your theme" is unsayable by
# construction. That wants an ABSOLUTE floor beside this one, whose value is
# exactly what a real corpus would let us measure.
SEMANTIC_SCORE_DROP = 0.05


async def _nothing() -> None:
    """A block this turn does not run. gather() wants a coroutine either way."""
    return None


def _strong_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The semantic rows that are close to the best one, in their own order.

    Rows arrive ordered by score DESC, so the result is a PREFIX of them —
    which matters, because the fold on screen and the prose the answer model
    writes must reference one ordering (see the limits above).
    """
    scores = [r["score"] for r in rows if r.get("score") is not None]
    if not scores:
        return rows
    floor = max(scores) - SEMANTIC_SCORE_DROP
    return [r for r in rows if r.get("score") is None or r["score"] >= floor]


def _answer_view(results: dict[str, Any]) -> dict[str, Any]:
    """The results as the ANSWER model sees them: card lists cut to a prefix.

    The prompt tells the model to cover every route it is given, so handing it
    twenty produces twenty sentences. A prefix, never a re-sort: the prose and
    the cards above the fold must be the same routes in the same order.
    """
    view = dict(results)
    for key in ("loops", "trails"):
        if isinstance(view.get(key), list):
            # Geometry never reaches the answer model (docs/route-design.md:
            # it receives facts, the assumptions strip and the counts) — a
            # coordinate list is thousands of tokens the prose cannot use.
            view[key] = [
                {k: v for k, v in row.items() if k != "geometry"}
                for row in view[key][:ANSWER_RESULT_LIMIT]
            ]
    return view


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
        planner: PlannerState | None = None,
        outing_docs: dict[str, list[dict]] | None = None,
    ) -> None:
        self._db = db
        self._llm = llm
        self._store = store
        self._embedder = embedder
        self._planner = planner
        # Per-conversation drawn documents, ordinal = 1-based list position.
        # Shared via app.state so it survives across requests (not restarts:
        # ponytail — a favourite after a redeploy asks the user to redraw).
        self._outing_docs = outing_docs if outing_docs is not None else {}

    async def run(
        self,
        user_id: str,
        message: str,
        conversation_id: str | None = None,
        near: tuple[float, float] | None = None,
        chip: dict | None = None,
    ) -> AsyncIterator[ChatEvent]:
        # `near` is the typed, coverage-validated "from here" point. It is
        # attached AFTER extraction — extract_plan below receives only the
        # message, history and standing plan, never a coordinate (pinned by
        # test). `chip` is a refinement tap: a typed, whitelisted delta that
        # skips BOTH model calls — the composer merges it and the planner
        # redraws, deterministically.
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

        # The conversation's standing plan: what earlier turns put in force.
        # The model sees it (to emit a delta against it); apply_delta merges
        # it (latest-wins, in Python). Both halves read the same jsonb.
        last_intent = await self._store.last_standing(conversation_id)
        standing_raw = (last_intent or {}).get("standing")

        refined = False
        subqueries: list = []
        plan_usage = Usage()
        if chip is not None:
            # No model in the loop: the chip IS the delta, already typed and
            # bounded (api/routes/chat.py whitelists the fields). It lands on
            # the standing outing exactly as a spoken refinement would.
            standing = standing_load(standing_raw)
            if standing is None or standing.outing is None:
                plan = ComposedPlan(
                    clarify=ClarifyIntent(
                        question=(
                            "There is no outing to refine yet — ask for one first."
                        )
                    )
                )
            else:
                plan = ComposedPlan(outing=standing.outing.model_copy(update=chip))
                refined = True
        else:
            plan_result = await self._llm.extract_plan(
                message, history, standing=standing_raw
            )
            plan_usage = plan_result.usage
            subqueries = plan_result.envelope.subqueries
            plan = compose(subqueries)
            # reset discards the plan in force BEFORE refine is considered: it
            # is the only way to clear a constraint (a delta can change one
            # but not unset it), and it must also stop a clarify turn carrying
            # the old plan forward below.
            if plan_result.envelope.reset:
                standing_raw = None
            if plan_result.envelope.refine and not plan.is_clarify:
                standing = standing_load(standing_raw)
                if standing is not None:
                    plan = apply_delta(standing, plan)
                    refined = True
        plan.near = near
        yield ChatEvent("intent", {"subqueries": [s.model_dump() for s in subqueries]})
        logger.info(
            "plan composed",
            extra={
                "conversation_id": conversation_id,
                "subqueries": len(subqueries),
                "clarify": plan.is_clarify,
                "refined": refined,
                "chip": chip is not None,
                "routes": len(plan.routes),
                "theme": bool(plan.theme),
            },
        )

        results, refs = await self._execute(plan, conversation_id)
        # answered_count is where the client folds the card list: the prose
        # narrates the first N, the rest sit behind "show more".
        yield ChatEvent(
            "results",
            {
                "kind": self._result_kind(plan),
                "answered_count": ANSWER_RESULT_LIMIT,
                # What the plan actually did, in the walker's own words. Note
                # it describes the EXECUTED plan, not the model's subqueries:
                # the composer's own decisions (a widened distance band, a
                # dropped duration, a capped difficulty) are the interesting
                # half, and the only place they are visible.
                **readback(plan),
                **results,
            },
        )

        # A clarify can arrive from the model (plan.clarify) or from Python —
        # compile's coverage refusal, the planner's built-from-counts question.
        # Either way it is streamed verbatim and the turn runs no answer model.
        turn_clarify = plan.is_clarify or "clarification" in results

        answer_parts: list[str] = []
        if turn_clarify:
            question = (
                plan.clarify.question if plan.is_clarify else results["clarification"]
            )
            answer_parts.append(question)
            yield ChatEvent("token", {"delta": question})
        elif chip is not None:
            # A chip turn spends nothing: the answer is a count, and the count
            # is Python's.
            drawn = results.get("loops") or []
            text = f"I drew {len(drawn)} routes for the refined ask."
            if results.get("relaxed"):
                text += f" {results['relaxed'].capitalize()}."
            answer_parts.append(text)
            yield ChatEvent("token", {"delta": text})
        else:
            # Links are stripped here, not asked for in the prompt: a live
            # smoke had the model linking every route name to trailforks.com,
            # a domain no result comes from (chat/sanitize.py).
            async for delta in strip_links_stream(
                self._llm.stream_answer(
                    message, results_to_json(_answer_view(results)), history
                )
            ):
                answer_parts.append(delta)
                yield ChatEvent("token", {"delta": delta})

        answer = "".join(answer_parts)
        # A clarify turn carries the PREVIOUS standing forward: the question
        # did not change what is in force, and last_standing reads only the
        # newest assistant row — dropping it here would end the conversation's
        # memory at every clarification.
        message_id = await self._store.add_message(
            conversation_id,
            "assistant",
            answer,
            intent={
                "subqueries": [s.model_dump() for s in subqueries],
                "standing": (standing_dump(plan) if not turn_clarify else standing_raw),
            },
            result_refs=refs,
        )

        answer_usage = (
            self._llm.last_answer_usage()
            if not (turn_clarify or chip is not None)
            else Usage()
        )
        total_in = plan_usage.input_tokens + answer_usage.input_tokens
        total_out = plan_usage.output_tokens + answer_usage.output_tokens
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
        if plan.routes and not (plan.search or plan.theme or plan.outing):
            return "route"
        # A drawn-outing turn is a loop_search, not a trail_search. The label is
        # client-facing: mislabelling it makes the frontend render drawn routes
        # as if they were named trails, which they are not. The wire value keeps
        # its name now the catalogue is gone — the frontend contract is the
        # shape of the cards, not where they came from.
        if plan.outing is not None and not (plan.search or plan.theme):
            return "loop_search"
        return "trail_search"

    async def _execute(
        self, plan: ComposedPlan, conversation_id: str | None = None
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

        # The on-demand path: an outing with a pack mounted is DRAWN, not
        # searched. compile owns the numbers; the planner draws and counts;
        # a Python clarify (coverage, infeasibility) poisons the turn like a
        # model clarify would — no other block runs beside it.
        if plan.outing is not None and self._planner is None:
            # No pack, no drawing — and with the catalogue retired (R7) there
            # is nothing behind it to fall back to. Say so rather than
            # streaming an empty result list, which reads as "nothing exists".
            # Production cannot reach this: REQUIRE_PACK refuses to boot.
            return {
                "clarification": (
                    "I cannot draw routes right now — the route data is not "
                    "loaded. Ask about a named trail instead, or try again "
                    "shortly."
                ),
                "suggestions": [],
            }, {}

        if plan.outing is not None:
            # Coverage speaks with the pack's own gazetteer (R5): the areas
            # this network actually holds, not a name list in code.
            compiled = compile_outing(
                plan.outing, gazetteer=self._planner.gazetteer or None
            )
            if isinstance(compiled, ClarifyIntent):
                return {
                    "clarification": compiled.question,
                    "suggestions": compiled.suggestions,
                }, {}
            anchor = await self._outing_anchor(plan, compiled)
            drawn = await asyncio.to_thread(
                plan_outing, self._planner, compiled, anchor
            )
            if drawn.clarify is not None:
                return {
                    "clarification": drawn.clarify.question,
                    "suggestions": drawn.clarify.suggestions,
                    "counts": drawn.counts,
                }, {}
            cards = [dict(r["card"]) for r in drawn.routes]
            ordinals = self._remember(
                conversation_id, [r["document"] for r in drawn.routes]
            )
            for card, ordinal in zip(cards, ordinals, strict=True):
                card["ordinal"] = ordinal
            results["loops"] = cards
            results["total_loops"] = len(cards)
            results["drawn"] = True
            results["assumptions"] = drawn.assumptions
            results["counts"] = drawn.counts
            if drawn.relaxed:
                results["relaxed"] = drawn.relaxed
            refs["loop_ids"] = [card["id"] for card in cards]

        # The blocks are independent reads over the same driver, so they go out
        # together. Sequentially, a trails-and-routes turn paid for the trail
        # search and then the routes one after another — two round trips of
        # latency for work that shares no state.
        wants_trails = plan.search is not None or plan.theme is not None
        trail_result, route_result = await asyncio.gather(
            (
                self._search(plan.search or TrailSearchIntent(), plan.theme)
                if wants_trails
                else _nothing()
            ),
            self._routes(plan.routes) if plan.routes else _nothing(),
        )

        if trail_result is not None:
            trails, semantic_unavailable = trail_result
            results["trails"] = trails
            # No estimate template for trails: the shown length is the count
            # (the trail graph is small and the page rarely fills its cap).
            results["total_trails"] = len(trails)
            if semantic_unavailable:
                results["semantic_unavailable"] = True
            refs["trail_ids"] = [r["id"] for r in trails]

        if route_result is not None:
            routes, route_refs = route_result
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

    #: Conversations whose drawn documents stay resident; oldest evicted.
    OUTING_CACHE_CONVERSATIONS = 50

    def _remember(
        self, conversation_id: str | None, documents: list[dict]
    ) -> list[int]:
        """Append this turn's documents; ordinals are 1-based positions in
        the conversation, stable for `refine_from` and favourite/share."""
        if conversation_id is None:
            return list(range(1, len(documents) + 1))
        docs = self._outing_docs.setdefault(conversation_id, [])
        first = len(docs) + 1
        docs.extend(documents)
        while len(self._outing_docs) > self.OUTING_CACHE_CONVERSATIONS:
            self._outing_docs.pop(next(iter(self._outing_docs)))
        return list(range(first, first + len(documents)))

    async def _outing_anchor(
        self, plan: ComposedPlan, compiled: Constraints
    ) -> tuple[float, float] | None:
        """Where the ask is anchored: the validated `near` for "here", a
        geocoded name otherwise. Resolution is the one I/O an ask needs, so
        it happens here rather than inside the CPU-bound planner."""
        if compiled.start_mode == "here" and plan.near is not None:
            return plan.near
        name = compiled.start_name or (plan.outing.area if plan.outing else None)
        if name:
            rows = await self._find_poi(name)
            if rows:
                return rows[0]["lat"], rows[0]["lon"]
        return plan.near

    async def _search(
        self, intent: TrailSearchIntent, theme: str | None
    ) -> tuple[list[dict[str, Any]], bool]:
        """One structured or semantic+structured search; returns (rows, degraded)."""
        max_level = capped_difficulty(
            intent.max_difficulty_level, intent.family_friendly
        )

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
                rows = _strong_matches(
                    await self._db.run_named(
                        "semantic_search_trails_filtered",
                        embedding=embedding,
                        candidate_pool=SEMANTIC_CANDIDATE_POOL,
                        limit=CARD_RESULT_LIMIT,
                        **params,
                    )
                )
            else:
                # 503-until-populated (CLAUDE.md) — in a chat turn that means
                # saying so, then answering from the structured filters alone.
                semantic_unavailable = True
                rows = await self._db.run_named(
                    "search_trails", limit=CARD_RESULT_LIMIT, **params
                )
        else:
            if theme is not None:
                semantic_unavailable = True
            rows = await self._db.run_named(
                "search_trails", limit=CARD_RESULT_LIMIT, **params
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
