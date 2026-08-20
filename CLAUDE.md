# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Monorepo; the roadmap and target architecture live in `docs/plan.md` — read it before structural work. Layout: `pipeline/` (geodata pipeline, PostGIS, Python — its own uv project, own `README.md` and `docs/`), `backend/` (FastAPI + Neo4j, Python), `gateway/` (Fastify BFF, Node/TS), `frontend/` (Next.js + MapLibre), `infra/` (compose, Supabase migrations, deploy). All four tiers are built and tested; CI runs one job per tier. Backend code lands under `backend/` (`api/`, `ingestion/`, `graph/`, `scripts/`, `tests/`, `fixtures/`).

## Setup and commands

- Backend deps: **uv** in `backend/` (`cd backend && uv sync`), not pip. Run tools via `uv run`.
- `pipeline/` is a **separate uv project** with its own venv: `cd pipeline && uv sync`. Never run pipeline code from `backend/`'s environment or vice versa.
- Neo4j: `docker compose --env-file .env -f infra/docker-compose.yml up -d neo4j` (run from repo root; `--env-file` is required because the compose file lives in `infra/`) (Community + APOC + GDS). Copy `.env.example` → `.env` first; `NEO4J_PASSWORD` is required (no default).
- PostGIS (pipeline working store): `docker compose --env-file .env -f infra/docker-compose.yml up -d postgis` (pgRouting image; `POSTGIS_PASSWORD` in `.env`). It listens on **5433** — the local Supabase stack owns 5432.
- Supabase for dev is the **local** stack, not a hosted project: `cd infra && supabase start` (see CONTRIBUTING "Start Supabase").
- Ingestion for local dev/CI must stay offline: `--mock` (reads `backend/fixtures/trailforks_mock.json`), never the live Trailforks API.
- Checks before PR: from `backend/` **and** from `pipeline/` — `uv run ruff check .`, `uv run black --check .`, `uv run pytest tests/ -v`; from `gateway/` — `npm run lint && npm run typecheck && npm test`; from `frontend/` — `npm test && npm run build`.

## Architecture rules (from docs/plan.md — owner-ratified)

- The Fastify **gateway is the only public service** and carries no business logic (auth, rate limits, origin control, quota pre-check, SSE proxy only). Backend and Neo4j are internal; backend trusts only the gateway.
- **The LLM never sees or writes Cypher.** `/chat` uses OpenAI structured outputs to decompose the message into validated atomic pydantic subqueries (`backend/chat/intents.py`); `backend/chat/composer.py` — Python, not the model — merges them (tightest-wins) and maps the result onto parameterized templates in `backend/graph/queries.cypher`. Out-of-scope or adversarial input → `Clarify` (with suggestions), which poisons the whole plan and runs no query. Never add a field to an intent that carries a query, template name, or database identifier; that would hand the boundary away.
- Golden-dataset eval: `uv run python -m scripts.eval_golden` checks decomposition against `backend/fixtures/golden_questions.json` (add `--graph` for live retrieval). Extend the dataset when adding intent fields or templates.
- OpenAI strict structured outputs reject `oneOf` and `discriminator`; use `to_strict_schema()` for any new schema sent to the model.
- After changing prompts or intents, re-run `uv run python -m scripts.check_intents_live` (costs money, needs `OPENAI_API_KEY`) — the adversarial half must stay 7/7 `clarify`.
- Chat history, usage ledger, and quotas live in Supabase Postgres (`infra/supabase/migrations/`); per-user daily LLM cost caps are enforced before every OpenAI call.
- SSE streaming end-to-end for `/chat` (backend → gateway → frontend).

## Geodata pipeline rules (`pipeline/` — see pipeline/docs/metadata-rules.md)

