# Redesign & Delivery Plan

Living roadmap for get-out-door. Ratified 2026-08-15 after an architecture review. Update this file as phases land.

---

## Context

The original skeleton had a sound core (the two-source OSM + Trailforks knowledge graph) but incomplete or wrong surroundings:

- The LLM layer — the product's point — was undesigned and implied raw LLM-generated Cypher (injection + hallucination risk).
- The routing model was internally inconsistent: `CONNECTS_TO` was Segment→Intersection, but GDS routing requires Intersection–Intersection edges.
- `COMPOSED_OF`/`MAPS_TO` were redundant and unordered, silently breaking distance-along-trail queries ("hut at the halfway point").
- No security, auth, observability, or deployment story.

## Product decisions

| Decision | Choice |
|---|---|
| Ambition | Public beta product (real users, abuse protection, LLM cost caps) |
| UX | Chat-first frontend with map rendering of returned routes |
| Frontend | Next.js (independent app) |
| Gateway | Fastify (Node/TS) — auth, rate limits, origin control, SSE proxy; **no business logic** |
| Backend | FastAPI (Python) — chat orchestration + graph query service |
| Graph DB | Neo4j 5 **Community** + APOC + GDS |
| Auth + relational store | Supabase (JWT auth; Postgres for chat history, quotas, cost ledger) |
| LLM | OpenAI (structured-output intent extraction; text-embedding-3-small) |
| Hosting | Single VPS + docker-compose now; 12-factor so pieces can move to PaaS later |
| Repo | This monorepo: `frontend/`, `gateway/`, `backend/`, `infra/`, `docs/` |
| Beta data scope | Lake Como / Lecco bbox only |
| Streaming | SSE end-to-end from day one |

## Target architecture

```
Browser (Next.js app)
   │  HTTPS + Supabase JWT
   ▼
Caddy (TLS) ── Fastify GATEWAY  (public: the ONLY exposed service)
   │   • Supabase JWT validation (JWKS), origin/CORS control
   │   • per-user + per-IP rate limits, daily LLM quota check (Postgres)
   │   • request IDs, structured logs, SSE passthrough
   ▼  internal docker network only
FastAPI BACKEND (chat orchestration + graph query service)
   │   • /chat: OpenAI structured-output intent extraction (NEVER raw Cypher)
   │   • intent JSON validated (pydantic) → parameterized query template
   │   • conversation history + cost ledger in Supabase Postgres
   ▼
Neo4j Community + APOC + GDS (internal only)
   ▲
Ingestion jobs (Overpass w/ backoff+cache, Trailforks --mock, spatial matcher)
```

Security model: browsers never reach FastAPI, Neo4j, or OpenAI. The gateway is the single ingress; the backend trusts only the gateway (shared-secret header + network isolation); LLM calls happen server-side with per-user cost caps enforced before each call.

## Graph model (corrected)

1. **Routing graph:** `(:Intersection)-[:CONNECTS_TO {distance, elevation_change, osm_way_id, surface, highway_type}]->(:Intersection)`. Segments are edge data on the routing graph; `(:Segment)` nodes remain for trail composition and POI proximity. GDS Dijkstra projects Intersection/CONNECTS_TO consistently.
2. **`[:MAPS_TO]` is dropped.** Single ordered relationship `(:Trail)-[:COMPOSED_OF {seq, match_confidence}]->(:Segment)`; `seq` makes distance-along-trail queries correct.
3. **Region:** numeric bbox properties; `(:Trail)-[:LOCATED_IN]->(:Region)` and `(:POI)-[:LOCATED_IN]->(:Region)` created at ingestion time.
4. Routing queries never traverse semantic edges (`PASSES_BY`); all traversals bounded.

## The LLM boundary (intent contract)

`backend/app/intents/schema.py` defines pydantic models — e.g. `TrailSearchIntent {activity, difficulty[], distance_km_range, duration_hours?, poi_constraints[{type, position: along|near|midpoint|endpoint}], region, surface_exclusions[], multi_day?}`, `RouteIntent {from_poi, to_poi, max_km}`, and `Clarify {question}`. OpenAI structured outputs (strict json_schema) produce exactly one of these; out-of-scope input → `Clarify`. Each intent maps to a named parameterized template in `backend/graph/queries.cypher`. **The model never sees or writes Cypher.**

## Delivery phases

### Phase 0 — Restructure & foundations ✅
- [x] This plan committed as `docs/plan.md`; architecture docs corrected.
- [x] Monorepo layout: `backend/`, `gateway/`, `frontend/`, `infra/`.
- [x] `infra/docker-compose.yml`: Neo4j Community, localhost-only port binding for dev.
- [x] CI (GitHub Actions): backend lint/format/test, offline.
- [x] Supabase Postgres migration drafts (`infra/supabase/migrations/`).
- [ ] Supabase project created + auth configured (manual, needs account).

