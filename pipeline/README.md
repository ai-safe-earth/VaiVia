# VaiVia geodata pipeline

Sources → PostGIS → curated route map → (tiles, dashboard, Neo4j export).

**The database is the product.** Curated routes, POIs and starts live in PostGIS
(`vaivia-postgis` in `infra/docker-compose.yml`); the tile server, the dashboard, QGIS and
the Neo4j export are all readers of it. `backend/` consumes the export; it no longer
produces the data.

Read first:

- `docs/data-sources.md` — the evaluated sources and their verdicts (Phase 0 deliverable).
- `docs/metadata-rules.md` — how attributes survive splitting and joining, and the
  PostGIS-vs-Python division of labour. Code implements this table, not its own judgement.

## Layout

```
sources/    acquire (Geofabrik PBF, GLO-30 tiles, GTFS, CLC+, REL, Infomont)
load/       into staging_* tables
topology/   noding, metadata propagation, QA detectors and automated repairs
draw/       route enumeration (loops, out-and-back) and assembly
curate/     aggregation rules, scoring, dedup
export/     to Neo4j
sql/        migrations, applied in filename order by migrate.py
tests/      pure-function tests (no database in the loop)
```

## Running

```bash
# from the repo root: start the working store (POSTGIS_PASSWORD in .env)
docker compose --env-file .env -f infra/docker-compose.yml up -d postgis

# from pipeline/: deps, then schema
uv sync
uv run python migrate.py     # idempotent; --dry-run lists files
```

Checks before a PR (from `pipeline/`): `uv run ruff check .`,
`uv run black --check .`, `uv run pytest tests/ -v`.

## QGIS

Failures are inspected visually, then their repairs automated — never the reverse order.
Connect QGIS directly to the store (Layer → Add Layer → PostgreSQL):

| field | value |
|---|---|
| Host / Port | `127.0.0.1` / `5433` (or `POSTGIS_PORT` from `.env`) |
| Database | `vaivia_geo` |
| User / Password | `vaivia` / `POSTGIS_PASSWORD` from `.env` |

Every QA rule is a layer: filter `qa.finding` on `rule` (and `run_id` for one run).
`qa.fix` carries before/after geometry for every automated repair, so a repair pass is
reviewable after the fact and reversible if a tolerance was wrong.

## Provenance

Every curated row carries the `run_id` that produced it; `build_run` records each run's
stage, parameters and counts. Two runs are compared inside the database — there is no file
artefact to diff, deliberately.
