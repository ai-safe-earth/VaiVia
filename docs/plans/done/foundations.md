---
status: done
step: foundations
next: none; every item landed
---

# Foundations — Phases 0–5

Moved from `docs/plan.md` "Delivery phases" on 2026-10-08.

## Phase 0 — Restructure & foundations ✅
- [x] This plan committed as `docs/plan.md`; architecture docs corrected.
- [x] Monorepo layout: `backend/`, `gateway/`, `frontend/`, `infra/`.
- [x] `infra/docker-compose.yml`: Neo4j Community, localhost-only port binding for dev.
- [x] CI (GitHub Actions): backend lint/format/test, offline.
- [x] Supabase Postgres migration drafts (`infra/supabase/migrations/`).
- [x] Supabase project created; `0001_chat_and_quotas.sql` applied (four tables, RLS on, one policy each). Applied with `uv run python -m scripts.apply_migrations` rather than the Supabase CLI, which expects its own `supabase/migrations` layout. The direct host `db.<ref>.supabase.co` is IPv6-only, so `DATABASE_URL` points at the Supavisor pooler in session mode (port 5432).
- [x] Email auth configured (email provider only, signups on, autoconfirm off) and a real sign-in exercised end to end: password grant returns an ES256 token whose `kid` matches the project JWKS, and the running gateway verifies it against the live JWKS — no token 401, malformed token 401, real token passes auth and the quota pre-check and is only stopped by the backend being down.

## Phase 1 — Graph core & ingestion (the moat) ✅
Ontology extended and owner-validated before build: difficulty trio (label +
level 1–4 + notes), per-activity durations (DIN 33466 hike / speed-by-level
MTB), elevation gain/loss at trail, segment, and per-direction edge level,
seasonality lists (`best_seasons`, `seasonal_hazards`), `landscape_description`
feeding the Phase 3 embedding alongside description and difficulty notes.
- [x] `backend/graph/schema.cypher` (corrected model) + `backend/scripts/init_schema.py` (applies schema, seeds region from `DEFAULT_BBOX`).
- [x] `backend/ingestion/osm_ingest.py`: Overpass with backoff + on-disk cache (`overpass_client.py`); pure topology extractor (`osm_extract.py`) splits ways at intersections into deterministic `"<wayId>#<n>"` pieces and builds the Intersection/CONNECTS_TO routing graph (oneway-aware, both directions); MERGE-idempotent loaders.
- [x] `backend/ingestion/trailforks_ingest.py --mock` + `fixtures/trailforks_mock.json` (full ontology); `spatial_match.py` creates ordered `COMPOSED_OF {seq, match_confidence}` with activity/highway compatibility checks (delete-and-recreate so re-runs never leave stale links).
- [x] Tests (29, offline): topology split/oneway/determinism, matcher precision incl. the 15 m parallel-trail residual risk, DIN/MTB duration formulas, fixture normalization.
- [x] Live-DB smoke: `uv run python -m scripts.smoke_graph` runs both ingesters twice against the compose Neo4j and compares counts. Passes — 15,937 Segment, 15,451 Intersection, 31,848 CONNECTS_TO, 489 PASSES_BY, identical across both passes, so the MERGE keys are stable. Fixed on the way: the schema loader split `.cypher` files on `;` before stripping comments, so a semicolon inside a comment sent `durations are MINUTES.` to the server as Cypher, and Overpass rejected the default httpx User-Agent with 406 so OSM ingestion could never run.
- [x] Fixture geometry re-cut along real OSM ways (`scripts/make_trailforks_fixture.py` traces connected segment chains from the ingested graph, per-activity highway types, all metadata preserved). Spatial matching now produces 15/9/15 `COMPOSED_OF` edges for the three trails, idempotent across re-ingestion (39 edges, stable). The original synthetic geometry sat ~106 m from the nearest real way against the 20 m threshold, so `COMPOSED_OF` had never existed.

