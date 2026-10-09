# CLAUDE.md

## Project
- What it is: VaiVia, a chat route planner for hikers, trail runners and mountain bikers over OSM in Lecco and Bergamo. You describe the outing; it draws the route.
- Stack: Python 3.11+ (FastAPI, uv), Neo4j, PostGIS + pgRouting, Supabase Postgres, Fastify/TypeScript gateway, Next.js + MapLibre, OpenAI.
- Main folders: `pipeline/` (geodata pipeline, its OWN uv project), `shared/routes/` (`vaivia_routes`, the pack engine both uv projects use), `backend/`, `gateway/`, `frontend/`, `infra/`. Design doctrine: `docs/plan.md`.

## Commands
- Install: `cd backend && uv sync`; `cd pipeline && uv sync` (separate venv, never run one from the other); `npm ci` in `gateway/` and `frontend/`.
- Run: copy `.env.example` to `.env` (`NEO4J_PASSWORD`, `POSTGIS_PASSWORD` required); from repo root `docker compose --env-file .env -f infra/docker-compose.yml up -d neo4j postgis`; `cd infra && supabase start` (local stack, not hosted).
- Test: `uv run pytest tests/ -v` in `backend/` and `pipeline/`; `npm test` in `gateway/`; `npm test && npm run build` in `frontend/`.
- Lint/format: `uv run ruff check . && uv run black --check .` in `backend/` and `pipeline/`; `npm run lint && npm run typecheck` in `gateway/`.
- Evals: `uv run python -m scripts.eval_golden` (`--graph`, `--answers`, `--only <ids>`) and `uv run python -m scripts.check_intents_live` (paid) in `backend/`.
- Pipeline: `uv run python migrate.py` (`--dry-run` lists); `uv run python -m export.review_bundle` in `pipeline/`.

## Rules
- Verify before saying a task is done: run the tests, or check the result in the browser for UI changes.
- Keep changes small and focused on the task.
- Ask before deleting files, adding dependencies, or changing the roadmap order.

## Two ways to work
Pick one at the start of every task. If unsure, ask me in one line.
My shortcuts: `quick:` at the start of my message means quick fix. `plan:` means planned work.

### Quick fix (no plan)
Use it when ALL of these are true:
- It fits in one session.
- It is small and clear: a UI change, a bug fix, debugging, a small improvement.
- No new dependency, no database or schema change, no change to a public API.

How:
- Fix it now. Do not create or update a plan.
- Debugging: reproduce first, find the cause, then fix. No guess fixes.
- Verify it works.
- If you learned something lasting (a gotcha, a rule), add one line to "Decisions".
- If it changes the state of an active plan, update only that plan's `next` line.
- If it grows (more than one session, many files, or any "no" item above), stop and ask me to turn it into a plan.

### Planned work (uses a plan)
Everything else: multi-step or multi-session work, new features, refactors, data or architecture changes.

## Roadmap and plans
- `docs/ROADMAP.md` is the roadmap. I own the step order. Never change it without asking.
- Plans live in `docs/plans/`. One file per feature or refactor.
- Every plan starts with this frontmatter:
  ```yaml
  ---
  status: todo | active | blocked | done
  step: <roadmap step slug>
  next: <one line>
  ---
  ```
- Before working: read the plan. If there is none, create one and ask me which roadmap step it belongs to.
- After each finished step: tick it, update `status` and `next`, write down any decision taken.
- When a plan is finished: set `status: done` and move it to `docs/plans/done/`.
- `/roadmap` refreshes the status section of `docs/ROADMAP.md`.

## Tracking files
- The only tracking files are: this file, `docs/ROADMAP.md`, and `docs/plans/`.
- Quick fixes are recorded only in git history, so commit messages must say what changed and why.
- Do not create handoff, status, summary, audit, or report files. Put that information in a plan or in "Decisions".

