# 🏔️ get-out-door

> A multi-hop adventure chatbot backend that answers complex trail queries like *"Find a 2-day mountain bike route near Lake Como with a place to sleep."*

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Neo4j 5.x](https://img.shields.io/badge/neo4j-5.x-green.svg)](https://neo4j.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-teal.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What is get-out-door?

get-out-door merges two best-in-class data sources into a single knowledge graph:

- **OpenStreetMap (OSM)** — raw infrastructure: paths, tracks, cycleways, lakes, huts, train stations
- **Trailforks** — curated human metadata: difficulty ratings, named loops, trail conditions

The result is a graph database (Neo4j) that can answer multi-hop queries no flat database can handle efficiently:

| Query complexity | Example |
|---|---|
| Simple (1–2 hops) | *"Show me all MTB trails near Lecco"* |
| Compound (2–3 hops) | *"Easy trail for kids that passes a swimming spot"* |
| Complex (4+ hops) | *"2-day loop with a mountain hut at the halfway point"* |

---

## Architecture Overview

```
┌─────────────────────┐     ┌─────────────────────┐
│   OpenStreetMap     │     │    Trailforks API   │
│ (Overpass API/OSM)  │     │  (or mock fixture)  │
└────────┬────────────┘     └──────────┬──────────┘
         │  Segments, POIs             │  Trail metadata
         │  Intersections              │  Difficulty, names
         └──────────────┬─────────────┘
                        ▼
              ┌─────────────────┐
              │   Ingestion     │
              │   Pipeline      │  Python (async)
              │   (ETL layer)   │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │   Neo4j Graph   │
              │   Database      │  APOC + GDS + Spatial
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │   FastAPI       │
              │   Query Layer   │  REST + future LLM bridge
              └─────────────────┘
```

See [`docs/architecture.md`](docs/architecture.md) for the full graph data model.

---

## Quick Start

### Prerequisites

- Python 3.11+
- Neo4j 5.x (Desktop, AuraDB, or Docker)
- Neo4j plugins: **APOC**, **Graph Data Science (GDS)**

### 1. Clone & install

```bash
git clone https://github.com/your-org/get-out-door.git
cd get-out-door/backend
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your Neo4j credentials and bounding box
```

### 3. Initialize the graph schema

```bash
python scripts/init_schema.py
```

### 4. Run the ingestion pipeline

```bash
# Ingest OSM data for a bounding box
python ingestion/osm_ingest.py --bbox 45.8,9.3,46.0,9.6

# Map Trailforks metadata to OSM segments
python ingestion/trailforks_ingest.py --mock
```

### 5. Start the API

```bash
uvicorn api.main:app --reload
# Docs at http://localhost:8000/docs
```

---

## Repository Structure

Monorepo — see [`docs/plan.md`](docs/plan.md) for the full architecture and delivery phases.

```
get-out-door/
├── backend/                # FastAPI + graph layer (Python, uv-managed)
│   ├── api/                #   REST + /chat orchestration (LLM intent → query templates)
│   ├── ingestion/          #   OSM + Trailforks ETL, spatial matching
│   ├── graph/              #   schema.cypher, queries.cypher, async Neo4j client
│   ├── scripts/            #   init_schema.py, embed_trails.py
│   ├── tests/
│   ├── fixtures/           #   trailforks_mock.json (offline dev/CI)
│   └── pyproject.toml
│
├── gateway/                # Fastify (Node/TS) — auth, rate limits, origin control, SSE proxy
├── frontend/               # Next.js — chat UI + MapLibre map
├── infra/                  # docker-compose, Supabase migrations, deploy
├── docs/                   # architecture, data sources, query cookbook, fragilities, plan
├── .env.example
└── README.md
```

---

## Core Concepts

### The Matching Problem

Trailforks geometries rarely align perfectly with OSM geometries. **get-out-door does not attempt to merge them.** Instead, it links them via ordered `[:COMPOSED_OF {seq, match_confidence}]` relationships when a Trailforks route falls within ~20 metres of an OSM segment. See [`docs/fragilities.md`](docs/fragilities.md).

### Graph Data Model (Summary)

| Node | Key Properties |
|---|---|
| `(:Trail)` | `name`, `total_distance`, `difficulty`, `source` |
| `(:Segment)` | `length`, `surface`, `coordinates` |
| `(:Intersection)` | `lat`, `lon` |
| `(:POI)` | `type` (lake, hut, station), `name` |
| `(:Region)` | `name`, bounding box |

Key relationships: `COMPOSED_OF {seq}` (Trail→Segment, ordered), `CONNECTS_TO` (Intersection→Intersection routing graph), `PASSES_BY`, `LOCATED_IN`.

Full schema: [`graph/schema.cypher`](graph/schema.cypher) · Full model: [`docs/architecture.md`](docs/architecture.md)

---

## Example Queries

**Easy trail near a lake:**
```cypher
MATCH (t:Trail {difficulty: 'Easy'})-[:COMPOSED_OF]->(s:Segment)-[:PASSES_BY]->(p:POI)
WHERE p.type IN ['lake', 'water']
RETURN t.name, t.total_distance, p.name
```

**Route between two POIs under 20 km:**
```cypher
MATCH path = shortestPath(
  (a:POI {name: 'Station A'})-[:CONNECTS_TO*]-(b:POI {name: 'Hut B'})
)
WITH path, reduce(d = 0, r IN relationships(path) | d + r.distance) AS total_km
WHERE total_km < 20
RETURN path, total_km
ORDER BY total_km ASC
LIMIT 5
```

More examples in [`docs/query-examples.md`](docs/query-examples.md).

---

## Roadmap

- [ ] Phase 1 — Schema + ingestion MVP (OSM + Trailforks mock)
- [ ] Phase 2 — FastAPI query endpoints
- [ ] Phase 3 — Vector embeddings for semantic search (*"muddy after rain"*)
- [ ] Phase 4 — LLM query translation (natural language → Cypher)
- [ ] Phase 5 — Multi-day route planning with GDS pathfinding

---

## Contributing

Pull requests are welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) first.

---

## License

MIT — see [`LICENSE`](LICENSE).
