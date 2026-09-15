# On-demand routes — the Pack Planner

Ratified 2026-09-02. This is the second product feature: a route drawn for one person
from what they said, over the same cleaned network the catalogue was drawn from, at the
moment they ask. It keeps every doctrine the project already has and retires the parts
that only existed to make a pre-generated catalogue answerable.

## The asks it has to answer

| | Ask | What it needs that the catalogue could not give |
|---|---|---|
| A | A bike loop for the kids, no asphalt, some interesting stops, ~3 h, no more than an hour's drive from here | party, surface exclusion, hours→distance, drive time from a point |
| B | A 2 h out, 2 h back trail in nature ending at a lake or river to bathe, not far | strict out-and-back, a *role* for the destination (bathe), setting |
| C | A one-hour cultural walk from the station | urban theme, a station as start, town setting allowed |
| D | ~~Three days on the Orobie sleeping in rifugi, challenging for my level~~ *(dropped 2026-09-12: VaiVia plans single-day outings; the ask is understood and refused honestly)* | multi-day chain over sleep places, fitness scaling, a named area |
| E | Seven days by bike around Tuscany sleeping in agriturismi | the same, plus an area we do not cover — which must be said, not approximated |

The catalogue answers "which of these 627 fits" and nothing in A–E is in the 627. A catalogue
big enough to contain them would be a catalogue of every route, which is the network itself.

## The paradigm

**The network is the catalogue.** The pipeline stops at the cleaned network, its places and
its starts, and exports that as one binary **pack**. The backend loads the pack into memory
and draws at ask time, in-process, with the same assembly rules that drew the 627 — so a
generated route and an on-demand route are the same kind of thing, minted by the same
`ids.route_id`, validated by the same schema, rendered by the same card.

Nothing about the LLM boundary moves. The model still emits a validated pydantic intent
and nothing else; Python compiles the intent into geometric constraints; the planner runs
them; the answer is built from counts, not from prose.

```
  PostGIS ──export──> packs/<run_id>/network.npz + manifest.json      (pipeline, per build)
                              │
                              ▼  mmap at startup
  backend  ── /chat ──> OutingIntent ──compile──> constraints ──plan──> 3 documents ──> cards
                                                     │                      │
                                             Neo4j: places, names,    favourite/share:
                                             favourites, map-back     write document + (:Route)
```

### Why not route inside Neo4j

Measured on the local stack, 2026-09-01 (84,137 intersections, 200,565 `CONNECTS_TO`,
GDS 2.13.12):

| Operation | Time |
|---|---|
| Full GDS projection (undirected, 401k rels) | 317 ms, 92 MiB |
| A→B `shortestPath.dijkstra`, 7 km | 22.6 ms |
| Yen's k=5, same pair | 187 ms |
| `allShortestPaths.dijkstra.stream`, one source, filter ≤ 8 km | 6.5 s (81k rows through Cypher; no cost cutoff) |
| `allShortestPaths.delta.stream` | timed out (> 10 s) |
| Spatially pre-filtered local projection + bounded SSSP | 1–3 s + 0.77 s |
| pgRouting `pgr_drivingDistance`, one start | 5.8 s (graph rebuilt per call) |

Point-to-point in the graph is fine. Every loop, out-and-back and destination route needs
a **bounded distance field** from the start — "everything within N km" — and GDS has no
cost cutoff, so the cheapest way to get one is to stream the whole graph or pre-project a
subgraph per request. That is the operation A–E run dozens of times per ask. scipy's
`dijkstra(limit=)` over a CSR of the same graph does it in tens of milliseconds and holds
the whole network in ~35 MB. So the graph keeps meaning (places, names, favourites,
map-back) and gives up geometry.

Full measurement in the session record; the numbers above are the ones the design leans on.

## The pack

`pipeline/export/pack.py` writes `packs/<run_id>/network.npz` + `manifest.json`. One
export per pipeline build; the backend loads the `run_id` the manifest names and every
route it draws carries that `run_id` in its provenance. The format — every array, its
dtype, its length symbol and the invariants a pack must hold to load — is
`shared/routes/vaivia_routes/pack.py` (`SPEC`, `validate`); this table is the reading
copy. Slice 1 exported the whole store in 10 s: 80,113 vertices, 101,951 edges, 761,048
points, 17,697 places (10,489 starts). Edges routable by neither activity, zero-length
edges and self-loops stay in PostGIS.

