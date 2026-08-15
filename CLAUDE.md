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
- **The LLM never sees or writes Cypher.** `/chat` uses OpenAI structured outputs to produce a validated pydantic intent, which selects a parameterized template from `backend/graph/queries.cypher`. Out-of-scope input → `Clarify` intent.
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
