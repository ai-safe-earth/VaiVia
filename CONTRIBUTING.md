# Contributing to VaiVia

Thanks for your interest. This document covers local setup, the conventions we
follow, the invariants you must not break, and the pull request process.

Two files are worth reading before you write any code:

- [`CLAUDE.md`](CLAUDE.md) — the rules a contributor is most likely to break by
  accident, stated tersely. It is also what AI coding assistants in this repo are
  bound by.
- [`docs/plan.md`](docs/plan.md) — the roadmap and target architecture, ratified
  by the owner. Read it before any structural change.

---

## Development setup

### 1. Fork and clone

```bash
git clone https://github.com/<your-username>/VaiVia.git
cd VaiVia
```

### 2. Install dependencies

Backend dependencies are managed with [uv](https://docs.astral.sh/uv/) and
declared in `backend/pyproject.toml`. **Not pip, and there is no
`requirements.txt`.**

```bash
cd backend && uv sync      # creates .venv/ with runtime + dev deps
```

Prefix commands with `uv run` (`uv run pytest`) rather than activating the venv.

Gateway and frontend are plain npm workspaces:

```bash
cd gateway  && npm install
cd frontend && npm install
```

### 3. Start Neo4j

```bash
cp .env.example .env      # NEO4J_PASSWORD is required; there is no default
docker compose --env-file .env -f infra/docker-compose.yml up -d neo4j
```

Run this from the repo root — `--env-file` is required because the compose file
lives in `infra/`. The container needs both **APOC** and **GDS**. If
`gds.version()` comes back unknown, the plugin installer lost its network race
on a cold Docker start: recreate the container rather than debugging it.

### 4. Seed the graph

```bash
cd backend
uv run python -m scripts.init_schema
uv run python -m ingestion.osm_ingest --region Lecco
uv run python -m ingestion.trailforks_ingest --mock
```

Local dev and CI ingestion must stay **offline**: always `--mock` for
Trailforks, never the live API. OSM ingestion does hit live Overpass; it is
idempotent, so re-running is cheap in graph terms but not in Overpass load.

### 5. Run the checks

From `backend/`, before every PR:

```bash
uv run ruff check .
uv run black --check .
uv run pytest tests/ -v
```

And `npm test` in `gateway/` and `frontend/`.

---

## Invariants — do not break these

These came out of an architecture review and are load-bearing. A PR that
violates one will be rejected on principle, not on style.

### The gateway is the only public service, and it holds no business logic

Auth, rate limiting, origin control, quota pre-check, SSE proxying. That is the
whole list. If your change adds domain logic to `gateway/`, it belongs in the
backend instead. Backend and Neo4j stay internal; the backend trusts only the
shared-secret hop and never parses a token.

### The LLM never sees or writes Cypher

`/chat` uses OpenAI structured outputs to decompose a message into validated
atomic pydantic subqueries (`backend/chat/intents.py`). `backend/chat/composer.py`
— Python, not the model — merges them tightest-wins and maps the result onto
parameterized named templates in `backend/graph/queries.cypher`.

**Never add a field to an intent that can carry a query, a template name, or a
database identifier.** That single change would hand the boundary away.
Out-of-scope or adversarial input must resolve to `Clarify`, which poisons the
whole plan so no query runs.

OpenAI's strict structured outputs reject `oneOf` and `discriminator`; run any
new schema you send to the model through `to_strict_schema()`.

### Cypher lives in `.cypher` files

Query-service Cypher goes in `backend/graph/queries.cypher` as a named template
(`// name: <x>`) and runs via `db.run_named("<x>", **params)` — parameters only,
never string interpolation. Guard tests in `backend/tests/test_query_loader.py`
fail the build if a template mutates data, traverses a semantic edge inside a
path expression, or leaves a traversal unbounded.

### Graph model rules

See [`docs/architecture.md`](docs/architecture.md) and
[`docs/fragilities.md`](docs/fragilities.md) for the reasoning. In short:

- **Never merge OSM and Trailforks nodes.** The only link is
  `(:Trail)-[:COMPOSED_OF {seq, match_confidence}]->(:Segment)`, created by
  proximity (≤ `SPATIAL_MATCH_THRESHOLD_M`, default 20 m) plus `highway_type` /
  `surface` compatibility. There is no `MAPS_TO`.
- **Routing is Intersection→Intersection** via `CONNECTS_TO`. Segments are not
  routing vertices; never put `PASSES_BY` or another semantic edge in a path
  expression.
- **Trail identity lives only on `(:Trail)`** — never filter by trail name on a
  segment.
- **Always bound traversals** (`*..100`) and pre-filter spatially. Use GDS
  Dijkstra for real routing.
- **Ingestion must be idempotent** — `MERGE` on `osm_way_id` / `osm_node_id` /
  Trailforks IDs. Re-running must leave counts identical.
- Distance-along-trail must use `COMPOSED_OF.seq`; an unordered
  `sum(s.length)` is wrong.
- Semantic-search endpoints return `503`, not empty results, when the vector
  index is unpopulated.
- Hazards are season-scoped: `hazards_spring/summer/autumn/winter` per season,
  `seasonal_hazards` as the union for display. A source record with no season
  puts the union in every season — a hazard we cannot place in time is always
  possible.

The `graph-model` skill in `.claude/skills/` carries the same rules for AI
assistants.

---

## Testing expectations

- CI runs the three unit suites and must stay **fully offline**. No test may
  reach Overpass, Trailforks, OpenAI, Supabase, or a real Neo4j.
- Backend tests run against a fake graph client; the offline fake cannot catch
  Cypher syntax errors, so a new template needs a live run before you claim it
  works.
- Playwright e2e (`frontend/`) is a local/pre-deploy check. It reads
  `E2E_EMAIL` / `E2E_PASSWORD` and skips itself when they are unset; the live
  OpenAI turn is further gated behind `E2E_LIVE=1`.
- After changing chat prompts or intents, run:

  ```bash
  cd backend
  uv run python -m scripts.eval_golden            # add --graph for live retrieval
  uv run python -m scripts.check_intents_live     # costs money, needs OPENAI_API_KEY
  ```

  The adversarial half of `check_intents_live` must stay **7/7 clarify**. Extend
  `backend/fixtures/golden_questions.json` whenever you add an intent field or a
  template.

---

## Code conventions

### Python

- 3.11+, type hints required on all public functions.
- `async`/`await` for I/O (Neo4j driver, HTTP).
- Formatter **Black**, linter **Ruff**.

### TypeScript

- Strict mode, gateway and frontend both.
- The gateway stays thin (see the invariant above).

### Commits

[Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add elevation backfill from SRTM
fix: handle null surface tag in OSM segments
docs: add query examples for multi-day routes
refactor: extract spatial matching into its own module
test: cover the proximity threshold edge cases
```

### Branches

```
feat/elevation-backfill
fix/null-surface-tag
docs/query-cookbook
```

### Secrets

Never commit a key, password, or connection string — not even in a test fixture
or a comment. `.env` files are gitignored and must stay that way. If a
credential is exposed anywhere, including a chat transcript, treat it as
compromised and rotate it.

---

## Documentation duties

A change is not finished until the docs match it:

- `docs/architecture.md` — data model changes.
- `docs/query-examples.md` — new query patterns.
- `docs/fragilities.md` — a new failure mode, or a mitigation for an old one.
- `docs/plan.md` — tick the phase checkboxes.
- `handoff.md` — the root handoff, updated at the end of every working session.
  There is exactly one; never start a second. Append to `decisions` and
  `sessions` in the machine block, never rewrite past entries.

---

## Pull request process

1. Open an issue first for anything non-trivial, so the approach can be agreed.
2. Keep the PR focused — one feature or fix.
3. Add or update tests for changed behaviour.
4. Update the relevant `docs/` file.
5. Make sure `ruff`, `black`, `pytest`, and both `npm test` suites pass.
6. One review approval is required to merge.

---

## Where to start

| Area | Key files | Good first issues |
|---|---|---|
| Ingestion | `backend/ingestion/osm_ingest.py`, `trailforks_ingest.py` | New POI types, better error handling, new regions |
| Graph schema | `backend/graph/schema.cypher` | Indexes, constraints |
| Query layer | `backend/graph/queries.cypher`, `backend/api/routes/` | New named templates and endpoints |
| Chat | `backend/chat/intents.py`, `composer.py`, `prompts.py` | Golden-dataset coverage, composer edge cases |
| Frontend | `frontend/app/`, `frontend/components/` | Map interactions, accessibility |
| Docs | `docs/` | Clearer examples, documenting a fragility you hit |
| Tests | `backend/tests/`, `gateway/test/`, `frontend/test/` | Edge-case coverage |

---

## Questions

Open a GitHub Discussion, or file an issue with the `question` label.