## Phase 2 — Backend query service ✅
- [x] FastAPI app (`backend/api/`): `POST /trails/search`, `GET /trails/{id}`, `GET /trails/{id}/geojson`, `POST /routes`, `GET /healthz`.
- [x] Named-template library `backend/graph/queries.cypher` + `query_loader.py`; `Neo4jClient.run_named()` runs templates by name with parameters only. `TrailSearchRequest` is shaped to match Phase 4's `TrailSearchIntent` 1:1.
- [x] Routing: POI resolution → nearest-intersection snap (point index, bounded radius) → bounded `shortestPath` over `CONNECTS_TO` only; distance capped by settings.
- [x] Gateway-trust middleware (`X-Gateway-Secret`, `/healthz` public) + request-id propagation + structured JSON logging.
- [x] 34 API/template tests (63 backend total), no Neo4j needed — fake graph client records template name + parameters. Guard tests assert no template mutates data, none traverses semantic edges in a path, and no traversal is unbounded.
- [x] GDS Dijkstra wired and verified live. `scripts/smoke_routing.py` proves the templates against the real instance — on a 2.3 km test pair, Dijkstra (metre-weighted) found 2322 m where hop-count shortestPath found 2474 m. `/routes` now prefers GDS over a per-request bbox projection (unique name, always dropped in `finally`), enriches the node-only GDS stream via the new `route_edge_details` template (gain, surfaces, way ids), and falls back to shortestPath when GDS is unavailable — a real failure mode: the plugin silently skips installation when its network fetch fails at container start. Verified end to end over HTTP: a live 223 m POI-to-POI route served by GDS with real surface data, and a correct 404 for a disconnected pair.

## Phase 3 — Gateway (security layer) ✅
- [x] Fastify 5 + TypeScript (`gateway/`): Supabase JWT verification via remote JWKS (`jose`), `@fastify/rate-limit` keyed by verified user id with IP fallback, strict CORS allowlist, `@fastify/http-proxy` (SSE-capable) for `/trails`, `/routes`, `/chat` only — every other path 404s at the gateway.
- [x] Request pipeline ordered so identification runs *before* rate limiting (limits key on the verified user; unauthenticated traffic is still IP-counted rather than escaping on an early 401), with enforcement at the route preHandler.
- [x] LLM quota pre-check on `/chat` against Supabase `daily_quotas`; fails open on Postgres errors (logged) so a database blip degrades cost control, not availability.
- [x] Proxy attaches `X-Gateway-Secret`, `X-Request-ID`, and verified `x-user-id`; the caller's bearer token is never forwarded.
- [x] 25 tests (real RSA-signed JWTs, stub upstream, SSE stream assertion): 401 paths, unknown-key and expired tokens, per-user limit isolation, 429 on limit and on exhausted quota with the backend never called, CORS allow/deny, request-id adoption, health.
- [x] CI job (npm ci → lint → typecheck → test); clean `tsc --noEmit` and eslint.

## Phase 4 — Chat orchestration (LLM) ✅
- [x] `POST /chat` streams SSE (`conversation`, `intent`, `results`, `token`…, `done`, `error`). Identity comes from the gateway's `X-User-Id`; the backend never parses JWTs.
- [x] Intent contract (`backend/chat/intents.py`): `TrailSearchIntent | RouteIntent | ClarifyIntent`, pydantic-validated and dispatched to named templates **in Python**. The model never sees Cypher, never names a template, never supplies an identifier. Adversarial or out-of-scope input → `Clarify`, which runs no query at all.
- [x] OpenAI strict structured outputs. Pydantic's tagged-union schema needed transforming: strict mode rejects `oneOf` and `discriminator`, so `to_strict_schema()` rewrites to `anyOf`, closes every object, and marks every property required.
- [x] Grounded answers: the answer model only sees results the graph returned; `result_refs` pins every reply to real trail ids.
- [x] Quota enforced before any model call (authoritative; the gateway also pre-checks). Usage written to the ledger after each turn.
- [x] History persisted per conversation with an ownership check — one user cannot continue another's conversation.
- [x] 33 offline tests (96 backend total): pipeline, dispatch, quota, history, ownership, injection containment.
- [x] **Live verification** — `uv run python -m scripts.check_intents_live`: 15/15 against the real API (8 natural phrasings → correct intents and fields; 7 injection/out-of-scope payloads → all `clarify`). Costs money, so it is a script, not CI.
- [x] Swap `InMemoryStore` for the written `PostgresStore` (asyncpg). The lifespan now opens a pool when `DATABASE_URL` is set and falls back to in-memory only when it is not — previously it warned about the missing variable and then used `InMemoryStore` either way. Verified against the live database by `uv run python -m scripts.smoke_supabase` (12 checks inside a transaction that is always rolled back).