- **The route document is the product; PostGIS is the working store that holds the value** (`docs/route-document.md`, ratified 2026-08-20). Curated geometry, elevation, routes and places live in PostGIS; `pipeline/export/route_documents.py` emits one structured JSON + GeoJSON per route, and Neo4j, the API, the frontend and any future social layer are all **readers of that document**. No reader redefines a route: a field a reader needs goes in the document, never into that reader. `backend/` consumes what the pipeline produces — it no longer produces the data.
- The document's contract is `pipeline/schemas/route-document.schema.json`, it is versioned, and the emitter is validated against it in the test suite. Attribution, licence and provenance travel **inside** the document, because the document is the ODbL Produced Work.
- **A route id must be stable across rebuilds.** Photos, comments and likes will key to it (`docs/social-layer.md`), so a generated route's id comes from its geometry, never from a sequence number or a `run_id` — vertex ids do not survive a rebuild.
- Migrations are `pipeline/sql/NNNN_*.sql`, applied in filename order by `uv run python migrate.py` (idempotent; `--dry-run` lists them). Add a new file; never edit an applied one.
- Division of labour: one SQL statement over a whole table (noding, snapping, line-merge, raster sampling) → PostGIS. Per-feature branching, anything needing a unit test or a plot → Python (GeoPandas/Shapely). Code implements the table in `pipeline/docs/metadata-rules.md`, not its own judgement.
- **Look before repairing.** Detectors write `qa.finding` (one rule = one QGIS layer); repairs write `qa.fix` with before/after geometry and honour `--dry-run`. Tolerances come from a measured distribution (2 m from the near-miss histogram, `topology/histogram.py`), never a guess — re-run the histogram after any change to the network.
- Pipeline tests are pure-function and must not touch a database: CI has no PostGIS.
- **Refresh the review bundle after every step that changes the store** — `cd pipeline && uv run python -m export.review_bundle`. A bundle that lags the database is worse than none, because it looks current. Its `README.md` is GENERATED from live queries (state, open issues, layers, and every field with its categories); never hand-write it. `review/REVIEW.md` is the opposite and is preserved across rebuilds: hand-written, saying what is being asked of this round.
- **Every field that gets styled needs a `*_class` or `*_band` twin** in its `qa.v_*` view, with a leading digit so QGIS legends sort correctly (`1 flat (<5%)`). The point is that a review is "colour by this field", never "write an expression first". Category boundaries come from a measured distribution, like every other tolerance here.
- Views are `DROP VIEW IF EXISTS` + `CREATE VIEW`, never `CREATE OR REPLACE` (the one exception is `qa.latest_run`, which others depend on). `migrate.py` replays the whole chain every run, so a later migration widening a view makes the earlier one fail on the next replay.
- Every curated row carries the `run_id` that produced it (`build_run` records each run's stage, parameters and counts); runs are compared inside the database — there is deliberately no file artefact to diff.

## Graph model rules (load-bearing — see docs/architecture.md, docs/fragilities.md)

- **Never merge OSM and Trailforks nodes.** Single ordered link: `(:Trail)-[:COMPOSED_OF {seq, match_confidence}]->(:Segment)`, created by proximity (≤ `SPATIAL_MATCH_THRESHOLD_M`, default 20 m) + `highway_type`/`surface` compatibility. There is no `MAPS_TO` (dropped as redundant).
- Trailforks data is **API-only and needs a granted key**; Outside's terms require prior written consent for commercial, in-software and AI use, which is all three of what VaiVia is (`docs/licensing.md`). Ingestion is a deliberate stub and no Trailforks data has ever entered the system; the data is OSM throughout, with open-licensed enrichment (Wikipedia/Wikidata) over marquee places. Keep the two-source model, but wire nothing new to Trailforks.
- **Routing graph is Intersection–Intersection**: `(:Intersection)-[:CONNECTS_TO {distance, elevation_change, osm_way_id, surface, highway_type}]->(:Intersection)`. Segments are NOT routing vertices; never put `PASSES_BY` or other semantic edges in a path expression.
- **Trail identity lives only on `(:Trail)`** — never filter by trail name on segments.
- **Always bound traversals** (`*..100`) and spatially pre-filter; use GDS Dijkstra (Intersection/CONNECTS_TO projection) for real routing.
- **Ingestion must be idempotent**: `MERGE` on `osm_way_id` / `osm_node_id` / Trailforks IDs.
- Distance-along-trail queries must use `COMPOSED_OF.seq` — unordered `sum(s.length)` is wrong.
- Semantic-search endpoints return `503` (not empty results) when the vector index is unpopulated.
- Hazards are season-scoped: `hazards_spring/summer/autumn/winter` hold each season's list and `seasonal_hazards` stays the union for display. A hazard filter with a season checks that season's list only; without a season it checks the union. Unscoped source records put the union in every season (a hazard we cannot place in time is always possible).
- Coverage is multi-region (`REGIONS` setting: Lecco, Bergamo). Seed with `scripts.init_schema`; ingest OSM per region via `ingestion.osm_ingest --region <name>`; trail→region links are recomputed from geometry each ingestion run.
- Cypher lives in `.cypher` files under `backend/graph/`, not inline in Python. Query-service Cypher goes in `queries.cypher` as a named template (`// name: <x>`) and runs via `db.run_named("<x>", **params)` — parameters only, never string interpolation. Guard tests in `tests/test_query_loader.py` fail the build if a template mutates data, traverses semantic edges in a path expression, or leaves a traversal unbounded.

## Code conventions

- Python 3.11+, type hints required on all public functions, async for I/O (Neo4j driver, HTTP). Format Black, lint Ruff.
- Gateway/frontend: TypeScript strict; gateway stays thin — if a change adds domain logic there, it belongs in the backend.
- Conventional Commits (`feat:`, `fix:`, `docs:`, …); branches `feat/…`, `fix/…`, `docs/…`, `chore/…`.
- **Branch from `develop` and open PRs against `develop`**, never `main`. `main` is production: protected, no direct pushes, and reached only by a release PR from `develop` or a `hotfix/…` branched off `main` (which must then be merged back into `develop`). `develop` is the repo default, so `gh pr create` targets it on its own.
- Update the relevant file in `docs/` (including `docs/plan.md` checkboxes) when a change affects the data model, query patterns, fragilities, or roadmap. Pipeline changes update `pipeline/docs/` (`metadata-rules.md`, `data-sources.md`) instead.

## Handoff file (read by the project tracker)

Read `handoff.md` once, at the start of a session, before the first plan or code change. Do not
re-read it later in the same session — the conversation is the fresher source. Re-read only after
a `/clear`, a `/compact`, or if I say the repo moved outside this session. If it conflicts with
the repo, trust the repo and say so.