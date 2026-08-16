# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Early-stage monorepo; the roadmap and target architecture live in `docs/plan.md` — read it before structural work. Layout: `backend/` (FastAPI + Neo4j, Python), `gateway/` (Fastify BFF, Node/TS — Phase 3), `frontend/` (Next.js — Phase 5), `infra/` (compose, Supabase migrations, deploy). Backend code lands under `backend/` (`api/`, `ingestion/`, `graph/`, `scripts/`, `tests/`, `fixtures/`).

## Setup and commands

- Backend deps: **uv** in `backend/` (`cd backend && uv sync`), not pip. Run tools via `uv run`.
- Neo4j: `docker compose --env-file .env -f infra/docker-compose.yml up -d neo4j` (run from repo root; `--env-file` is required because the compose file lives in `infra/`) (Community + APOC + GDS). Copy `.env.example` → `.env` first; `NEO4J_PASSWORD` is required (no default).
- Ingestion for local dev/CI must stay offline: `--mock` (reads `backend/fixtures/trailforks_mock.json`), never the live Trailforks API.
- Checks before PR (from `backend/`): `uv run ruff check .`, `uv run black --check .`, `uv run pytest tests/ -v`.

## Architecture rules (from docs/plan.md — owner-ratified)

- The Fastify **gateway is the only public service** and carries no business logic (auth, rate limits, origin control, quota pre-check, SSE proxy only). Backend and Neo4j are internal; backend trusts only the gateway.
- **The LLM never sees or writes Cypher.** `/chat` uses OpenAI structured outputs to decompose the message into validated atomic pydantic subqueries (`backend/chat/intents.py`); `backend/chat/composer.py` — Python, not the model — merges them (tightest-wins) and maps the result onto parameterized templates in `backend/graph/queries.cypher`. Out-of-scope or adversarial input → `Clarify` (with suggestions), which poisons the whole plan and runs no query. Never add a field to an intent that carries a query, template name, or database identifier; that would hand the boundary away.
- Golden-dataset eval: `uv run python -m scripts.eval_golden` checks decomposition against `backend/fixtures/golden_questions.json` (add `--graph` for live retrieval). Extend the dataset when adding intent fields or templates.
- OpenAI strict structured outputs reject `oneOf` and `discriminator`; use `to_strict_schema()` for any new schema sent to the model.
- After changing prompts or intents, re-run `uv run python -m scripts.check_intents_live` (costs money, needs `OPENAI_API_KEY`) — the adversarial half must stay 7/7 `clarify`.
- Chat history, usage ledger, and quotas live in Supabase Postgres (`infra/supabase/migrations/`); per-user daily LLM cost caps are enforced before every OpenAI call.
- SSE streaming end-to-end for `/chat` (backend → gateway → frontend).

## Graph model rules (load-bearing — see docs/architecture.md, docs/fragilities.md)

- **Never merge OSM and Trailforks nodes.** Single ordered link: `(:Trail)-[:COMPOSED_OF {seq, match_confidence}]->(:Segment)`, created by proximity (≤ `SPATIAL_MATCH_THRESHOLD_M`, default 20 m) + `highway_type`/`surface` compatibility. There is no `MAPS_TO` (dropped as redundant).
- **Routing graph is Intersection–Intersection**: `(:Intersection)-[:CONNECTS_TO {distance, elevation_change, osm_way_id, surface, highway_type}]->(:Intersection)`. Segments are NOT routing vertices; never put `PASSES_BY` or other semantic edges in a path expression.
- **Trail identity lives only on `(:Trail)`** — never filter by trail name on segments.
- **Always bound traversals** (`*..100`) and spatially pre-filter; use GDS Dijkstra (Intersection/CONNECTS_TO projection) for real routing.
- **Ingestion must be idempotent**: `MERGE` on `osm_way_id` / `osm_node_id` / Trailforks IDs.
- Distance-along-trail queries must use `COMPOSED_OF.seq` — unordered `sum(s.length)` is wrong.
- Semantic-search endpoints return `503` (not empty results) when the vector index is unpopulated.
- Cypher lives in `.cypher` files under `backend/graph/`, not inline in Python. Query-service Cypher goes in `queries.cypher` as a named template (`// name: <x>`) and runs via `db.run_named("<x>", **params)` — parameters only, never string interpolation. Guard tests in `tests/test_query_loader.py` fail the build if a template mutates data, traverses semantic edges in a path expression, or leaves a traversal unbounded.

## Code conventions

- Python 3.11+, type hints required on all public functions, async for I/O (Neo4j driver, HTTP). Format Black, lint Ruff.
- Gateway/frontend: TypeScript strict; gateway stays thin — if a change adds domain logic there, it belongs in the backend.
- Conventional Commits (`feat:`, `fix:`, `docs:`, …); branches `feat/…`, `fix/…`, `docs/…`.
- Update the relevant file in `docs/` (including `docs/plan.md` checkboxes) when a change affects the data model, query patterns, fragilities, or roadmap.

## Handoff file (read by the project tracker)

One handoff per repository: `handoff.md` at the root. Never start a second one. When work happens inside a plan folder (`docs/refactor/`), keep writing to the root handoff and list the folder under "plans" so the paths stay findable.

Update it at the end of every working session: write it however you like for humans, then append this machine block as the last thing in the file, replacing the previous one.

<!-- pmctl:handoff v1 -->
```json
{
  "project": "Solar Forge",
  "org": "ai safe earth",
  "status": "amber",
  "updated": "2026-08-15",
  "deadline": "2026-09-30",
  "people": ["ana", "dro"],
  "plans": [
    { "name": "refactor", "path": "docs/refactor/", "status": "active" },
    { "name": "billing", "path": "docs/billing/", "status": "done" }
  ],
  "phases": [
    { "name": "Build", "status": "active", "start": "2026-06-11", "end": null, "plan": "refactor",
      "decisions": [{ "date": "2026-07-02", "text": "Postgres over Mongo, reporting needs joins" }] }
  ],
  "blockers": [{ "text": "Waiting on the provider API key", "severity": "high", "owner": "dro", "since": "2026-08-01" }],
  "nextSteps": [{ "title": "Wire auth to the new schema", "est": 3, "owner": "ana", "phase": "Build", "plan": "refactor" }],
  "sessions": [{ "date": "2026-08-12", "model": "opus-5", "credits": 40, "person": "dro", "hours": 2.5 }]
}
```

Rules: "plans" only points at folders; the work itself stays in phases, blockers, nextSteps and sessions, each tagged with "plan" when it belongs to one. ISO dates, null when unknown. status green|amber|red. phase status done|active|planned. severity critical|high|medium|low. est in working days. One sessions entry per working session. Append decisions and sessions, never rewrite past ones. Commit the handoff on whatever branch you are working in. No emoji.