## Phase 5 — Frontend ✅
- [x] Next.js 15 + React 19 app (`frontend/`): chat-first two-pane layout (superseded by Phase 11), streaming replies, trail result cards, MapLibre map drawing the selected trail's real geometry.
- [x] `lib/sse.ts` — incremental SSE parser that survives frames split across arbitrary network chunks (mid-JSON, between `event:` and `data:`, on the separator itself); 13 tests including a character-by-character stream.
- [x] `lib/api.ts` — the app's only network surface: gateway URL from env, Supabase bearer attached, 401/429 mapped to usable messages, malformed frames skipped rather than tearing down the stream.
- [x] `lib/format.ts` — the single place metres/minutes become human units.
- [x] Map: OSM raster tiles (no API key, correct attribution), auto-fit bounds; `dynamic(ssr: false)` since MapLibre touches `window` at import. The tiles are desaturated and darkened at the raster layer (not by a CSS filter, which would take the route line with it) so the light basemap sits on the dark ground; the route line is lime at 3px with no casing. The 2026-08-27 difficulty band ramp (lime/amber/flare/muted, mtb near-black) was withdrawn 2026-08-28: every line is lime, selection is width/opacity only (Phase 11 U0).
- [x] Verified: 25 tests pass, `next build` compiles and type-checks clean, production server serves the rendered app, and no secret appears in the bundle.
- [x] Sign-in page + conversation list. Email/password auth (`AuthPanel`, sign-up with the confirm-email flow, friendly error mapping), a session bar with sign-out, and a conversation list read straight from Supabase under the migration's RLS policies — select-only, `auth.uid() = user_id`, which is what those policies were written for; writes still go only through the backend. Selecting a conversation loads its history and resumes it (the page remounts `ChatPanel` via `key`, so no state leaks across switches). Verified in a real browser against the full live stack: sign-in as a real user, history resumed, a live streamed follow-up turn answered with a graph-grounded trail, and its geometry drawn on the map. RLS also verified negatively: the anon role sees zero rows.
- [x] Playwright end-to-end smoke (`frontend/e2e/smoke.spec.ts`, `npm run test:e2e`). Four tests against the full running stack: wrong password gets a human sentence, sign-in/sign-out round-trip, resuming a stored conversation renders its history, and — behind `E2E_LIVE=1`, since it costs an OpenAI turn — a live streamed answer whose trail card draws geometry on the map. Credentials come from `E2E_EMAIL`/`E2E_PASSWORD`, never the repo; the suite skips cleanly when they are unset, so CI stays offline. First run caught a real bug: creating a conversation remounted `ChatPanel` mid-stream (the panel key tracked `selected`) and destroyed the streaming answer — the key now changes only on explicit navigation. 4/4 passing.
- [x] Brand system v1.0 applied (`assets/brand/BRAND-SPEC.md`): dark ground, two accents (lime = route/action/confirmed, flare = hazard/stale/missing), square corners, 1px hairlines, no shadows. Tokens are imported once globally (`app/tokens.css`) and no component holds a hex literal — the single exception is MapView, which resolves `--vv-lime` off the root because MapLibre paint properties cannot read a CSS variable. The transcript became a ruled document rather than bubbles, the route card a hairline-delimited band with a figure row, and every route now carries a Sources disclosure stating that the two sources are matched by proximity and never merged.
