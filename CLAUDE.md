# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Planning-stage repo: docs, docker-compose, and config exist, but **no source code yet**. The directory layout in README.md (`api/`, `ingestion/`, `graph/`, `scripts/`, `tests/`, `fixtures/`) is the target structure — create files there as phases land, starting with Phase 1 (schema + ingestion MVP).

## Setup and commands

- Dependencies: **uv** with `pyproject.toml` (`uv sync`), not pip/requirements.txt. Run tools via `uv run`.
- Neo4j: `docker-compose up -d neo4j` (needs APOC + GDS plugins; ports 7474/7687). Copy `.env.example` → `.env` first. The compose file's `api` service references a Dockerfile that doesn't exist yet — only the `neo4j` service is runnable.
- Ingestion for local dev/CI must stay offline: use `python ingestion/trailforks_ingest.py --mock` (reads `fixtures/trailforks_mock.json`), never the live Trailforks API.
- Checks before PR: `black .`, `ruff check .`, `pytest tests/ -v` — all must pass.

## Graph model rules (load-bearing — see docs/fragilities.md)

- **Never merge OSM and Trailforks nodes.** They are linked, not merged, via `[:MAPS_TO]` created by proximity matching (≤ `SPATIAL_MATCH_THRESHOLD_M`, default 20 m).
- **Trail identity lives only on `(:Trail)`** (Trailforks-sourced). OSM `(:Segment)` nodes have no trail name — never filter by trail name on segments; go through `[:COMPOSED_OF]`.
- **Always bound path traversals** (`-[:CONNECTS_TO*..100]-`) and spatially pre-filter; use GDS Dijkstra for large-graph routing.
- **Ingestion must be idempotent**: `MERGE` on `osm_way_id` / Trailforks IDs so re-runs update rather than duplicate.
- Semantic-search endpoints return `503` (not empty results) when the vector index is unpopulated.
- Cypher lives in `.cypher` files under `graph/`, not inline in Python.

## Code conventions

- Python 3.11+, type hints required on all public functions, async for I/O (Neo4j driver, HTTP).
- Format with Black, lint with Ruff.
- Conventional Commits (`feat:`, `fix:`, `docs:`, …); branches `feat/…`, `fix/…`, `docs/…`.
- Update the relevant file in `docs/` when a change affects the data model, query patterns, or known fragilities.