| Array | Shape / dtype | Contents |
|---|---|---|
| `vertex_id` | i64 [V] | `source_map.vertex.vertex_id`, the key Neo4j `:Place`/`:Start` and QGIS use |
| `vertex_lon`, `vertex_lat` | f64 [V] | vertex positions |
| `vertex_component` | i64 [V] | connected component (the main one is what starts must sit on) |
| `edge_u`, `edge_v` | i32 [E] | endpoints, indices into the vertex table |
| `edge_length_m`, `edge_ascent_m`, `edge_descent_m` | f32 [E] | forward direction; reverse swaps ascent/descent; NaN unknown |
| `edge_cost_foot`, `edge_cost_foot_rev`, `edge_cost_bike`, `edge_cost_bike_rev` | f32 [E] | cost per direction, −1 = forbidden (oneway and access folded in here, straight from `catalogue.v_edges_foot/bike`, so pack and factory route the same arcs) |
| `edge_surface`, `edge_highway`, `edge_sac_scale`, `edge_mtb_scale` | i16 [E] | codes into the manifest's tables, −1 NULL; the raw tag values, so a rank is the planner's reading, not the pack's |
| `edge_routable_bike` | bool [E] | the mtb-legality flag the assembly rules read |
| `edge_urban_m` | f32 [E] | metres of the edge inside an urban area |
| `edge_id` | i64 [E] | `source_map.edge.edge_id`, for QGIS and for the id mint |
| `geom_offsets`, `geom_lon`, `geom_lat` | i64 [E+1], f64 [P] | ragged edge geometry, u → v |
| `geom_ele` | f32 [P] | elevation per point — `profile_m` is one sample per point of `geom` — NaN where the DEM had no sample (f16 would cost 1–2 m per point above 1,000 m for 1.8 MB saved) |
| `place_*` | [K] | every `source_map.place` row whose vertex is in the pack: vertex, lon/lat, source, kind, name, source_id, ele, distance to its vertex, `is_start`, `start_class`, `n_trips`, GTFS service window as days since the epoch. Starts are the `is_start` mask — `qa.v_start`'s aggregates and `terminals.seasons_block`'s inputs are both derivable from these rows, so there is no separate start table |
| `start_trail_share_5km` | f32 [K] | slice 5: ranks a settlement's starts by how much unpaved network they open |
| `potential_<kind>` | f16 [V] per kind | network distance from every vertex to the nearest place of that kind; makes "end at a lake" a lookup, not a search |
| `drive_min` | f16 [settlements × S] | minutes by car from each settlement to each start, pgRouting over OSM car roads in the same PostGIS (decision 3) |
| `rail_min` | f16 [stations × stations] | GTFS |
| `gazetteer` | manifest | named areas → polygon + `covered: bool` |
| `provenance` | manifest | run_id, OSM extract date, licence block, code tables |

Size: ~35 MB on disk, read whole (not mmap'd: the arrays are small enough that a fork
shares them copy-on-write, and validation touches every one anyway), +250 MB resident in
the backend once CSRs are built. The
potential fields and both matrices are slice 5; slices 1–4 ship without them and answer
"end at a lake" by search and "an hour from here" by crow-fly with the substitution said
out loud (the posture `handoff.md` already records for travel time).

## Ask time

1. **Intent.** `extract_plan` emits an `OutingIntent` (below) or `Clarify`. `here` is a
   typed `near: {lat, lon}` on `ChatRequest`, validated to the coverage bbox, attached
   after extraction; a test asserts the LLM mock never sees it.
2. **Compile** (`backend/chat/compile.py`, pure Python, unit-tested): hours × activity ×
   party → distance band ±20 % and an ascent cap from a stated pace table
   (`core/durations.py`, labelled *our estimate*); party → sac/mtb caps; setting → urban
   share cap or floor; surface exclusions → cost ×4 and a 10 % share cap; waypoint roles
   → place-kind sets; `start.mode` → candidate start vertices (`here` → settlement → drive
   matrix ≤ `max_drive_min`, ranked by `trail_share_5km`; `station` → rail; `named` →
   fulltext in Neo4j); `area` → gazetteer, and `covered: false` is a Python `Clarify` that
   names what *is* covered (E).
