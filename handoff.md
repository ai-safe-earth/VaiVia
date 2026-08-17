# Handoff — VaiVia

Last updated 2026-08-17.

The project was renamed from `get-out-door` to **VaiVia** on 2026-08-17. The
GitHub remote is now `https://github.com/ai-safe-earth/VaiVia.git` and the local
root folder is `A02_VaiVia`. README, LICENSE and CONTRIBUTING have been rewritten
under the new name, and the in-code identifiers followed and are **merged to
`main`** (PRs #2 and #3): package names (`vaivia`, `vaivia-gateway`,
`vaivia-frontend`, both lockfiles relocked), the compose container
(`vaivia-neo4j`), page title and headings, the Overpass User-Agent default, the
FastAPI title, the `graph-model` skill description, and the doc and
`.env.example` headers. All three unit suites pass after the rename (148 / 34 /
33).

Two things the rename touched that are worth knowing. The compose **volumes**
(`neo4j_data`, `neo4j_logs`) are unchanged, so the ingested graph survives; only
the container is renamed and `up -d` recreates it. And renaming the root folder
broke every console-script shim in `backend/.venv` (Windows `.exe` launchers
hardcode the absolute interpreter path, so `uv run black` failed with "Failed to
canonicalize script path"); deleting `.venv` and re-running `uv sync` fixes it.
Anyone else who pulls after the folder rename will hit the same thing.

## Where the project stands

A trail-query chatbot backed by a Neo4j knowledge graph that fuses OpenStreetMap
geometry with Trailforks curation. A working four-tier monorepo: Next.js
frontend, Fastify gateway, FastAPI backend, Neo4j graph, with Supabase providing
auth and Postgres.

The product works end to end against real infrastructure and has been driven in
a real browser: sign-in, resumed conversation history, a live streamed chat turn
grounded in the graph, and the trail drawn on the map — all of it pinned by a
repeatable Playwright suite. Status stays amber for one reason only: three
credentials were shared in plaintext during development and must be rotated
before anything deploys. Everything else that remains is Phase 6 hardening
(embeddings, deploy plumbing), not unverified core.

## What is built and how far it is verified

| Piece | State | Verification |
|---|---|---|
| Graph schema and ontology | Owner-validated, frozen | Applied to the live database (16 statements, region seeded) |
| Ingestion (OSM + Trailforks) | Complete, live-verified | Offline tests plus real runs: 15,937 segments / 31,848 edges from live Overpass; re-runs leave counts identical |
| Query service (FastAPI) | Complete | Tests against a fake graph client; live `/routes` and `/trails` verified over HTTP |
| Gateway (Fastify) | Complete | 28 tests; real Supabase ES256 token verified against the live JWKS |
| Chat orchestration (OpenAI) | Complete | 33 offline tests, plus 15/15 against the live OpenAI API; live turns persisted to Supabase |
| Frontend (Next.js + MapLibre) | Complete | 33 unit tests; `next build` clean; driven in a real browser |
| Supabase store and quotas | Complete | Schema applied; 12-check live round-trip of `PostgresStore`; gateway quota store queries the real database |
| Supabase auth | Complete | Real sign-in issues an ES256 token; the running gateway verifies it against the live JWKS and 401s both a missing and a malformed token |
| Graph, live | Ingested and idempotent | Schema applied to a real Neo4j; both ingesters run twice leave counts identical (`scripts/smoke_graph.py`) |
| Spatial matching | Complete | Fixture re-cut along real OSM ways; 39 `COMPOSED_OF` edges, idempotent |
| Routing (GDS Dijkstra) | Wired and verified live | `/routes` served a real 223 m POI route via GDS; Dijkstra beat shortestPath 2322 m vs 2474 m on the verification pair; shortestPath fallback kept for GDS-absent starts |
| Sign-in + conversations | Complete | Real browser session against the full stack: sign-in, history resumed under RLS, live streamed turn, trail drawn on the map; anon role reads zero rows |
| Playwright e2e | Complete | 4/4 against the live stack in ~10 s; first run caught a real mid-stream remount bug |
| Gateway claim pinning | Complete | iss/aud pinned when SUPABASE_URL is set; right-key/wrong-claim tokens 401 in tests, real token passes live |
| Semantic search | Complete | 503 verified live on the unpopulated index; job idempotent (3 embedded, 0 on re-run); three distinct queries each ranked the intended trail first |
| Query decomposition + composer | Complete | Model decomposes into atomic subqueries; Python composer merges tightest-wins, drops vacuous 0-bounds, clarifies with suggestions when under-specified; 17/17 live containment (adversarial 7/7 clarify) |
| Semantic + filters in chat | Complete | New `semantic_search_trails_filtered` template (vector pool → NULL-idiom filters); degrades to structured search with `semantic_unavailable` while the index is cold |
| Trailforks links | Complete | `trailforks_url` stored at ingestion (from `alias`/explicit URL, never guessed), returned by all trail templates, linked on TrailCard and cited by the answer prompt |
| Golden dataset eval | Complete | `fixtures/golden_questions.json` (24 questions incl. Bergamo + season-hazard cases) + `scripts/eval_golden.py`; live: decomposition 24/24, retrieval 16/21 ranked-first (misses: no bathing_water POI in either bbox; model over-constraining ambiguous phrasings) |
| NEAR_POI proximity edges | Complete, live-verified | `(:Trail)-[:NEAR_POI {distance_m}]->(:POI)` at ingestion (500 m); fixture walks now anchor near a lake/hut, so lake and hut filters return the right trail live |
| POI full-text lookup | Complete, live-verified | `poi_name_fulltext` Lucene index; route resolution queries it first with escaped input (`core/text.py`), CONTAINS as fallback |
| Richer embedding input | Complete | Input now adds activity/difficulty, seasons, and POIs along the way; sha-gated job re-embedded only changed trails |
| Season-scoped hazards | Complete | `hazards_<season>` lists on Trail, `seasonal_hazards` stays the union; queries check the requested season's list (union when unseasoned); unscoped records get the union in every season |
| Bergamo region | Complete, live-ingested | Multi-region config (`REGIONS`), `osm_ingest --region`; 24,859 intersections / 25,755 segments / 51,503 edges / 101 POIs from live Overpass; two new mock trails (Canto Alto Skyline hike, Colli di Bergamo Ride mtb) anchored on real Bergamo POIs |

Totals: 148 backend, 34 gateway, 33 frontend unit tests plus 4 e2e, all
passing. CI runs the three unit suites and stays fully offline; the e2e suite
is a local/pre-deploy check that skips itself without credentials.

## The two properties the redesign exists to guarantee

**The browser never reaches anything but the gateway.** The gateway is the only
public service. It verifies Supabase JWTs, rate-limits per user with an IP
fallback, enforces the origin allowlist, pre-checks the LLM quota, and proxies
only `/trails`, `/routes`, and `/chat`. Everything else 404s there. The backend
trusts only the shared-secret hop and never parses a token.

**The model never writes Cypher.** Its only structured output is a plan of
validated atomic subqueries (`TrailSearchIntent | RouteIntent |
SemanticThemeIntent | ClarifyIntent`). `chat/composer.py` — Python, not the
model — merges them tightest-wins and maps the result onto named, read-only,
parameterized templates; a semantic theme is embedded server-side and reaches
the vector index as a list of floats. No field in the schema can carry a query,
a template name, or an identifier, and one `Clarify` anywhere in the plan stops
the whole turn. Against the live API, seven of seven injection and jailbreak
payloads were contained this way.

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

## Dependency audit, triaged 2026-08-17

`npm audit` reported 7 findings in the gateway and 8 in the frontend. They are
not equally serious and the counts are misleading, so here is what each one
actually meant.

**The gateway's critical was real and is fixed.**
`@fastify/http-proxy` 10 carried GHSA-gwhp-pf74-vj37 — a client can name a
header in `Connection:` and have the proxy strip it *after* the rewrite hook
added it. That is precisely the gateway's trust mechanism: `app.ts` injects
`x-gateway-secret`, `x-user-id` and `x-user-email` in `rewriteRequestHeaders`.
The saving grace is that both consumers **fail closed** —
`GatewayTrustMiddleware` 401s on a missing or wrong secret, and `/chat` 401s on
an empty `x-user-id` — so the reachable impact was a caller denying its own
request, not forging an identity or evading the quota ledger. Client-supplied
`x-user-id` was never a risk either: the rewrite spreads incoming headers first
and then overwrites. Upgraded to `@fastify/http-proxy` 11.6.0; 34/34 gateway
tests and typecheck pass. Gateway production dependencies are now at zero
findings.

**Everything else was dev- or build-time.** The `vitest`/`vite`/`esbuild` chain
(GHSA-67mh-4wv8-2f99) only exposes a dev server on a developer's machine;
bumping `vitest` to 3 in both packages cleared it, with all tests still passing.

**Three high findings remain in the frontend and are deliberately deferred.**
`postcss` (CSS-stringify XSS and `sourceMappingURL` path traversal) and `sharp`
(inherited libvips CVEs) are both reached only through `next` 14, and npm's only
fix is `next` 16 — a two-major framework migration. Neither is reachable as this
app is built: the CSS is authored in-repo rather than attacker-supplied, and
nothing imports `next/image`, which is what pulls `sharp` into a running server.
Do the Next upgrade as its own piece of work, not as an audit drive-by.

**Merged 2026-08-17.** The full stack (vaivia-neo4j, backend, gateway, frontend)
was brought up locally and the SSE-proxied chat turn was verified by hand
through the browser rather than the Playwright suite — sign-in, streaming
reply, and the trail drawn on the map all worked through the new
`@fastify/http-proxy` major. `chore/dep-audit` merged to `main` on that basis.

**New finding from that manual pass: retrieval quality is poor.** The pipeline
mechanics work (decomposition, composer, templates, SSE), but the answers
returned for open-ended questions were weak. Not yet triaged — no root cause
identified (ranking, template coverage, embedding input, or the mock data
itself are all candidates). This is now the top item to investigate, ahead of
the `next` 14→16 upgrade and deploy plumbing.

## What blocks progress

1. **Credentials shared in plaintext must be rotated before any deployment.**
   The OpenAI key in `backend/.env`, and the Supabase database password, have
   both been pasted into chat transcripts. They work today and are gitignored;
   treat both as compromised.
2. **Real Trailforks data still needs licensing review.** The mock fixture now
   traces real OSM ways (so matching, `COMPOSED_OF`, and routing are all
   exercised), but the three trails themselves remain synthetic until live
   Trailforks data clears review (docs/fragilities.md #4).
3. **The account password is `12345678`.** It is eight characters, entirely
   numeric, and has been pasted into a chat transcript. Fine for a scratch
   login today; it must not survive contact with a deployed service.


## Running the graph locally

Docker Desktop is installed and the stack runs. Note two local specifics:

- **Neo4j is on 7688/7475, not the defaults.** An older copy of this project
  (container `god-neo4j`, `restart: unless-stopped`, from
  `…\Learning\google, kaggle, antropic, openai\dev\agentic\get-out-door`)
  already binds 7687/7474 and starts with Docker Desktop. The compose ports are
  now variables; the root `.env` moves this stack aside so both can run. Stop
  that container and clear `NEO4J_*_PORT` if you would rather have the defaults.
- **The first boot after Docker starts can lose GDS.** The plugin installer
  fetches a version manifest over the network, and on a cold Docker Desktop it
  ran before networking was ready: APOC installed, GDS silently did not, and
  Neo4j started anyway. Recreating the container fixed it. If `gds.version()`
  is unknown, that is what happened — recreate rather than debug.

Current state: schema applied, two regions ingested — Lecco (15,937 segments /
31,848 edges) and Bergamo (25,755 segments / 51,503 edges, live Overpass) —
GDS 2.13.12 loaded, five mock trails matched and embedded. The data volume
persists across `docker compose … down`, so `up -d neo4j` restores the graph
without re-running ingestion.

## Suggested order of work

The whole product works end to end and is pinned by a repeatable Playwright
suite (`cd frontend && E2E_EMAIL=… E2E_PASSWORD=… npm run test:e2e`, add
`E2E_LIVE=1` to spend one real OpenAI turn). Its first run earned its keep by
catching a bug the manual browser pass missed: a brand-new chat's first answer
was destroyed mid-stream, because assigning the conversation id remounted the
panel. What remains is deploy plumbing (Caddy TLS, VPS deploy script, backup
cron, uptime check against /healthz) and the credential rotations. Rotate all
three credentials before anything deploys.

Both gateway findings from the auth verification are resolved. The "missing
health endpoint" turned out to be a wrong finding: the gateway has always served
`/healthz` (matching the backend's path) — the check that produced the finding
curled `/health`. And the gateway now pins `iss` (`<project-url>/auth/v1`) and
`aud` (`authenticated`) whenever `SUPABASE_URL` is configured, verified both by
negative tests (right key, wrong issuer or audience -> 401) and live: a real
Supabase token still passes with pinning active.

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
  "project": "VaiVia",
  "org": "ai safe earth",
  "status": "amber",
  "updated": "2026-08-17",
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
        { "date": "2026-08-16", "text": "The auth plugin throws a named error when SUPABASE_JWT_JWKS_URL is empty; config only requires it in production, so outside production the empty string reached new URL and killed boot with a bare ERR_INVALID_URL" },
        { "date": "2026-08-16", "text": "run_cypher_file strips // comments before splitting on semicolons; splitting first cut comments containing semicolons in half and executed the tail as Cypher, which is why applying the schema to a real database failed on 'durations are MINUTES.'" },
        { "date": "2026-08-16", "text": "The Overpass client sends a descriptive User-Agent, overridable via OVERPASS_USER_AGENT; Overpass answers the default python-httpx UA with 406, which is not retryable, so live OSM ingestion could never have worked" },
        { "date": "2026-08-16", "text": "Compose host ports for Neo4j are variables defaulting to 7474/7687, because an older copy of this project already binds those on the dev machine and starts with Docker Desktop" },
        { "date": "2026-08-16", "text": "Compose uses the Neo4j 5 server.memory.* setting names; the dbms.memory.* forms worked but warned on every boot" },
        { "date": "2026-08-16", "text": "The fixture's trail geometry is generated from the ingested graph by scripts/make_trailforks_fixture.py rather than hand-written, so it always traces real ways and spatial matching is exercised offline; metadata is preserved because tests pin it" },
        { "date": "2026-08-16", "text": "/routes prefers GDS Dijkstra over a per-request bbox projection with a unique name dropped in finally, and falls back to shortestPath when GDS is unavailable, because the GDS plugin silently skips installation when its network fetch fails at container start" },
        { "date": "2026-08-16", "text": "GDS streams node ids only, so the route_edge_details template maps consecutive node pairs back onto CONNECTS_TO to recover gain, surfaces and way ids; parallel edges resolve to the shortest, matching what Dijkstra weighted by" },
        { "date": "2026-08-16", "text": "The browser reads conversations and messages directly from Supabase under the migration's select-only RLS policies (auth.uid() = user_id); this is what those policies were written for and does not breach the gateway-only rule, which guards backend, Neo4j and OpenAI. Writes still go only through the backend" },
        { "date": "2026-08-16", "text": "Switching conversations remounts ChatPanel via a React key instead of syncing state with effects, so no message or stream state can leak across conversations" },
        { "date": "2026-08-16", "text": "The panel remount key changes only on explicit navigation, never when a fresh chat's first turn is assigned a conversation id: keying on selected remounted the panel mid-stream and destroyed the arriving answer, a bug the manual browser pass missed and the first scripted e2e run caught" },
        { "date": "2026-08-16", "text": "The e2e suite reads credentials from E2E_EMAIL/E2E_PASSWORD and skips when unset so CI stays offline; the live OpenAI turn is additionally gated behind E2E_LIVE=1" },
        { "date": "2026-08-16", "text": "The gateway pins token iss to <project-url>/auth/v1 and aud to authenticated whenever SUPABASE_URL is configured; unset leaves behaviour unchanged for dev without Supabase" },
        { "date": "2026-08-16", "text": "Semantic search embeds the user's text server-side and passes the vector as a query parameter, so free text never approaches Cypher; the endpoint returns 503 while the vector index is unpopulated rather than an empty list" },
        { "date": "2026-08-16", "text": "The embedding job stores a sha256 of the owner-ratified input text on each Trail and skips unchanged trails on re-run, making it idempotent and safe to run after every ingestion; vectors are written with db.create.setNodeVectorProperty so the index sees a typed vector" },
        { "date": "2026-08-16", "text": "Chat decomposes each message into atomic subqueries (trail_search, semantic_theme, route, clarify) and a Python composer merges them tightest-wins onto templates; one clarify anywhere poisons the whole plan so a half-adversarial decomposition never half-runs" },
        { "date": "2026-08-16", "text": "The composer nullifies non-positive bounds: under strict structured outputs the model occasionally writes 0 to mean no-limit, and a 0-metre max silently filters out every trail (found by the golden eval, g09)" },
        { "date": "2026-08-16", "text": "Semantic themes compose with structured filters in one template (vector candidate pool then NULL-idiom WHERE); while the index is unpopulated chat degrades to structured search and flags semantic_unavailable instead of 503ing the turn" },
        { "date": "2026-08-16", "text": "trailforks_url is stored only when the source record names it (alias or explicit URL) — never guessed from an id; mock fixture aliases are synthetic so their links 404 until real Trailforks data lands" },
        { "date": "2026-08-16", "text": "Golden eval (scripts/eval_golden.py) scores decomposition and retrieval separately so a failure names its layer; retrieval misses are all POI-coverage gaps in the mock graph, not pipeline bugs" },
        { "date": "2026-08-16", "text": "Trail-level NEAR_POI proximity edges (500 m, computed at ingestion with delete-then-recreate) complement segment-level PASSES_BY; 500 m because area features ingest as one node — the lake's node sits ~400 m off its own shoreline path" },
        { "date": "2026-08-16", "text": "Fixture trail walks anchor at the intersection nearest a lake/hut POI so the traced geometry passes the features its prose describes; owner declined season-scoped hazards for now" },
        { "date": "2026-08-16", "text": "POI name resolution goes Lucene full-text first with Python-side escaping (core/text.py), CONTAINS as fallback; CALL subqueries modernized to the CALL (t) scope-clause form after live deprecation warnings" },
        { "date": "2026-08-16", "text": "Embedding input extended (owner-ratified) with activity/difficulty, best seasons, and POIs along the way; the sha gate re-embedded only changed trails" },
        { "date": "2026-08-16", "text": "Grouping variables cannot appear inside an aggregation expression in one WITH (direct + collect(x) is a syntax error live); offline FakeDb cannot catch Cypher syntax, only the live run did" },
        { "date": "2026-08-16", "text": "Hazards are season-scoped (hazards_spring/summer/autumn/winter; seasonal_hazards stays the union for display); a hazard filter with a season checks that season only, and unscoped source records put the union in every season as the conservative reading" },
        { "date": "2026-08-16", "text": "Coverage is multi-region via the REGIONS setting (Lecco, Bergamo); Bergamo's bbox starts at the city and runs north into the hills so the plains' road grid stays out of the graph; trail-region links recompute from geometry each run, deleted first so a moved trail drops its stale region" },
        { "date": "2026-08-16", "text": "Fixture anchors carry an optional near-point: with Bergamo data present, a type-only 'nearest hut' anchor silently relocated the Lecco traverse onto a Bergamo bivouac, so anchors that mean a specific area must say so" },
        { "date": "2026-08-17", "text": "Project renamed get-out-door to VaiVia: remote is github.com/ai-safe-earth/VaiVia.git, packages are vaivia / vaivia-gateway / vaivia-frontend, container is vaivia-neo4j. The compose volumes keep their names so the ingested graph survives the rename; only the container is recreated" },
        { "date": "2026-08-17", "text": "Renaming the root folder invalidates every console-script shim in backend/.venv, because Windows .exe launchers hardcode the absolute interpreter path; uv run black failed with 'Failed to canonicalize script path' until .venv was deleted and uv sync re-run" },
        { "date": "2026-08-17", "text": "@fastify/http-proxy upgraded 10 to 11.6.0 for GHSA-gwhp-pf74-vj37 (Connection-header abuse strips proxy-added headers, which is exactly how the gateway injects x-gateway-secret and x-user-id). Impact was bounded because both consumers fail closed: the trust middleware 401s on a bad secret and /chat 401s on an empty x-user-id, so the attack denied the caller's own request rather than forging identity" },
        { "date": "2026-08-17", "text": "next 14 to 16 deferred rather than taken as an audit fix: postcss and sharp are reachable only through next, the CSS is authored in-repo rather than attacker-supplied, and nothing imports next/image, so a two-major framework migration is not justified by these advisories" },
        { "date": "2026-08-17", "text": "chore/dep-audit merged to main on a manual browser verification of the SSE-proxied chat turn rather than the Playwright suite, since credentials for the automated run were not available in-session; sign-in, streaming and the map all worked through the new @fastify/http-proxy major" }
      ] }
  ],
  "blockers": [
    { "text": "OpenAI API key was shared in plaintext and must be rotated before any deployment", "severity": "high", "owner": "oscar", "since": "2026-08-15" },
    { "text": "Supabase database password was shared in plaintext and must be rotated before any deployment", "severity": "high", "owner": "oscar", "since": "2026-08-16" },
    { "text": "Real Trailforks data is pending licensing review; the fixture now traces real OSM ways but the three trails are synthetic", "severity": "low", "owner": "oscar", "since": "2026-08-15" },
    { "text": "The Supabase account password is 12345678 and was shared in plaintext; it must be changed before any deployment", "severity": "high", "owner": "oscar", "since": "2026-08-16" }
  ],
  "nextSteps": [
    { "title": "Rotate the exposed OpenAI API key, Supabase database password and account password, then update backend/.env and gateway/.env", "est": 0.5, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Triage poor retrieval quality found in manual testing: identify whether the cause is ranking, template coverage, embedding input, or the mock data itself", "est": 2, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Upgrade next 14 to 16 as its own piece of work, clearing the deferred postcss and sharp advisories", "est": 1, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" },
    { "title": "Caddy TLS, VPS deploy script, Neo4j and Postgres backup cron, uptime check", "est": 2, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" }
  ],
  "sessions": [
    { "date": "2026-08-15", "model": "fable-5", "credits": 69, "person": "oscar", "hours": null },
    { "date": "2026-08-16", "model": "opus-5", "credits": 175, "person": "oscar", "hours": null },
    { "date": "2026-08-16", "model": "fable-5", "credits": 61, "person": "oscar", "hours": null },
    { "date": "2026-08-17", "model": "opus-5", "credits": 7, "person": "oscar", "hours": null }
  ]
}
```
