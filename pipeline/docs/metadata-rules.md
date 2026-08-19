# Metadata rules: how attributes survive splitting and joining

The two operations that silently corrupt a route map are cutting ways into pieces and
merging pieces into routes. Neither has a safe default: "copy everything" on split
produces reversed inclines, and "max" on join lets 30 m of scramble label a 20 km valley
walk alpine. So every attribute is classified **once, here**, and the code in
`topology/` and `curate/aggregate.py` implements this table rather than its own judgement.
A new attribute must be added to a class before it may be loaded.

## On split (a way cut at intersections)

| class | attributes | rule |
|---|---|---|
| **Inherited** | `highway`, `surface`, `sac_scale`, `mtb:scale`, `trail_visibility`, `tracktype`, `width`, `access`, `foot`, `bicycle`, `name`, `ref`, `operator` | copied verbatim — they describe the whole way |
| **Recomputed** | `length_m`, ascent/descent, bearing, midpoint, bbox | derived from the piece's own geometry, never divided arithmetically |
| **Directional** | `oneway`, `incline` | carried *with orientation*: reversing a piece inverts them (`oneway=yes` ↔ `oneway=-1`; `incline=up` ↔ `incline=down`; `incline=12%` ↔ `incline=-12%`) |
| **Positional** | route-relation membership + ordering index | recomputed per piece — a way may join a relation midway |
| **Provenance** | `osm_way_id`, `piece_index`, `run_id`, source | stamped, so every curated row traces back |

Directional is the class that corrupts silently: a reversed piece with an uninverted
`incline` is a plausible gradient pointing the wrong way, and no count or total will ever
reveal it. Unit tests pin the inversion.

## On join (pieces merged into one route line)

| attribute | rule |
|---|---|
| `length_m` | from the merged geometry (`ST_Length` in EPSG:32632), cross-checked against the sum of pieces; mismatch beyond tolerance = gap or double-counted overlap → `qa.finding`, not a stored route |
| ascent / descent | from the **altitude profile**, never summed per piece — per-piece sums double-count at joins |
| `surface` | length-weighted **distribution** kept whole, plus a dominant value |
| `sac_scale`, `mtb:scale`, `trail_visibility` | hardest value covering **≥ 5% of the length**, not the max (rule proven in `backend/graph/graphhopper.py::_weighted_max`) |
| access / legality | **conjunction**: one forbidding piece forbids the route |
| `name` / `ref` | ordered distinct values with their length share — "follows Sentiero 33 for 4.1 km, then 31" |
| land cover | length-weighted class shares |
| POIs | union, each positioned along the merged line (`ST_LineLocatePoint`): `(poi_id, distance_along_m, offset_m, side)` — an *ordered* section |
| provenance | the set of contributing `osm_way_id`s, kept |

Assembly itself is a check: the ordered pieces must `ST_LineMerge` to **exactly one
LineString**. A MultiLineString means a gap, which is filed as a `qa.finding` pointing at
the break — a broken route is never stored with a straight line across the hole.

## Route-relation membership (`curated.edge_route`, written 2026-08-20)

The **Positional** row of the split table, realised. A member of an OSM route relation is
a way id, and `curated.edge.way_id` is the same id, so the join needed no matching
algorithm — it already existed in the data and had never been written.

| decision | rule | why |
|---|---|---|
| shape | a **link table**, never a column on `edge` | 5,295 edges belong to more than one relation; a column would pick one and discard the rest |
| grain | one row per (edge, relation, member position) | a way may appear **twice in one relation** (140 cases: an out-and-back leg), so (edge, relation) is not a key |
| the relation's tags | **not copied** — `staging.osm_relation` stays the source of truth | a second copy is a second thing to keep in step; the same argument that keeps edge tags in `tags` |
| all pieces of a member | every piece of a member way joins the route | the relation is a claim about the **way**; pieces are an artefact of noding |
| ordering | `member_index` (position in the relation) + `piece_index` (position along the way) | that is the order OSM stated |
| direction | **not resolved here** | a member way can be walked backwards along the route; resolving that is assembly's job, above. Store provenance, derive direction later |
| nested relations | **skipped, and counted** | 16 exist, all superroutes listing their stages. The stages are relations in their own right and join on their own; flattening the parent would make every stage's edges appear twice under two names |
| node members | skipped | 2,639 of them: guideposts and summits, not network |

