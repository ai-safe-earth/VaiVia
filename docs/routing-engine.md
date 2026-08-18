# Decision: routing engine vs custom pathfinding

Evaluated 2026-08-17 on the Lecco region. **This records an evaluation, not a
migration.** Nothing has been switched over.

## The question

We built custom routing on Neo4j: GDS Dijkstra, comfort weighting
(`core/comfort.py`), and a seed-and-stitch loop generator
(`scripts/spike_loop_routes.py`). The product needs routes that start where you
can park and either return to that point or end somewhere worth reaching. Before
building more of that by hand, is an off-the-shelf engine better?

## What was run

GraphHopper 12 (Apache 2.0) in Docker, fed the **same** walkable-highway extract
of the same Lecco bbox that our own ingestion uses, so the input data is
identical. Same start (45.856, 9.393 — the waterfront the map opens on), same
targets, same metrics, 10 candidates per target. Import took ~55 s.

Reproduce: `scratchpad/gh/` holds `config.yml`, the Overpass query and `eval.py`.

## Results

| | ours (comfort-tuned) | GraphHopper (**stock** foot profile) |
|---|---|---|
| Routed, 10 km | 10/10 | 10/10 |
| Routed, 15 / 20 km | 8/10, 8/10 | 9/10, 9/10 |
| On target (±25%), 10 / 15 / 20 km | 8, 8, 7 | 7, 3, 4 |
| Retrace, best loop | ~20% | **0.8 – 6.2%** |
| Off-road share (mean) | **61 – 64%** | 38.7 – 42.5% |
| Climb reported | **none — 0 m everywhere** | 262 – 2581 m |

**The off-road and on-target rows are not a fair comparison and should not be
read as GraphHopper losing.** Ours runs a tuned comfort model; GraphHopper ran
the *stock* `foot.json` with no customisation at all, because GH 12 changed the
profile schema (`vehicle:` is gone) and the custom model was dropped to unblock
the run. Its `custom_model` system is a superset of `core/comfort.py`, and our
calibration transfers directly into it. Length targeting is likewise tunable —
`round_trip.distance` is approximate by design, and `heading` plus more seeds
narrow it.

**The retrace and climb rows are the real result**, because they are not tuning:

- **Retrace 0.8–6.2% against our ~20%.** Their round-trip algorithm produces
  genuinely circular loops; our three-leg triangle doubles back far more. This
  is the difference between "a loop" and "an out-and-back with a bulge".
