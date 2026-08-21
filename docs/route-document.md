# The route document — the product

Ratified 2026-08-20. This corrects a framing that had been right about the value and
wrong about the goal.

## What changed

`CLAUDE.md` said **"the database is the product"**, and that came out of a real fix: the
backend used to ingest OSM and derive its own geometry, and moving that upstream into
PostGIS stopped two tiers producing the same data differently. That part stands.

But PostGIS is where the **value** accumulates, not what the project **delivers**. What
VaiVia hands to anything downstream — an API response, a map, a Neo4j node, a phone — is
**a structured JSON document and a map, one per route**. That is the artefact. It is what a
user ultimately sees, what a comment will one day attach to, and what has to be right.

So, precisely:

> **PostGIS is the working store and holds the project's value. The route document is the
> product.** Curated geometry, elevation, routes and places live in PostGIS; the document
> is emitted from them by `pipeline/export/route_documents.py`.

## Everything else is a reader

The same inversion that made `backend/` stop producing data now applies one level up.

```
   PostGIS  ──emit──>  route document (JSON + GeoJSON)
                              │
              ┌───────────────┼────────────────┬──────────────────┐
              ▼               ▼                ▼                  ▼
          Neo4j           the API          the frontend      the social layer
   graph + semantic     serves it        renders it        attaches to its id
        search
```

No reader redefines a route. Neo4j holds the document for graph traversal and vector
search; the API serves it; the frontend renders it; user-generated content keys to its
`id`. If a reader needs a field the document does not have, the field goes **in the
document**, not into that reader — otherwise two tiers describe a route differently again,
which is the exact failure this architecture already corrected once.

Consequences worth stating:

- **The document is versioned** (`schema_version`), because readers outlive producers.
- **The document is self-contained.** Attribution, licence and provenance travel inside it.
  A consumer that renders the geometry somewhere else cannot strip the ODbL obligation by
  accident, because it never had to fetch it separately.
- **Two runs of the same route produce byte-identical JSON.** A diff means the data moved.

## What is in it

The contract is `pipeline/schemas/route-document.schema.json`; this is the reasoning.

| block | holds | why it is shaped that way |
|---|---|---|
| `shape` | `loop` \| `destination` \| `circular` \| `linear` | Since 1.2 (2026-08-21). What shape of outing this is, **and how that is known**: `loop`/`destination` are constructed — the generator drew them that way; `circular`/`linear` are measured on a mapped route (`pipeline/export/shape.py`: the mapper's `roundtrip` tag wins, then the merged-endpoint gap at the calibrated ratio ≤ 0.01 of length; a route in pieces is `linear` unless tagged — calling a linear route a loop strands a walker, the reverse merely under-sells). The pairs stay distinct so a classifier bug can never impersonate generation intent |
| `identity` | name, `ref` (CAI sentiero number), activity, network scope, waymark, from/to, operator, regions, source id | The `ref` and the painted waymark are how a walker actually recognises a route on the ground |
| `geometry` | GeoJSON LineString or MultiLineString, WGS84 | **The map.** A MultiLineString is not an error — it is a route this network holds in pieces, and `continuity` says so |
| `bbox` | `[minx, miny, maxx, maxy]` | So a reader can index and cull without parsing the geometry |
| `measures` | distance, ascent, descent, lowest, highest | **No duration.** See below |
| `profile` | parallel `distance_m[]` / `elevation_m[]` along the route | The altitude profile `metadata-rules.md` requires — the elevation panel exists to draw this |
| `surface` | length-weighted distribution, plus a dominant | "62% unpaved" is a fact; "unpaved" alone is a claim |
| `difficulty` | SAC grade, the distribution, **and the rule that produced it** | The rule ships with the number so nobody has to guess how it was derived |
| `continuity` | pieces, continuous | A route in nine pieces is honest about it rather than drawn as one line across the holes |
| `start` | vertex, names, anchor count, car-free | From `curated.place`. `names` is often empty, and that is a known gap, not a bug |
| `places` | what the route passes, each with `offset_m` and `distance_along_m` | Computed **here**, against the merged line — see below |
| `quality` | warnings, matched fraction, edges without a profile | Carried, never filtered on |
| `provenance` | run id, producer, sources with licence and attribution | Every row in PostGIS carries its `run_id`; the document carries it out |

### Three rules carried in, not reinvented

**Difficulty is the hardest grade covering ≥ 5% of the length**, never the max — 30 m of
scramble must not label a 20 km valley walk alpine. Same rule as
`backend/graph/graphhopper.py::_weighted_max`, which proved it.

**Absent is not zero.** A route with any unprofiled edge reports `ascent_m: null`, not a
partial sum. Same principle as the semantic-search endpoint returning 503 rather than an
empty list.

**Places are positioned at assembly.** `metadata-rules.md` specifies `ST_LineLocatePoint`
against the *merged* line, which does not exist until the route does. This is why
`curated.place` snaps to a vertex and there is deliberately no precomputed place-to-edge
table: that would have answered "what does this route pass" with a radius nobody chose.
The one bound here is **100 m**, which is where `qa.distance_band` already puts "near"
(measured: median 7 places per route, p90 34; against 12 and 59 at 250 m). Every place
carries `offset_m`, so a reader wanting 30 m filters on it.

### Duration is deliberately absent

DIN 33466 rates the classic Grigna ascent at 10 hours where guidebooks say 6–8. The figure
this codebase can compute today is one a user would not trust, and a catalogue reading
"15 h" for a day walk discredits everything beside it. An absent field invites the
calibration; a wrong one ships. Calibrating it is a tracked next step.

## Emitting

```bash
cd pipeline
uv run python -m export.route_documents --limit 5    # a handful, to look at
uv run python -m export.route_documents              # all of them
```

Writes `review/routes/<id>.json` per route, plus `review/routes/routes.geojson` — one
FeatureCollection of every route with the headline properties, for dropping straight onto
a map without opening 752 files. The geometry in the collection is the same object the
document carries, not a second rendering of it.

**Today the routes are the 752 OSM route relations**, because those are the routes that
exist. `pipeline/draw/` will generate its own and emits through the same module: a
generated route is a different `kind`, not a different document.

## The social layer

Photos, comments and likes attach to a route document by `id`. That design is
`docs/social-layer.md`; nothing is built yet.