### What it produced, against the 2026-08-19 network

| | |
|---|---|
| relations joined | **752 of 752** |
| distinct member ways in the network | 10,246 of 15,392 |
| links written | 25,719 |
| edges carrying a route | 17,118 (**2,469.5 km** of 9,238.0) |
| edges with no `name` of their own that now carry a route's | **10,361** |
| routes that merge into a single line | 621 of 752 |

The 5,146 member ways the network does not hold are outside both region bboxes or were
excluded by `load/legality.py` — expected, and the reason `qa.v_route_coverage` exists:
`matched_fraction` near 1 is a route clipped at the edge of coverage, near 0 is a route
this network cannot hold. Twenty-seven sit below 0.2, led by BI-12 (Trieste–Savona) at
0.003. **A route generator must filter on that number**; a "route" of two matched ways out
of 646 is a fragment with a famous name.

### The link describes ONE build of the network

`edge_route` holds `edge_id`s, so it is true only of the network that produced them.
`build_network` (which replaces the network) and `topology/repair` (which splits and
deletes edges) both **clear** the table and say so; `curate.routes --check` reports
staleness by comparing the network run ids recorded in `build_run` against the run ids now
in `curated.edge`. An empty table is visibly missing, a partly-stale one lies — which is
`curated.vertex_degree`'s lesson, applied before it could be repeated.

## Where an operation runs

If it is naturally **one SQL statement over a table** — noding, snapping, line-merging,
`ST_LineLocatePoint`, raster sampling at scale — it is PostGIS. If it needs **branching
per feature, a unit test, or a plot** — dangle classification, tolerance choice from a
near-miss histogram, profile summarising — it is Python via GeoPandas/Shapely. Nothing is
implemented by hand that either layer already provides: the vertex-distance and
bounding-box workarounds in `backend/core/geo.py` and `backend/graph/route_context.py`
exist only because neither PostGIS nor Shapely was available there, and they are exactly
what this stack retires.

## QA loop

Detectors write `qa.finding` (one rule = one QGIS layer, geometry per finding). Repairs
are automated per rule, write `qa.fix` with before/after geometry, honour `--dry-run`, and
take tolerances from measured distributions (the near-miss histogram), never from guesses.

### Measured, not guessed: the 2 m tolerance

`topology/histogram.py` plots, for every loose end, the distance to the nearest edge it is
not joined to. Over the two provinces: **14,769 loose ends**, and the distribution *peaks
at 6–8 m*, decaying to 100 m. That shape is the answer — the bulk are **real dead ends**
(spurs, driveways, paths stopping near a road), not defects. Only the small population
below ~2 m sits before the rise, where "near another line" is not explainable as a genuine
ending.

So the tolerance is **2 m**, catching 231 of 14,769 (1.6%). At 5 m it would touch 1,180
and at 10 m 3,036 — both deep inside the real-dead-end population, welding junctions that
do not exist. Re-run the histogram after any change to the network before moving it.

### What the 231 actually are — reconciled 2026-08-19

The histogram counts loose ends with *something* within the tolerance. That is not the
same as counting gaps, and the difference is most of them:

| | loose ends |
|---|---|
| within 2 m of an edge they are not joined to | **231** |
| ...whose own edge ends at a junction under 2 m away — a **stub**, not a gap | 129 |
| ...genuinely near something they should meet | 102 |

A stub is a short edge hanging off a junction. Its far end is a dangle, and the *other*
edges at that junction are, trivially, within 2 m of it — so it registers as a near miss
against its own neighbourhood. Nothing is broken there.

Reconciling the remaining 102 against the detectors is what found the missing rule. The
pair rule needs both ends to be dangles; the edge rule excludes anything within tolerance
of an edge's start or end, to avoid double-reporting the pair case. Between them they
could not see **a loose end stopping just short of an existing junction** — the third
class, now `gap_dangle_junction`. Fifteen of them, invisible until the arithmetic was
made to add up.

The lesson is the reconciliation itself: a detector suite is only trustworthy once the
measure and the rules are shown to account for the same population.

