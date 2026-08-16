# Handoff — get-out-door

Last updated 2026-08-16.

## Where the project stands

A trail-query chatbot backed by a Neo4j knowledge graph that fuses OpenStreetMap
geometry with Trailforks curation. The repository began this session as a
docs-only skeleton with no code. It is now a working four-tier monorepo:
Next.js frontend, Fastify gateway, FastAPI backend, Neo4j graph, with Supabase
providing auth and Postgres.

Status is amber, not green, for one reason: everything is verified except the
parts that need infrastructure nobody has stood up yet. No Docker was available,
so nothing has been run against a real Neo4j; no Supabase project exists, so
real sign-in and persistent history are unproven. The code for both paths is
written and unit-tested; it has simply never met its dependencies.

## What is built and how far it is verified

| Piece | State | Verification |
|---|---|---|
| Graph schema and ontology | Owner-validated, frozen | Encoded in `backend/graph/schema.cypher`; never applied to a live database |
| Ingestion (OSM + Trailforks) | Complete | 29 offline tests on synthetic fixtures; never run against Neo4j or the live Overpass API |
| Query service (FastAPI) | Complete | 34 tests against a fake graph client; five endpoints registered and serving |
| Gateway (Fastify) | Complete | 25 tests with real RSA-signed JWTs and a stub upstream |
| Chat orchestration (OpenAI) | Complete | 33 offline tests, plus 15/15 against the live OpenAI API |
| Frontend (Next.js + MapLibre) | Complete | 25 tests; `next build` clean; production server verified serving |

Totals: 96 backend, 25 gateway, 25 frontend tests, all passing. CI runs all
three suites and is fully offline.

## The two properties the redesign exists to guarantee

**The browser never reaches anything but the gateway.** The gateway is the only
public service. It verifies Supabase JWTs, rate-limits per user with an IP
fallback, enforces the origin allowlist, pre-checks the LLM quota, and proxies
only `/trails`, `/routes`, and `/chat`. Everything else 404s there. The backend
trusts only the shared-secret hop and never parses a token.

**The model never writes Cypher.** Its only structured output is a validated
intent (`TrailSearchIntent | RouteIntent | ClarifyIntent`). Python — not the
model — maps that intent to a named, read-only, parameterized template. No field
in the schema can carry a query, a template name, or an identifier. Anything out
of scope becomes `Clarify`, which runs no query at all. Against the live API,
seven of seven injection and jailbreak payloads were contained this way.

## Read these before changing anything

- `docs/plan.md` — the delivery plan, decisions, and per-phase checkboxes.
- `docs/architecture.md` — the graph model, corrected during the redesign.
- `docs/fragilities.md` — known failure modes and the mitigations chosen.
- `CLAUDE.md` — the rules a contributor is most likely to break by accident.

## What blocks progress

1. **The OpenAI key in `backend/.env` was shared in plaintext and must be
   rotated before any deployment.** It works today and is gitignored, but treat
   it as compromised.
2. **No Docker.** The ingestion idempotency check, the routing queries, and the
   GDS Dijkstra upgrade are all unverified against a real database.
3. **No Supabase project.** `PostgresStore` is written but unwired, so chat
   history and quotas live in memory and reset on restart. Real sign-in is
   untested, and the frontend sign-in page was deliberately not built against a
   project that does not exist.

## Suggested order of work

Create the Supabase project first: it unblocks auth, history, and quotas
together, and it is the cheapest of the three. Then get Docker running and do
the graph smoke test, since every routing claim depends on it. Rotate the key
whenever convenient, and certainly before Phase 6 deploys anything.

## Running it locally

```bash
# Neo4j (needs Docker)
docker compose --env-file .env -f infra/docker-compose.yml up -d neo4j

# Backend
cd backend && uv sync
uv run python -m scripts.init_schema
uv run python -m ingestion.osm_ingest
uv run python -m ingestion.trailforks_ingest --mock
uv run uvicorn api.main:app --reload

# Gateway
cd gateway && npm install && npm run dev

# Frontend
cd frontend && npm install && npm run dev
```

Test suites: `uv run pytest tests/ -v` in `backend/`, `npm test` in `gateway/`
and `frontend/`. After changing chat prompts or intents, re-run
`uv run python -m scripts.check_intents_live` — it costs money and the
adversarial half must stay at seven of seven.

