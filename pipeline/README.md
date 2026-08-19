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
curate/     route-relation join (edge_route), DEM sampling (elevation), scoring, dedup
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

# base layers (Phase 2). Downloads land in pipeline/data/ (gitignored):
#   nord-ovest-latest.osm.pbf   https://download.geofabrik.de/europe/italy/
#   glo30_N45_E009.tif          s3://copernicus-dem-30m/Copernicus_DSM_COG_10_N45_00_E009_00_DEM/
#   trenord_gtfs.zip            https://www.dati.lombardia.it/download/3z4k-mxz9/application/zip
uv run python -m load.osm  --pbf data/nord-ovest-latest.osm.pbf
uv run python -m load.dem  --tif data/glo30_N45_E009.tif
uv run python -m load.gtfs --zip data/trenord_gtfs.zip --feed trenord
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

Ready-made layers, latest run only — add these by name rather than filtering by hand:

| layer | what it shows |
|---|---|
| `qa.v_network` | every edge, with highway/surface/sac_scale/name — the context the findings sit on |
| `qa.v_gap_dangle_pair` | two loose ends within tolerance, not joined. **The classic gap** |
| `qa.v_gap_dangle_edge` | a loose end near another edge's interior: under/overshoot |
| `qa.v_gap_dangle_junction` | a loose end stopping just short of an existing junction |
| `qa.v_overlap` | the same ground mapped twice, with `shared_m` |
| `qa.v_degenerate` | sub-metre edges and self-loops |
| `qa.v_island` | components too small to hold a route |
| `qa.v_dangle` | all loose ends — for judging whether a dangle is a defect or a real dead end |
| `qa.v_fix` | what the last repair pass changed, with how far each end moved |
| `qa.v_route` | **one line per named route** (752 features): ref, name, network, km, and `pieces` — how many disconnected parts it comes out in |
| `qa.v_route_edge` | every edge that carries a route, with the route's identity as real columns |
| `qa.v_route_coverage` | how much of each relation the network holds; `matched_fraction` near 0 is a route clipped away by the region bboxes |
| `qa.v_elevation` | every edge with ascent, descent and **gradient** — style the network by steepness |
| `qa.v_route_elevation` | the 752 routes with climb, lowest and highest point |

`qa.finding` and `qa.fix` are the underlying tables; `qa.fix` carries before/after geometry
for every automated repair, so a repair pass is reviewable after the fact and reversible if
a tolerance was wrong.

```bash
uv run python -m topology.build_network            # noded network + components
uv run python -m topology.qa --measure             # the near-miss distribution
uv run python -m topology.histogram                # the same, as a PNG to look at
uv run python -m topology.qa --dry-run             # counts, writes nothing
uv run python -m topology.qa --tolerance-m 2.0     # write findings
uv run python -m topology.repair --dry-run         # what would change, writes nothing
uv run python -m topology.repair                   # repair, recording every change

uv run python -m curate.routes --dry-run           # what the route join would write
uv run python -m curate.routes                     # join route relations onto the network
uv run python -m curate.routes --check             # is the stored join still current?

uv run python -m curate.elevation --dry-run        # DEM coverage + the check against OSM ele tags
uv run python -m curate.elevation                  # sample the DEM onto vertices and edges (~5 min)
uv run python -m curate.elevation --check          # is the stored profile still aligned?
```

The route join and the elevation sample both run **after** build and repair, and both of
those clear them: the join holds `edge_id`s and the profile is aligned to `geom` point by
point, so each is true only of the network that produced it. `--check` says whether what is
stored still describes the network in the database.

Elevation is sampled **bilinear**, and there is deliberately no noise threshold. Both were
measured, not chosen — nearest-neighbour sampling invents 47% of the climb on this network,
and `|dz|` never plateaus as point spacing falls, so there is no noise floor to subtract.
The tables are in `docs/metadata-rules.md`. Judge the DEM's accuracy on **saddles** (~4 m),
never on peaks: a 30 m cell reads a summit ~23 m low by design.

Repairs consume the **latest QA run's findings**, so what you judged in QGIS is what
changes. `qa.fix` holds before/after geometry for every one of them. The check that a pass
went right is the total length of the network: it must not move. See
`docs/metadata-rules.md` for what each rule repairs and the two ways the degenerate rule
was wrong before it was right.

**Look before repairing.** The tolerance comes from the histogram and from opening
examples in QGIS at each candidate distance — see `docs/metadata-rules.md`, which records
why 2 m and not 5.

## Provenance

Every curated row carries the `run_id` that produced it; `build_run` records each run's
stage, parameters and counts. Two runs are compared inside the database — there is no file
artefact to diff, deliberately.