### Repairs: what each rule does, and two ways to get it wrong

`topology/repair.py` consumes the **findings of the latest QA run**, not a fresh scan, so
what was judged in QGIS is exactly what is changed. Every change writes `qa.fix` with
before/after geometry (`qa.v_fix` is the layer), and `--dry-run` writes nothing.

| rule | repair |
|---|---|
| `gap_dangle_pair` | weld the two ends; the lower `vertex_id` stays put |
| `gap_dangle_junction` | weld the end onto the junction; the junction never moves |
| `gap_dangle_edge` | split the target edge at the loose end, which becomes the shared vertex |
| `degenerate` | self-loop → **split** at the midpoint; sub-metre edge → **collapse** |
| `island`, `overlap` | not repaired: one is a coverage fact, the other needs judgement per case |

Both halves of the degenerate rule were wrong on the first run, and both failures are the
same mistake — treating a defect in the ROUTING GRAPH as a defect in the GROUND:

- **A self-loop is real.** A loop trail or a roundabout mapped as one closed way has
  `source = target`, which no shortest path can enter. Deleting them removed **26.3 km of
  network**, the longest a 640 m loop way. They are split at the midpoint instead.
- **A sub-metre edge carries no ground but a real connection.** Deleting 245 of them
  severed the joins they carried and created **129 new loose ends**. They are collapsed —
  weld the two ends, and the edge disappears without disconnecting its neighbours.

Only a zero-length ring is deleted, and it connects nothing to nothing by construction.

A weld can collapse an edge that was not in the pass's first snapshot, so the degenerate
rule re-selects until a pass changes nothing. A split refuses to cut closer than a metre
from an end, because doing so manufactures the very degenerate edge it would then have to
clean up.

### The 2026-08-19 pass, in numbers

Against the two provinces, on 101,870 edges and 9,238 km:

| rule | before | after |
|---|---|---|
| `gap_dangle_pair` | 9 | 0 |
| `gap_dangle_edge` | 92 | 0 |
| `gap_dangle_junction` | 15 | 0 |
| `degenerate` | 488 | 0 |
| `island` | 389 | 370 |
| `overlap` | 128 (972.6 m) | 164 (1,168.5 m) |

Loose ends 14,769 → 14,586; components 407 → 387; **total length unchanged at 9,238.0 km**,
which is the check that matters — a repair pass that changes the length of the network has
either invented ground or thrown some away.

Overlap is the one number that moved the wrong way: 36 more findings and 196 m more shared
geometry. Welding two near-duplicate ways together makes the duplication measurable where
before it was two lines with a gap between them. Not repaired automatically, so it sits in
`qa.v_overlap` for judgement.

### A matview that lied

`curated.vertex_degree` is materialised, every detector reads it, and 0004 said
`build_network.py` refreshed it after a rebuild. It did not. A rebuilt network was
therefore measured with the *previous* network's degrees: the same 101,870 edges reported
9 dangle pairs before a rebuild and 19 after, with nothing changed in between. Repairs
chosen from those numbers would weld vertices picked off a graph that no longer existed.

The refresh now happens where the comment always claimed it did, and `topology/qa.py`
refuses to run when the matview's row count does not match `curated.vertex` — the cheapest
possible detection of a class of error that is otherwise silent.

### Two PostGIS traps found building these detectors

Both read naturally and are quietly wrong — the same family as the per-node radius that
cost 14 s a route in the old graph.

- **`ST_Dimension` of an empty geometry returns the dimension of its TYPE.**
  `ST_Dimension(LINESTRING EMPTY)` is `1`, not `-1`, so testing `= 1` to find line-on-line
  overlaps matched every bbox-overlapping pair that does not intersect at all: **51,905
  phantoms against 146 real overlaps**. Measure the shared *length* instead, which is also
  the honest measure of the defect.
- **A CTE has no indexes.** `WITH dangles AS (...) FROM dangles a JOIN dangles b ON
  ST_DWithin(...)` degrades to a nested loop over every pair — 14,769² geography distances,
  killed after ten minutes. Join the indexed relation directly and filter on `degree`
  inside the join: same result in 21 s. Related: a `::geography` predicate needs a
  `gist((geom::geography))` index; a plain geometry index cannot serve it.