### Phase 1 — Graph core & ingestion (the moat) ✅
Ontology extended and owner-validated before build: difficulty trio (label +
level 1–4 + notes), per-activity durations (DIN 33466 hike / speed-by-level
MTB), elevation gain/loss at trail, segment, and per-direction edge level,
seasonality lists (`best_seasons`, `seasonal_hazards`), `landscape_description`
feeding the Phase 3 embedding alongside description and difficulty notes.
- [x] `backend/graph/schema.cypher` (corrected model) + `backend/scripts/init_schema.py` (applies schema, seeds region from `DEFAULT_BBOX`).
- [x] `backend/ingestion/osm_ingest.py`: Overpass with backoff + on-disk cache (`overpass_client.py`); pure topology extractor (`osm_extract.py`) splits ways at intersections into deterministic `"<wayId>#<n>"` pieces and builds the Intersection/CONNECTS_TO routing graph (oneway-aware, both directions); MERGE-idempotent loaders.
- [x] `backend/ingestion/trailforks_ingest.py --mock` + `fixtures/trailforks_mock.json` (full ontology); `spatial_match.py` creates ordered `COMPOSED_OF {seq, match_confidence}` with activity/highway compatibility checks (delete-and-recreate so re-runs never leave stale links).
- [x] Tests (29, offline): topology split/oneway/determinism, matcher precision incl. the 15 m parallel-trail residual risk, DIN/MTB duration formulas, fixture normalization.
- [ ] Live-DB smoke: run init_schema + both ingesters against compose Neo4j, assert re-run leaves counts unchanged (needs Docker running).

### Phase 2 — Backend query service ✅
- [x] FastAPI app (`backend/api/`): `POST /trails/search`, `GET /trails/{id}`, `GET /trails/{id}/geojson`, `POST /routes`, `GET /healthz`.
- [x] Named-template library `backend/graph/queries.cypher` + `query_loader.py`; `Neo4jClient.run_named()` runs templates by name with parameters only. `TrailSearchRequest` is shaped to match Phase 4's `TrailSearchIntent` 1:1.
- [x] Routing: POI resolution → nearest-intersection snap (point index, bounded radius) → bounded `shortestPath` over `CONNECTS_TO` only; distance capped by settings.
- [x] Gateway-trust middleware (`X-Gateway-Secret`, `/healthz` public) + request-id propagation + structured JSON logging.
- [x] 34 API/template tests (63 backend total), no Neo4j needed — fake graph client records template name + parameters. Guard tests assert no template mutates data, none traverses semantic edges in a path, and no traversal is unbounded.
- [ ] GDS Dijkstra swap-in: `route_gds_dijkstra` + `graph_project_routing` templates are written but not wired to the endpoint until they can be verified against a live GDS instance.

### Phase 3 — Gateway (security layer)
- Fastify + TypeScript: `@fastify/jwt` with Supabase JWKS, `@fastify/rate-limit` (per-user + per-IP), strict CORS/origin allowlist, `@fastify/http-proxy` to backend (SSE-capable), request-ID propagation, quota pre-check against Postgres.
- Contract tests: unauthenticated → 401, wrong origin → 403, over-limit → 429, happy path proxies.

### Phase 4 — Chat orchestration (LLM)
- `/chat` in backend, SSE streaming end-to-end: history load (Postgres) → OpenAI structured intent → template query → grounded answer composed from actual graph results (IDs + GeoJSON refs, no fabricated trails) → history + cost-ledger write.
- Cost caps: per-user daily token budget enforced before each OpenAI call; 429-with-message when exhausted.
- Prompt-injection posture: intent schema is the only LLM output consumed; no write-capable tools; adversarial test set ("delete all data", nonsense regions) must yield `Clarify`/refusal.

### Phase 5 — Frontend
- Next.js: Supabase sign-in, streaming chat UI, MapLibre GL map rendering trail GeoJSON from result cards, conversation list.
- Talks only to the gateway; environment-driven base URL.

### Phase 6 — Beta hardening
- Embeddings job (`backend/scripts/embed_trails.py`) + vector search behind the 503-until-populated rule.
- Caddy TLS, VPS deploy script, Neo4j + Postgres backup cron, uptime check, dashboards from structured logs.

## Verification per phase

- Every phase lands with CI green, fully offline (`--mock`).
- Phase 1: run ingestion twice, assert node/relationship counts unchanged; cookbook queries return expected fixture results.
- Phases 2–3: end-to-end curl through gateway → backend → Neo4j on compose; auth/limit contract tests.
- Phase 4: golden set of ~20 NL queries → expected intents (snapshot tests); adversarial set never produces a write.
- Phase 5: Playwright smoke — sign in, ask "easy trail near a lake", see result cards + map polyline.