3. **Plan** (`vaivia_routes`, shared package): per candidate start, one bounded Dijkstra
   gives the distance field; loops use the catalogue's rule (s→v1→v2→s, walked edges ×3),
   out-and-back is strict retrace, destination picks the alt return. (Multi-day
   chaining was dropped 2026-09-12 — a days>1 ask gets an honest refusal.) `assemble` → facts → **reject by stated limits, counting
   each rejection** → one rung of relaxation → `keep_distinct` → 3.
4. **Answer.** Three cards, an assumptions strip ("we read ~3 h as 12–18 km for kids on
   bikes"), refinement chips. Chips send a typed delta straight to the composer — no model
   call, 0.4–0.8 s. If nothing survives, the Clarify is built from the counts: "42 loops in
   reach, all had more than 10 % asphalt; allow some, or start further out?"
5. **Documents** are built in-process from the same `build_document`, `ids.route_id` minted
   last, cached per conversation as ordinals (`refine_from: 2`). Nothing is written until
   the user favourites or shares, then the document goes to the store and one `(:Route)`
   to Neo4j — exactly as a catalogue route would.

Latency, measured on the dev machine over the full pack (slice 2, 2026-09-11; VPS
confirmation still owed): pack load + validate 210 ms and `Network.build` ~140 ms per
activity, both once at startup; bounded field (8 km limit) 3 ms median; point-to-point
26 ms, 30 ms with the walked-edge penalty (the reduction is patched per affected pair,
not rebuilt); a 10 km three-leg loop draw 165 ms median / 225 ms p90; an out-and-back
with alternative return 213 ms. That keeps turn 1 (LLM + plan) at 3–5 s, chip refine
0.4–0.8 s. Planning runs in a 2-worker `ProcessPool` (scipy holds
the GIL) on the 4-vCPU box. The engine reproduces all 627 catalogue ids from the pack
(`shared/routes/tests/test_parity.py`); the one divergence class found and fixed on the
way was parallel-arc reduction before the penalty — the pack is a multigraph, and the
reduction must happen after costs are adjusted.

## OutingIntent

Replaces `LoopSearchIntent`. `Trail`, `Route`, `Semantic` and `Clarify` stay.

```python
class Waypoint(BaseModel):
    kind: PoiKind | None          # lake, river_access, hut, church, museum, ...
    name: str | None
    role: Literal["pass", "end", "bathe", "eat", "sleep"]

class StartSpec(BaseModel):
    mode: Literal["here", "named", "station", "parking", "any"]
    name: str | None
    max_drive_min: int | None
    car_free: bool = False

class OutingIntent(BaseModel):
    kind: Literal["outing"]
    activity: Literal["hike", "walk", "mtb", "bike"]
    shape: Literal["loop", "out_and_back", "destination", "traverse"] | None
    party: Literal["solo", "adults", "kids", "small_kids"] | None
    fitness: Literal["easy", "moderate", "challenging", "expert"] | None
    days: int = 1
    min_hours: float | None
    max_hours: float | None
    max_distance_km: float | None
    max_ascent_m: int | None
    waypoints: list[Waypoint]
    surface_exclusions: list[Literal["asphalt", "paved", "gravel"]]
    setting: Literal["nature", "mixed", "town"] | None
    theme: Literal["cultural", "panoramic", "water", "forest"] | None
    start: StartSpec
    sleep: Literal["hut", "campsite", "agriturismo", "wild", "any"] | None
    area: str | None
    refine_from: int | None       # ordinal of a card in this conversation
```

No field carries a query, a template name, a database id, a coordinate or a weight. The
compile step owns every number the planner sees. POI kinds to add for this: `church`,
`museum`, `monument`, `agriturismo`, `river_access`, and the lodging kinds.

## Decisions ratified 2026-09-02

1. **No human review before a user sees a route.** The catalogue's QGIS pass is replaced
   by invariants in the planner (`assert_connected`, no `mini_loop`, no `spur`, retrace
   share, urban share — the Phase 10 F1/F2 rules as pure functions), a parity test that
   the pack engine reproduces all 627 catalogue ids from the same network, a fixture pack
   cut from a 3 × 3 km tile, and a "report this route" action on the card.
2. **Cards show an estimated duration**, labelled *our estimate*. The route-document schema
   still refuses the field (`route-document.schema.json`, the duration rationale stands):
   it is a presentation number from `core/durations.py`, not a fact about the route.
3. **Drive time is pgRouting over OSM car roads in the existing PostGIS**, computed at
   pack-export time into `drive_min`. No external routing service.
4. **GDS is retired.** A→B routes run on the pack too, so there is one geometric truth. The
   `route_gds_dijkstra` / `graph_project_routing` / `graph_drop_routing` templates and the
   raw `Intersection`/`CONNECTS_TO` load go with it (slice 7).
5. **A shared package** `shared/routes/` (import name `vaivia_routes`) holds `assemble`,
   `loops`, `destinations`, `document` and `ids`, as an editable path dependency of both
   uv projects (`[tool.uv.sources]`). Its tests run in the pipeline CI job. `pipeline/draw`
   and `pipeline/ids.py` become re-exports until slice 7 removes them.
6. **Neo4j heap max 2 g → 1 g**, transaction-log retention 2 days (the log had reached
   1.1 GB against a 176 MB store). The gigabyte goes to the planner.

## What stays, what goes

**Stays** — the LLM boundary and `Clarify` poisoning; the golden eval (`expect_loops` ids
become `expect_facts` bands pinned to a pack `run_id`); schema 2.1 and `ids.route_id` as
the only mint; the pipeline up to the pack; Neo4j for `:Place`, `:Start`, `:Route`,
`:Trail`, `poi_by_name_fulltext`, `pois_near_points`, `routes_by_ids`, `route_card`,
favourites; gateway, SSE, Supabase quotas, the frontend cards.

**Goes as product, stays as test corpus** — `catalogue.route` / `route_edge`, the Neo4j
catalogue load, `search_loops` / `estimate_loops` / `loop_candidates` /
`loop_poi_conjunction`, `LoopSearchIntent`, `catalogue_view`. `draw/generate.py` and the
627 ids are the parity test's oracle, nothing else.

**Superseded in the plan** — Phase 7 P7 job queue (on-demand is synchronous, there is
nothing to queue); Phase 10's recipe registry, labels and F0/F3/F4 as *catalogue
generation* (F1/F2 invariants and F5 *sentiero* survive as planner rules and a theme);
GraphHopper (`docs/routing-engine.md`); Phase 9 D4 *publish-catalogue* → *publish-pack*;
`core/comfort.py` runtime plumbing (calibration moves into the pack's cost columns).

## Alternatives considered

- **Leg Graph** — hubs (starts, places) + precomputed legs between them, `DRIVE`/`RAIL` as
  edges, routing over legs. Prefer it if coverage passes ~500k edges or leg-level QGIS QA
  is wanted. Weaknesses now: turning only at hubs, parking-dominated hubs, needs a
  bbox-prefiltered `edges_sql` per call.
- **Field & Stitch** — draw in Neo4j/GDS. Refuted by the measurement above.
- **Sampler** — random walks with acceptance. Unproven yield, non-deterministic, no ids.
- **Mosaico** — a leg atlas with character columns. Too much scope for the asks in hand.
- **Agentic tool loop** — the model calls planner tools. Out by default: 8–12 model calls,
  15–40 s, non-deterministic. Documented escape hatch, ordinal-only references, 8-call cap.

## Slices

| | Branch | Days | Lands |
|---|---|---|---|
| 1 | `feat/pack-export` | 2 | `pipeline/export/pack.py`, manifest, fixture pack from a 3 × 3 km cut, schema round-trip test |
| 2 | `feat/pack-engine` | 3 | `shared/routes` (`vaivia_routes`), CSR + bounded SSSP, id-parity test vs 627, latency measured on the VPS |
| 3 | `feat/outing-intent` | 2 | `OutingIntent`, `compile.py`, golden entries, `check_intents_live` 7/7 |
| 4 | `feat/outing-planner` | 3 | `/chat` wiring, demo A–C, infeasibility counts + ladder, ordinals, assumptions strip + chips |
| 5 | `feat/pack-drive-rail` | 1.5 | `curate/drive.py`, GTFS rail matrix, gazetteer, `trail_share_5km`, potential fields |
| 6 | ~~`feat/multi-day`~~ | — | dropped (owner, 2026-09-12): single-day outings only |
| 7 | `chore/retire-catalogue` | 1 | drop templates and loads, publish script → pack, GDS plugin off |

Each slice is independently mergeable; the catalogue keeps serving until slice 7. Decision 6
(heap, tx-log retention) lands with slice 1 — it needs nothing else.