## Decisions
Architecture (owner-ratified):
- The Fastify gateway is the only public service and holds no business logic (auth, rate limits, origins, quota pre-check, SSE proxy). Backend and Neo4j are internal; the backend trusts only the gateway.
- The LLM never sees or writes Cypher. Structured outputs give pydantic intents (`backend/chat/intents.py`); Python (`chat/composer.py`) maps them onto named templates. Never add an intent field that carries a query, template name or database id. Out-of-scope or adversarial input becomes `Clarify` and runs no query.
- OpenAI strict schemas reject `oneOf` and `discriminator`: send every schema through `to_strict_schema()`.
- After changing prompts or intents, `check_intents_live` must stay 7/7 `clarify`. Extend `backend/fixtures/golden_questions.json` with new intent fields. Full `eval_golden` runs append to `backend/eval_runs.jsonl`; `--only` runs do not.
- Per-user daily LLM cost caps are checked before every OpenAI call. Chat history, usage and quotas live in Supabase (`infra/supabase/migrations/`). `/chat` streams SSE end to end.
- The route document is the product (`docs/route-document.md`). PostGIS holds the value; Neo4j, the API and the frontend only read the document. A field a reader needs goes into the document (`pipeline/schemas/route-document.schema.json`, versioned, licence and provenance inside).
- The pack planner draws every route at ask time, A-to-B included (`shared/routes/`, `docs/route-design.md`). A route id comes from its geometry, never a sequence or `run_id`.
Pipeline (details in `pipeline/docs/metadata-rules.md`):
- Migrations are `pipeline/sql/v2/NNNN_*.sql`: add a file, never edit an applied one. Views are `DROP VIEW IF EXISTS` + `CREATE VIEW`, never `CREATE OR REPLACE` (except `qa.latest_run`).
- Look before repairing: detectors write `qa.finding`, repairs write `qa.fix` with before/after and honour `--dry-run`. Tolerances come from a measured distribution, never a guess.
- Refresh the review bundle after every step that changes the store. Its `README.md` is generated; `review/REVIEW.md` is hand-written. Every styled field needs a `*_class`/`*_band` twin with a leading digit.
- Pipeline tests are pure and never touch a database (CI has no PostGIS). Every curated row carries its `run_id`.
Graph:
- Cypher lives in `.cypher` files under `backend/graph/`; query templates are `// name: <x>` in `queries.cypher`, run via `db.run_named`, parameters only. Guard tests in `tests/test_query_loader.py` block writes, semantic edges in paths, and unbounded traversals (`*..100`).
- Ingestion is idempotent (`MERGE` on stable ids) and offline in dev/CI (`--mock`). Never merge OSM and Trailforks nodes.
- Trailforks needs written consent we do not have (`docs/licensing.md`); no Trailforks data has ever entered the system. Wire nothing new to it.
- `Intersection`/`CONNECTS_TO` and the GDS plugin remain only for `scripts.check_graph_connectivity`. Semantic search returns 503 while the vector index is empty. Hazards are season-scoped (`hazards_<season>`, `seasonal_hazards` is the union).
Workflow and gotchas:
- Branch from `develop`, PR to `develop`. `main` is production: reached only by a release PR or a `hotfix/…` branch (merged back to `develop`). Conventional Commits; branches `feat/ fix/ docs/ chore/`.
- Compose needs `--env-file .env` because the file lives in `infra/`. PostGIS listens on 5433; local Supabase owns 5432.
- Python: type hints on public functions, async for I/O, Black + Ruff. Gateway/frontend: TypeScript strict.
- When a change affects the data model, queries or fragilities, update `docs/` (pipeline changes: `pipeline/docs/`).
- `docs/explain/` HTML explainers (`/explain-doc`) are allowed; they are not tracking files.

## How to write final responses
- Plain English. Short sentences. Bullet points.
- Clear structure: what changed, what is next, what I must decide.
- Put in [brackets] what I should know or need to learn.
- This applies to final responses only, not to code, commits, or plan files.
