# Contributing to get-out-door

Thank you for your interest in contributing! This document covers how to set up a local development environment, the conventions we follow, and the pull request process.

---

## Development Setup

### 1. Fork and clone

```bash
git clone https://github.com/your-username/get-out-door.git
cd get-out-door
```

### 2. Install dependencies

Backend dependencies are managed with [uv](https://docs.astral.sh/uv/) and declared in `backend/pyproject.toml`:

```bash
cd backend
uv sync
```

This creates `.venv/` and installs runtime + dev dependencies. Prefix commands with `uv run` (e.g. `uv run pytest`) or activate the venv.

### 3. Start Neo4j locally

The fastest way is Docker:

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d neo4j
```

Or use [Neo4j Desktop](https://neo4j.com/download/). Ensure **APOC** and **GDS** plugins are enabled.

### 4. Configure and initialise

```bash
cp .env.example .env
# Edit .env with your Neo4j credentials

python scripts/init_schema.py
python ingestion/osm_ingest.py --bbox 45.8,9.3,46.0,9.6
python ingestion/trailforks_ingest.py --mock
```

### 5. Run the test suite

```bash
pytest tests/ -v
```

---

## Code Conventions

### Python style

- Formatter: **Black** (`black .`)
- Linter: **Ruff** (`ruff check .`)
- Type hints are required on all public functions.
- Async (`async/await`) is preferred for I/O-bound operations (database writes, HTTP calls).

### Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add elevation backfill from SRTM
fix: handle null surface tag in OSM segments
docs: add query examples for multi-day routes
refactor: extract spatial matching into own module
test: add unit tests for proximity threshold logic
```

### Branch naming

```
feat/elevation-backfill
fix/null-surface-tag
docs/query-cookbook
```

---

## Pull Request Process

1. Open an issue first for any non-trivial change to discuss the approach.
2. Keep PRs focused — one feature or fix per PR.
3. Add or update tests for any changed behaviour.
4. Update the relevant doc in `docs/` if the change affects the data model, query patterns, or known fragilities.
5. Ensure `black`, `ruff`, and `pytest` all pass before requesting review.
6. At least one review approval is required before merging.

---

## Project Areas

| Area | Key files | Good first issues |
|---|---|---|
| Ingestion | `ingestion/osm_ingest.py`, `ingestion/trailforks_ingest.py` | Adding new POI types, improving error handling |
| Graph schema | `graph/schema.cypher` | Adding new indexes |
| Query layer | `graph/queries.cypher`, `api/routes/` | New query endpoints |
| Documentation | `docs/` | Improving examples, clarifying fragilities |
| Tests | `tests/` | Adding coverage for edge cases |

---

## Questions?

Open a GitHub Discussion or file an issue with the `question` label.
