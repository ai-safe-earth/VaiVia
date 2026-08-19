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
