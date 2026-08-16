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
| Supabase store and quotas | Complete | Schema applied; 12-check live round-trip of `PostgresStore`; gateway quota store queries the real database |
| Supabase auth | Complete | Real sign-in issues an ES256 token; the running gateway verifies it against the live JWKS and 401s both a missing and a malformed token |

Totals: 96 backend, 28 gateway, 25 frontend tests, all passing. CI runs all
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

## Supabase is wired

Project `fatktvawkmrytywegjjz` (eu-west-1) is live and the schema is applied:
`conversations`, `messages`, `usage_ledger`, `daily_quotas`, RLS on with one
policy each. Chat history, the cost ledger and quotas now survive a restart.

Two things about the connection are worth knowing before anyone edits an env
file, because both fail in ways that look like something else:

- **The direct host is unusable.** `db.<ref>.supabase.co` publishes an AAAA
  record and no A record, so without IPv6 it does not resolve at all. Every
  `DATABASE_URL` points at the Supavisor pooler
  (`aws-1-eu-west-1.pooler.supabase.com`) in **session** mode, port 5432 — not
  6543, because these services hold long-lived connections.
- **`sslmode=require` in the URL breaks the gateway.** The bundled `pg` treats
  it as `verify-full`, and Supabase's pooler certificate does not chain to a
  public root, so the connection is rejected as self-signed. TLS is instead
  selected in `gateway/src/quotaStore.ts`, which encrypts for any non-local
  host. This matters: a bare connection string connects happily *in plaintext*,
  which is exactly what makes it easy to miss.

## What blocks progress

1. **Credentials shared in plaintext must be rotated before any deployment.**
   The OpenAI key in `backend/.env`, and the Supabase database password, have
   both been pasted into chat transcripts. They work today and are gitignored;
   treat both as compromised.
2. **No Docker.** The ingestion idempotency check, the routing queries, and the
   GDS Dijkstra upgrade are all unverified against a real database.
3. **The account password is `12345678`.** It is eight characters, entirely
   numeric, and has been pasted into a chat transcript. Fine for a scratch
   login today; it must not survive contact with a deployed service.

The auth chain itself is no longer a blocker: a real sign-in was exercised end
to end against the running gateway. The frontend sign-in page is still unbuilt,
which is now ordinary work rather than something waiting on infrastructure.

## Suggested order of work

Get Docker running and do the graph smoke test, since every routing claim
depends on it and it is the last piece of the stack never run for real. The
frontend sign-in page can proceed in parallel — nothing blocks it now. Rotate
all three credentials before Phase 6 deploys anything.

Two smaller things found while verifying the gateway, neither urgent:

- **There is no health endpoint.** `/health` 404s like any other unproxied
  path. The planned uptime check has nothing to poll, so add one before deploy.
- **The gateway does not validate `iss` or `aud`.** `jwtVerify` is called with
  no claim options, so it checks signature and expiry only. The project-specific
  signing key makes this sound in practice, but pinning
  `iss=https://<ref>.supabase.co/auth/v1` and `aud=authenticated` is cheap
  defence in depth. It was left alone rather than changed blind.

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
    { "name": "Phase 6 - Beta hardening", "status": "active", "start": "2026-08-16", "end": null, "plan": "redesign",
      "decisions": [
        { "date": "2026-08-16", "text": "Claude Code spend is attributed per commit by bucketing transcript usage into commit time intervals, deduped on requestId because one request writes several assistant records that each repeat the same usage object" },
        { "date": "2026-08-16", "text": "DATABASE_URL points at the Supavisor pooler in session mode because the direct host db.<ref>.supabase.co publishes an AAAA record only and does not resolve without IPv6" },
        { "date": "2026-08-16", "text": "Gateway selects TLS in quotaStore.ts for any non-local host rather than via sslmode in the URL: node-postgres sends no SSLRequest by default so a bare connection string is silently plaintext, while sslmode=require is aliased to verify-full by the bundled pg and fails against Supabase's pooler certificate" },
        { "date": "2026-08-16", "text": "Migrations are applied by scripts/apply_migrations.py rather than the Supabase CLI, which expects its own supabase/migrations layout; every migration must be idempotent, so the RLS policies now drop-if-exists first" },
        { "date": "2026-08-16", "text": "Backend tests pin gateway_shared_secret and database_url to empty via an autouse fixture; they previously inherited the developer's .env, so a populated secret 401'd 23 tests and a populated DATABASE_URL opened a real Postgres pool during unit tests" },
        { "date": "2026-08-16", "text": "Gateway builds from tsconfig.build.json with rootDir src: the base config includes test/ for typecheck, which made tsc emit dist/src/... so npm start could not find dist/server.js and the built artifact was unstartable" },
        { "date": "2026-08-16", "text": "Gateway dev and start load gateway/.env via node --env-file-if-exists rather than a dotenv dependency; nothing read that file before, so its Supabase settings were inert" },
        { "date": "2026-08-16", "text": "The auth plugin throws a named error when SUPABASE_JWT_JWKS_URL is empty; config only requires it in production, so outside production the empty string reached new URL and killed boot with a bare ERR_INVALID_URL" }
      ] }
  ],
  "blockers": [
    { "text": "OpenAI API key was shared in plaintext and must be rotated before any deployment", "severity": "high", "owner": "oscar", "since": "2026-08-15" },
    { "text": "Supabase database password was shared in plaintext and must be rotated before any deployment", "severity": "high", "owner": "oscar", "since": "2026-08-16" },
    { "text": "No Docker access, so ingestion idempotency, routing queries and GDS Dijkstra are unverified against a real Neo4j", "severity": "high", "owner": "oscar", "since": "2026-08-15" },
    { "text": "The Supabase account password is 12345678 and was shared in plaintext; it must be changed before any deployment", "severity": "high", "owner": "oscar", "since": "2026-08-16" }
  ],
  "nextSteps": [
    { "title": "Rotate the exposed OpenAI API key, Supabase database password and account password, then update backend/.env and gateway/.env", "est": 0.5, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Add a gateway health endpoint for the planned uptime check; /health currently 404s like any unproxied path", "est": 0.25, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Validate iss and aud on Supabase tokens in the gateway auth plugin; jwtVerify currently checks signature and expiry only", "est": 0.25, "owner": "oscar", "phase": "Phase 3 - Gateway", "plan": "redesign" },
    { "title": "Run the live graph smoke test once Docker is available: init schema, both ingesters twice, assert counts unchanged", "est": 0.5, "owner": "oscar", "phase": "Phase 1 - Graph core and ingestion", "plan": "redesign" },
    { "title": "Wire GDS Dijkstra routing and verify the bounded projection against a live GDS instance", "est": 1, "owner": "oscar", "phase": "Phase 2 - Query service", "plan": "redesign" },
    { "title": "Build the Supabase sign-in page and conversation list in the frontend", "est": 1, "owner": "oscar", "phase": "Phase 5 - Frontend", "plan": "redesign" },
    { "title": "Playwright end-to-end smoke across the full stack", "est": 1, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Embeddings job and semantic search behind the 503-until-populated rule", "est": 2, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Caddy TLS, VPS deploy script, Neo4j and Postgres backup cron, uptime check", "est": 2, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" }
  ],
  "sessions": [
    { "date": "2026-08-15", "model": "fable-5", "credits": null, "person": "oscar", "hours": null },
    { "date": "2026-08-16", "model": "opus-5", "credits": 12, "person": "oscar", "hours": null },
    { "date": "2026-08-16", "model": "opus-5", "credits": 18, "person": "oscar", "hours": null }
  ]
}
```