- **Climb at all.** We report 0 m everywhere because elevation was never
  ingested (fragility #6). GraphHopper enriches every edge from CGIAR SRTM with
  one config line, and returns `ascend`/`descend` per route plus per-point
  elevation. This blocks difficulty, effort ranking, and "800 m of climbing"
  today, and it is free there.

## Things found that change the picture

- **`hike_rating` and `mtb_rating` are native encoded values.** GraphHopper
  decodes OSM `sac_scale` and `mtb:scale` and can return them per route segment,
  and route *on* them. That is the difficulty data identified in
  `docs/licensing.md` as the Trailforks replacement, available without us
  writing any of it.
- **`average_slope` / `max_slope`** are routable, so "avoid steep pitches" is
  expressible. We cannot do this at all.
- **Fragmentation is handled natively.** The importer found 13,778 subnetworks
  and pruned them to 3 components automatically. That is fragility #9, which
  cost us a debugging session, solved as a matter of course.
- **`osm_way_id` is NOT available as a path detail.** This is the one real
  integration cost: the returned geometry cannot be joined back to `(:Segment)`
  by id. `surface`, `road_class`, `hike_rating`, `mtb_rating` and
  `average_slope` all are.

## Recommendation

**Hybrid: GraphHopper computes geometry, Neo4j holds meaning.** Not yet acted on.

The split is clean because they are good at different things. GraphHopper cannot
answer "a panoramic ridge past a hut that is fine in autumn without ice" — that
is a knowledge-graph question, and the semantic layer (trails, POIs,
`NEAR_POI`/`PASSES_BY`, seasonal hazards, embeddings, the LLM-intent boundary)
stays exactly as it is. Pathfinding is table stakes we are currently
reimplementing; the differentiator is the conversation over the graph.

On the missing `osm_way_id`: the join should be **spatial, not by id** — take
the returned polyline and ask Neo4j what POIs and trails lie near it. That is
the `PASSES_BY`/`NEAR_POI` logic generalised from a segment to a line, it reuses
the existing point indexes, and it answers the question we actually care about
("what does this route pass") rather than the one an id join answers ("which
exact ways").

### What would be kept, and what deleted

Kept: everything semantic, plus the comfort *calibration* (path 1.0,
residential 2.4, secondary 4.5, and specifically **do not penalise untagged
surface**), which becomes a GraphHopper custom model.

Deleted on migration: `route_gds_dijkstra`, `graph_project_routing`,
`graph_drop_routing`, `route_edge_details`, `intersections_in_ring`,
`core/comfort.py`'s plumbing, `spike_loop_routes.py`, and the `cost_m` property.
`check_graph_connectivity.py` stays useful for the semantic graph.

### Honest costs

- A sixth deployed service (JVM, ~1 GB heap for one region, plus graph cache and
  an SRTM cache on disk).
- Two sources of geometric truth, needing a stated rule about which wins.
- An import step to run per region, coupled to OSM extract management.
- Re-doing the comfort tuning in their custom model, including re-verifying that
  the off-road share matches or beats our 61–64%.

## Gate result — PASSED, 2026-08-18

Step 1 of the sequence below was run: `core/comfort.py` ported into a
GraphHopper `custom_model` (priority = 1/penalty, since their priority multiplies
desirability where our cost multiplies distance), same extract, same start, same
seeds.

| metric | ours (tuned) | GraphHopper (**tuned**) | gate |
|---|---|---|---|
| Off-road, 10 km | 61.0% | 53.8% | **miss** |
| Off-road, 15 km | 61.0% | **67.0%** | pass |
| Off-road, 20 km | 64.1% | **67.7%** | pass |
| Retrace, best loop | ~20% | **0.0 – 3.2%** | pass (<10%) |
| Routed | 10/10, 8/10, 8/10 | **10/10, 10/10, 10/10** | better |
| On target (±25%) | 8, 8, 7 | 6, 3, 7 | worse |
| Climb | none | 296 – 2,732 m | n/a for us |

**Verdict: adopt.** Off-road matches or beats ours at the distances that matter,
retrace is roughly six times better, every candidate routes, and elevation comes
free. The best 20 km loop is now path 5.9 km / footway 4.3 km / cycleway 2.5 km
against residential 2.0 km — a real trail loop.

Two honest caveats:

- **10 km misses (53.8% vs 61%).** A 10 km loop from a town waterfront draws its
  waypoints from a ~1.6 km radius, and there is not much trail that close to
  Lecco. Our ring-based generator forces waypoints outward at a fixed radius,
  which pushes it onto trails; `round_trip` is freer and takes the urban
  footways. Tunable by starting short loops from trailheads with a high
  `off_road_share` rather than from the town centre — which is exactly what
  `(:Trailhead)` now makes possible.
- **Length targeting is worse (3/10 on target at 15 km).** `round_trip.distance`
  is approximate by design. This matters far less than it looks in the offline
  pipeline: the design is generate-many-keep-few, so a spread of lengths that
  all route successfully is better raw material than fewer candidates that hit
  the target more often. Filtering is free offline.

### Suggested sequence if adopted

1. Port the comfort model to a GraphHopper `custom_model` and re-run `eval.py`;
   the gate is off-road share ≥ our 61–64% while retrace stays under 10%.
2. ~~Prove the spatial map-back: polyline → Neo4j POIs/trails passed.~~
   **DONE 2026-08-18** — `graph/route_context.py`. Against a real 13.89 km
   generated loop it returned 19 POIs within 150 m: three named saddles at
   0.0 m (the route crosses them), Cima di Ferrera at 3.0 m, plus lakes,
   parking and a chapel — one carrying Wikipedia prose and its CC-BY-SA
   attribution. The missing `osm_way_id` is not a real cost.

   It surfaced a genuine bug on the way. `core/geo.min_distance_to_polyline_m`
   measures to *vertices*, not perpendicular — fine for OSM ingestion, where
   polylines are vertex-dense, but wrong for engine output, where a straight
   kilometre can be two points. It reported a POI 7.8 m off the line as 556 m
   away, i.e. the map-back would have silently lost most of what a route
   passes. `distance_to_polyline_m` (projected, clamped to segment ends) is the
   correct primitive. The vertex-based one is left alone: changing it would
   alter every `PASSES_BY` edge and needs its own verification.
3. Move `/routes` behind an interface with both implementations, compare live.
4. Delete the custom routing once the engine is proven.

Do not start until step 1's gate is met — if the custom model cannot beat our
off-road share, the case is much weaker and worth re-opening.