## Knowing what a session cost

`backend/scripts/cost_by_commit.py` attributes Claude Code token spend to git
commits, sessions, or models. It reads the local transcripts under
`~/.claude/projects/`, buckets each request into the commit whose interval
contains its timestamp, and prices the tokens at list rates. Stdlib only, so it
runs without `uv sync`.

```bash
python backend/scripts/cost_by_commit.py              # per commit
python backend/scripts/cost_by_commit.py --by session # or model / branch
python backend/scripts/cost_by_commit.py --json       # feeds sessions[].credits
```

Two things it gets right that a naive count does not. Each API request writes
several assistant records to the transcript — one per content block — and every
one repeats the *same* usage object, so the script dedupes on `requestId`;
summing raw records roughly doubles the figure. And cache reads dominate the
token volume by an order of magnitude while billing at a tenth of the input
rate, so 5-minute writes, 1-hour writes, and reads are priced separately.

The numbers are list-price API equivalents. On a subscription plan nothing here
is billed per token — `/usage` is what reflects real plan consumption. Build
cost through Phase 5 was roughly $62.

<!-- pmctl:handoff v1 -->
```json
{
  "project": "get-out-door",
  "org": "ai safe earth",
  "status": "amber",
  "updated": "2026-08-16",
  "deadline": null,
  "people": ["oscar"],
  "plans": [
    { "name": "redesign", "path": "docs/", "status": "active" }
  ],
  "phases": [
    { "name": "Phase 0 - Foundations", "status": "done", "start": "2026-08-15", "end": "2026-08-15", "plan": "redesign",
      "decisions": [
        { "date": "2026-08-15", "text": "Fastify gateway is the only public ingress; backend and Neo4j stay internal and trust a shared-secret hop" },
        { "date": "2026-08-15", "text": "Monorepo restructured in place: backend/, gateway/, frontend/, infra/" },
        { "date": "2026-08-15", "text": "uv with pyproject.toml instead of pip and requirements.txt" },
        { "date": "2026-08-15", "text": "Neo4j Community rather than Enterprise, which needs a paid license" },
        { "date": "2026-08-15", "text": "Supabase supplies both auth and the Postgres store for history, ledger and quotas" },
        { "date": "2026-08-15", "text": "SSE streaming end to end from day one" },
        { "date": "2026-08-15", "text": "Beta data scope limited to the Lake Como and Lecco bbox" }
      ] },
    { "name": "Phase 1 - Graph core and ingestion", "status": "done", "start": "2026-08-15", "end": "2026-08-15", "plan": "redesign",
      "decisions": [
        { "date": "2026-08-15", "text": "Routing graph is Intersection to Intersection; segments carry edge data and are not routing vertices" },
        { "date": "2026-08-15", "text": "MAPS_TO dropped as redundant; one ordered COMPOSED_OF with seq and match_confidence" },
        { "date": "2026-08-15", "text": "All distances in metres and durations in minutes, converted only for display" },
        { "date": "2026-08-15", "text": "Ontology extended by the owner: difficulty label plus numeric level plus free-text notes, per-activity durations, elevation gain and loss at trail, segment and per-direction edge, seasonality lists, landscape_description feeding the embedding" },
        { "date": "2026-08-15", "text": "Hiking duration follows DIN 33466; MTB uses speed by difficulty plus a climbing penalty, documented as recalibratable" }
      ] },
    { "name": "Phase 2 - Query service", "status": "done", "start": "2026-08-15", "end": "2026-08-15", "plan": "redesign",
      "decisions": [
        { "date": "2026-08-15", "text": "Named Cypher template library rather than inline query strings, so the LLM boundary is enforceable by construction" },
        { "date": "2026-08-15", "text": "Guard tests fail the build if a template mutates data, traverses semantic edges in a path, or leaves a traversal unbounded" },
        { "date": "2026-08-15", "text": "GDS Dijkstra templates written but not wired to the endpoint until they can be verified against a live GDS instance" }
      ] },
    { "name": "Phase 3 - Gateway", "status": "done", "start": "2026-08-15", "end": "2026-08-15", "plan": "redesign",
      "decisions": [
        { "date": "2026-08-15", "text": "Pipeline ordered identify then rate limit then authenticate, so limits key on the verified user and unauthenticated floods are still IP-counted instead of escaping on an early 401" },
        { "date": "2026-08-15", "text": "Quota checks fail open on a Postgres error: a database blip degrades cost control, not availability" }
      ] },
    { "name": "Phase 4 - Chat orchestration", "status": "done", "start": "2026-08-15", "end": "2026-08-15", "plan": "redesign",
      "decisions": [
        { "date": "2026-08-15", "text": "The model returns only a validated intent; Python maps intent to a read-only template, so no field can carry a query, template name or identifier" },
        { "date": "2026-08-15", "text": "OpenAI strict structured outputs reject oneOf and discriminator, so to_strict_schema rewrites the tagged union to anyOf" },
        { "date": "2026-08-15", "text": "Quota enforced in the orchestrator as well as the gateway, since the orchestrator is the authoritative point before spending" }
      ] },
    { "name": "Phase 5 - Frontend", "status": "done", "start": "2026-08-15", "end": "2026-08-15", "plan": "redesign",
      "decisions": [
        { "date": "2026-08-15", "text": "The gateway client is the app's only network surface; no path exists to backend, Neo4j or OpenAI" },
        { "date": "2026-08-15", "text": "Incremental SSE parser holding a remainder across chunks, since a network chunk can split a frame anywhere" },
        { "date": "2026-08-15", "text": "Map draws geometry from the same segments the answer was grounded in, so prose and map cannot disagree" },
        { "date": "2026-08-15", "text": "Sign-in page deferred rather than built speculatively against a Supabase project that does not exist" }
      ] },
    { "name": "Phase 6 - Beta hardening", "status": "planned", "start": null, "end": null, "plan": "redesign",
      "decisions": [
        { "date": "2026-08-16", "text": "Claude Code spend is attributed per commit by bucketing transcript usage into commit time intervals, deduped on requestId because one request writes several assistant records that each repeat the same usage object" }
      ] }
  ],
  "blockers": [
    { "text": "OpenAI API key was shared in plaintext and must be rotated before any deployment", "severity": "high", "owner": "oscar", "since": "2026-08-15" },
    { "text": "No Docker access, so ingestion idempotency, routing queries and GDS Dijkstra are unverified against a real Neo4j", "severity": "high", "owner": "oscar", "since": "2026-08-15" },
    { "text": "No Supabase project, so PostgresStore is unwired and history, quotas and real sign-in are unproven", "severity": "high", "owner": "oscar", "since": "2026-08-15" }
  ],
  "nextSteps": [
    { "title": "Create the Supabase project, enable email auth and apply infra/supabase/migrations/0001_chat_and_quotas.sql", "est": 0.5, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Rotate the exposed OpenAI API key and update backend/.env", "est": 0.5, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Run the live graph smoke test once Docker is available: init schema, both ingesters twice, assert counts unchanged", "est": 0.5, "owner": "oscar", "phase": "Phase 1 - Graph core and ingestion", "plan": "redesign" },
    { "title": "Swap InMemoryStore for PostgresStore and verify history, ledger and quota against Supabase", "est": 1, "owner": "oscar", "phase": "Phase 4 - Chat orchestration", "plan": "redesign" },
    { "title": "Wire GDS Dijkstra routing and verify the bounded projection against a live GDS instance", "est": 1, "owner": "oscar", "phase": "Phase 2 - Query service", "plan": "redesign" },
    { "title": "Build the Supabase sign-in page and conversation list in the frontend", "est": 1, "owner": "oscar", "phase": "Phase 5 - Frontend", "plan": "redesign" },
    { "title": "Playwright end-to-end smoke across the full stack", "est": 1, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Embeddings job and semantic search behind the 503-until-populated rule", "est": 2, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Caddy TLS, VPS deploy script, Neo4j and Postgres backup cron, uptime check", "est": 2, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" }
  ],
  "sessions": [
    { "date": "2026-08-15", "model": "fable-5", "credits": null, "person": "oscar", "hours": null },
    { "date": "2026-08-16", "model": "opus-5", "credits": 12, "person": "oscar", "hours": null }
  ]
}
```
