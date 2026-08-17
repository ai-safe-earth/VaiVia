# VaiVia

**Ask for a trail in plain language; get an answer grounded in a real graph of the mountains.**

> *"Find a 2-day mountain bike route near Lake Como with a place to sleep."*
> *"Easy trail for kids that passes a swimming spot."*
> *"Something scenic and shaded in Bergamo, under 15 km, no exposed sections in spring."*

VaiVia (formerly *get-out-door*) is a full-stack trail assistant: a Next.js chat
and map UI, a Fastify gateway that is the only public service, a FastAPI backend,
and a Neo4j knowledge graph that fuses OpenStreetMap geometry with curated trail
metadata. Answers stream token by token and the map draws the very segments the
answer was grounded in.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Neo4j 5.x](https://img.shields.io/badge/neo4j-5.x-green.svg)](https://neo4j.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-teal.svg)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-14-black.svg)](https://nextjs.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Status:** the product works end to end against live infrastructure and is
pinned by a Playwright suite. It is pre-deployment — see
[Project status](#project-status).

---

## Why a graph

Two data sources with opposite strengths:

- **OpenStreetMap** — raw infrastructure: paths, tracks, cycleways, lakes, huts,
  bivouacs, stations. Complete geometry, no curation.
- **Trailforks** — curated human metadata: difficulty, named loops, conditions.
  Good judgement, imprecise geometry.

They are never merged. Each stays sovereign in its own nodes, joined by an
ordered `COMPOSED_OF` link inferred from proximity and tag compatibility. That
separation is what lets one query reason over both:

| Query complexity | Example |
|---|---|
| Simple (1–2 hops) | *"Show me all MTB trails near Lecco"* |
| Compound (2–3 hops) | *"Easy trail for kids that passes a swimming spot"* |
| Complex (4+ hops) | *"2-day loop with a mountain hut at the halfway point"* |

Coverage today: **Lecco** and **Bergamo**, ingested from live Overpass —
~41,700 segments, ~83,300 routing edges.

---

## Two guarantees the architecture exists to enforce

These are not implementation details; they are the reason the code is shaped the
way it is. Break either one and the review that ratified this design is void.

### 1. The browser never reaches anything but the gateway

The Fastify gateway is the only public service and carries **no business logic**.
It verifies Supabase JWTs (ES256 against the live JWKS, with `iss` and `aud`
pinned), rate-limits per user with an IP fallback, enforces an origin allowlist,
pre-checks the per-user daily LLM cost quota, and proxies exactly three paths —
`/trails`, `/routes`, `/chat`. Everything else 404s. Backend and Neo4j are
internal; the backend trusts only a shared-secret hop and never parses a token.

### 2. The LLM never sees or writes Cypher

The model's only structured output is a plan of validated atomic subqueries
(`TrailSearchIntent | RouteIntent | SemanticThemeIntent | ClarifyIntent`).
Python — `backend/chat/composer.py`, not the model — merges them tightest-wins
and maps the result onto named, read-only, parameterized templates in
`backend/graph/queries.cypher`. No field in the intent schema can carry a query,
a template name, or a database identifier. A single `Clarify` anywhere poisons
the whole plan and runs nothing. Against the live API, 7 of 7 injection and
jailbreak payloads were contained this way.

Guard tests fail the build if a template mutates data, traverses a semantic edge
inside a path expression, or leaves a traversal unbounded.

---

## Architecture

```
                          ┌──────────────────────────┐
                          │  Browser (Next.js 14)    │
                          │  chat UI + MapLibre map  │
                          └────────────┬─────────────┘
                                       │  HTTPS, SSE
                        ┌──────────────▼───────────────┐
                        │  Gateway (Fastify, TS)       │   ONLY public service
                        │  JWT · rate limit · origin   │   no business logic
                        │  quota pre-check · SSE proxy │
                        └──────────────┬───────────────┘
                                       │  shared secret, internal
                        ┌──────────────▼───────────────┐
                        │  Backend (FastAPI, Python)   │
                        │  /trails /routes /chat       │
                        │  intents → composer →        │
                        │  named Cypher templates      │
                        └───┬──────────────────────┬───┘
                            │                      │
              ┌─────────────▼──────┐   ┌───────────▼────────────┐
              │  Neo4j 5 (internal)│   │  Supabase Postgres     │
              │  APOC · GDS ·      │   │  auth · history ·      │
              │  vector index      │   │  usage ledger · quotas │
              └─────────────▲──────┘   └────────────────────────┘
                            │  idempotent ETL
        ┌───────────────────┴───────────────────┐
        │  Ingestion (async Python)             │
        │  Overpass/OSM  ·  Trailforks (mock)   │
        │  spatial matching · POI proximity     │
        │  embeddings (sha-gated)               │
        └───────────────────────────────────────┘
```

Full graph model: [`docs/architecture.md`](docs/architecture.md) ·
Known failure modes: [`docs/fragilities.md`](docs/fragilities.md) ·
Roadmap: [`docs/plan.md`](docs/plan.md)

---

## Quick start

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node 20+
- Docker (for Neo4j, with the **APOC** and **GDS** plugins)
- Optional for chat: an OpenAI API key and a Supabase project

Ingestion for local dev and CI stays **offline** — `--mock` reads
`backend/fixtures/trailforks_mock.json`, never the live Trailforks API.

### 1. Clone and configure

```bash
git clone https://github.com/ai-safe-earth/VaiVia.git
cd VaiVia
cp .env.example .env      # NEO4J_PASSWORD is required; there is no default
```

### 2. Neo4j

```bash
# run from the repo root; --env-file is required because the compose file is in infra/
docker compose --env-file .env -f infra/docker-compose.yml up -d neo4j
```

### 3. Backend: schema, data, API

```bash
cd backend && uv sync

uv run python -m scripts.init_schema                    # 16 statements, regions seeded
uv run python -m ingestion.osm_ingest --region Lecco    # live Overpass
uv run python -m ingestion.trailforks_ingest --mock     # offline fixture
uv run python -m scripts.embed_trails                   # optional: semantic search

uv run uvicorn api.main:app --reload                    # docs at :8000/docs
```

### 4. Gateway and frontend

```bash
cd gateway   && npm install && npm run dev
cd frontend  && npm install && npm run dev              # http://localhost:3000
```

Without Supabase and OpenAI configured you still get the graph endpoints;
sign-in and `/chat` need both.

---

## Repository layout

```
VaiVia/
├── backend/                # FastAPI + graph layer (Python, uv-managed)
│   ├── api/                #   routes: /trails, /routes, /chat, /healthz
│   ├── chat/               #   intents.py, composer.py, orchestrator.py, prompts.py, store.py
│   ├── core/               #   config, text escaping, shared helpers
│   ├── ingestion/          #   OSM + Trailforks ETL, spatial matching, POI proximity
│   ├── graph/              #   schema.cypher, queries.cypher, async Neo4j client, loader
│   ├── scripts/            #   init_schema, embed_trails, eval_golden, smoke_*, cost_by_commit
│   ├── fixtures/           #   trailforks_mock.json, golden_questions.json
│   └── tests/
│
├── gateway/                # Fastify (Node/TS): auth, rate limit, origin, quota, SSE proxy
├── frontend/               # Next.js + MapLibre: chat, history, map, Playwright e2e
├── infra/                  # docker-compose, Supabase migrations
├── docs/                   # architecture, data sources, query examples, fragilities, plan
├── CLAUDE.md               # the rules a contributor is most likely to break by accident
└── handoff.md              # current state, verification, blockers
```

---

## Core concepts

### The matching problem

Trailforks geometry rarely aligns with OSM geometry. VaiVia does not try to
reconcile them. A Trailforks route is linked to OSM segments by a single ordered
relationship when it falls within `SPATIAL_MATCH_THRESHOLD_M` (default 20 m) and
the `highway_type` / `surface` tags are compatible:

```
(:Trail)-[:COMPOSED_OF {seq, match_confidence}]->(:Segment)
```

`seq` is load-bearing: any distance-along-trail question must walk it. An
unordered `sum(s.length)` gives the wrong answer.

### Routing is intersection-to-intersection

```
(:Intersection)-[:CONNECTS_TO {distance, elevation_change, osm_way_id, surface, highway_type}]->(:Intersection)
```

Segments carry edge data but are **not** routing vertices. Real routing uses GDS
Dijkstra over an Intersection/`CONNECTS_TO` projection, with `shortestPath` as a
fallback when GDS is absent. Traversals are always bounded and spatially
pre-filtered.

### Graph model at a glance

| Node | Key properties |
|---|---|
| `(:Trail)` | `name`, `total_distance`, `difficulty` + numeric level, per-activity durations, elevation gain/loss, `hazards_<season>`, `seasonal_hazards`, `landscape_description`, `trailforks_url`, `embedding` |
| `(:Segment)` | `length`, `surface`, `highway_type`, `coordinates`, elevation |
| `(:Intersection)` | `lat`, `lon`, `osm_node_id` |
| `(:POI)` | `type` (lake, hut, station, …), `name` |
| `(:Region)` | `name`, bounding box |

Relationships: `COMPOSED_OF {seq}`, `CONNECTS_TO`, `PASSES_BY` (segment→POI),
`NEAR_POI {distance_m}` (trail→POI, 500 m), `LOCATED_IN`.

Trail identity lives only on `(:Trail)` — never filter by trail name on a
segment. Hazards are season-scoped: a filter naming a season checks that
season's list; without a season it checks the union.

Full schema: [`backend/graph/schema.cypher`](backend/graph/schema.cypher)

### Example queries

Easy trail near water:

```cypher
MATCH (t:Trail {difficulty: 'Easy'})-[:COMPOSED_OF]->(s:Segment)-[:PASSES_BY]->(p:POI)
WHERE p.type IN ['lake', 'water']
RETURN t.name, t.total_distance, p.name
```

More, including the routing and semantic patterns, in
[`docs/query-examples.md`](docs/query-examples.md).

---

## Testing and evaluation

```bash
cd backend  && uv run ruff check . && uv run black --check . && uv run pytest tests/ -v
cd gateway  && npm test
cd frontend && npm test
```

148 backend, 34 gateway, 33 frontend unit tests, plus 4 Playwright e2e. CI runs
the three unit suites and stays **fully offline**; the e2e suite skips itself
without credentials.

```bash
# end-to-end against a running stack (add E2E_LIVE=1 to spend one real OpenAI turn)
cd frontend && E2E_EMAIL=… E2E_PASSWORD=… npm run test:e2e

# golden dataset: does the model decompose questions correctly? (--graph adds live retrieval)
cd backend && uv run python -m scripts.eval_golden

# adversarial containment — costs money, needs OPENAI_API_KEY, must stay 7/7 clarify
cd backend && uv run python -m scripts.check_intents_live
```

Run the last two after touching prompts or intents, and extend
`backend/fixtures/golden_questions.json` when adding an intent field or template.

---

## Project status

Phases 0–5 are done and verified against live infrastructure; Phase 6 (beta
hardening) is active. What remains is deploy plumbing — Caddy TLS, a VPS deploy
script, Neo4j and Postgres backup cron, an uptime check against `/healthz` — and
a set of credential rotations that must happen before anything is deployed.

[`handoff.md`](handoff.md) is the authoritative current state: what is built,
how far each piece is verified, and what blocks progress.

---

## Data and licensing

The code in this repository is MIT-licensed (see [`LICENSE`](LICENSE)). The data
it ingests is not covered by that licence:

- **OpenStreetMap** data is © OpenStreetMap contributors, available under the
  [Open Database Licence (ODbL)](https://www.openstreetmap.org/copyright).
  Anything you publish that is derived from it must attribute OSM and respect
  the ODbL's share-alike terms.
- **Trailforks** data is proprietary. This repository ships only a synthetic
  fixture (`backend/fixtures/trailforks_mock.json`) whose geometry traces real
  OSM ways so that matching and routing are exercised offline. Live Trailforks
  ingestion is gated behind licensing review — see
  [`docs/fragilities.md`](docs/fragilities.md).

Respect the Overpass API's usage policy: send a descriptive `User-Agent`
(`OVERPASS_USER_AGENT`) and do not hammer it.

---

## Contributing

Pull requests are welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`CLAUDE.md`](CLAUDE.md) first — the second one lists the invariants that are
easiest to break by accident.

---

## License

MIT — see [`LICENSE`](LICENSE).
