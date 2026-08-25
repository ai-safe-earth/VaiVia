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
| `start` | vertex, names, anchor count, car-free | From `curated.place`. `names` is often empty, and that is a known gap, not a bug. **There is no matching `end`** — see "The start/end contract" below, which proposes replacing this with `terminals` |
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

## The start/end contract

**Proposed 2026-08-23, pending ratification.** The owner set out the model on
2026-08-22: a route needs a reachable start, and an end that is either the start
again or somewhere worth the effort. That model was right and incomplete —
`curate/anchors.py` classifies starts and nothing in the codebase models an end at
all. A route carries `shape`, `destination_name`, and nothing else about where it
finishes. This section closes that asymmetry.

Every rule below is stated in the form the rest of this document uses: the decision,
then the measurement or the code that forces it. Numbers were measured against the
live store on 2026-08-23 (752 mapped OSM route relations, 228 generated routes,
8,110 start vertices).

### 1. A route has terminals, and every terminal is tested for reachability

The end test and the "worth it" test are **different tests on different objects**,
and conflating them is why ends went unmodelled:

- **Reachability** is a property of a **terminal** — a point at which a walker
  joins or leaves this route from the outside world. It asks *can I get here, and
  get away from here*.
- **Interest** is a property of a **turnaround** — the point you stand at and go
  back from. It asks *is this worth the walk*. It already exists, as
  `draw/destinations.py::INTEREST`.

How many terminals a route has follows from its shape, and the document already
separates the cases:

| shape | terminals | turnaround | why |
|---|---|---|---|
| `loop` / `circular` | 1 | none | you leave from and return to the same point |
| `destination` | 1 | 1, the far end | out and back: one point joins the outside world |
| `linear` | 2 | none | a traverse A–B; you must be able to get to A *and* home from B |

So `start` becomes `terminals`, an array of one or two, and **every entry must pass
the reachability test independently**. The far end of a `linear` traverse is the
case the old rule missed entirely: a route that ends at a road-less col is not a
traverse, it is where our data stopped.

A `destination` route's turnaround is tested for interest and **not** for
reachability — a summit two hours above the nearest road is exactly the point.

**Two tables in the codebase already encode most of this and disagree slightly.**
`anchors.py::DESTINATION_NOT_START` names 13 kinds that are destinations rather
than starts; `destinations.py::INTEREST` weights 12. They agree on all twelve —
peak, viewpoint, hut, waterfall, lake, castle, cave, ruins, beach, chapel, saddle,
spring — and differ only in that `picnic_site` is a destination in the first and
has no weight in the second. One of them must become the source of truth; the
proposal is `INTEREST`, with `DESTINATION_NOT_START` deriving its keys from it, so
a new kind cannot be added to one and forgotten in the other.

### 2. Reachable is not timeless, and gets the season scoping hazards already have

A gated forest road, a winter bus timetable and a snow-closed pass are all
"reachable" in July and not in January. Reachability therefore carries the same
four-season shape as hazards do on `(:Trail)`:
`reachable_spring/summer/autumn/winter`, with a union for display.

**The default inverts, and the inversion is deliberate.** For hazards, an unscoped
record goes into every season, because a hazard we cannot place in time is always
possible. For reachability, an unscoped record is reachable in every season,
because absence of a gate tag is evidence there is no gate — not absence of
evidence. Both defaults are "put it in all four", but they mean opposite things,
and a reader that treats reachability as pessimistic would hide half the network
in winter for no reason.

**One concrete gap this exposes.** `load/gtfs.py` counts rows in `stop_times.txt`
and never opens `calendar.txt` or `calendar_dates.txt`, so `n_trips` is
calendar-blind: the 17 GTFS stops that are starts today claim a year-round service
this pipeline has never checked. Until the calendar is read, a GTFS-derived
terminal is marked `season_unverified` rather than asserted as year-round. That is
17 of 8,110 start vertices, and 45 car-free starts in total — small, but "you can
get here by bus" is precisely the claim a user would strand themselves on.

### 3. "Within 1 km" is network walking distance, and 1 km is measured

**The measure is network distance over foot-legal edges**, never straight line.
Straight line crosses rivers, cliffs and private land, and 1 km along a trunk road
is not an approach — it is a reason not to come. The connecting way is itself a
piece of route: it must exist in `curated.edge`, pass `load/legality.py` on foot,
and be recorded, so a reader can see what the approach actually is.

**The 1 km bound is measured, not chosen.** All 1,504 endpoints of the 752 mapped
routes were measured against the 8,110 start vertices (straight line, geodesic):

```
     0-100 m   533   ############################################
   100-200 m   130   ###########
   200-300 m    90   #######
   300-400 m    75   ######
   400-500 m    75   ######
   500-600 m    79   ######
   600-700 m    50   ####
   700-800 m    63   #####
   800-900 m    45   ####
  900-1000 m    26   ##
 1000-1100 m    26   ##      <- the decay stops here
 1100-1200 m    25   ##
 1200-1300 m    32   ###
 1300-1400 m    28   ##
 1400-1500 m    24   ##
 1500-1600 m    31   ###
 1600-1700 m    25   ##
 1700-1800 m    27   ##
 1800-1900 m    24   ##
 1900-2000 m    24   ##
    > 2000 m    72
```

p50 295 m, p90 1,688 m. The shape is what decides it: the distribution decays
monotonically from 533 to 26 across the first kilometre and then goes **flat** at
24–32 per bucket for the whole of the second. Past ~1,000 m, distance to the
nearest start carries no signal — it is the background density of starts on the
map, not a relationship between the route and a place you can arrive at. That knee
is the bound, by the same argument that took the 2 m snapping tolerance from the
near-miss histogram.

**What it costs at that bound**, under the straight-line measure: 491 of 752
mapped routes have both endpoints within 1 km of a start, and 675 have at least
one. Network distance is never shorter than straight line, so **these are
ceilings** — the real figures will be lower, and must be re-measured once the
connector is actually routed. Re-run the histogram after any change to the
network, as with every other tolerance here.

### 4. Climb joins the descriptive categories; duration still does not

`measures` already carries `ascent_m`, `descent_m`, `lowest_m`, `highest_m`. What
is missing is not the number, it is the **class twin** every styled field needs
(leading digit, so a legend sorts) — the rule `metadata-rules.md` states and
`qa.v_route` already follows with `climb_class` and `length_class`.

So the document gains a `categories` block: `distance_class`, `climb_class`,
`steepness_class`, `surface_class`, `difficulty_class`. Boundaries come from
measured distributions, like every other boundary here, and the classes are
carried in the document rather than derived by each reader — otherwise the map
legend and the chat answer describe the same route differently, which is the
failure this whole architecture exists to prevent.

**Duration stays absent**, for the reason already ratified above: DIN 33466 rates
the classic Grigna ascent at 10 hours against a guidebook 6–8, and a catalogue
reading "15 h" for a day walk discredits everything beside it. Calibrating it is a
tracked next step; the field arrives when the calibration does.

### 5. A document describes one direction of travel

Difficulty differs by direction, and directional tags invert on reversal. A single
document carrying one `ascent_m` for a loop is therefore wrong for one of its two
readers.

The rule: **a route where the walker chooses a direction is two documents; a route
whose outing contains both directions is one.**

| shape | documents | why |
|---|---|---|
| `loop`, `circular` | 2 | clockwise and anticlockwise are different walks |
| `linear` | 2 | A→B and B→A differ in climb and in grade |
| `destination` | 1 | out and back is a single outing that already contains both legs |

Each direction carries its own `ascent_m`/`descent_m` (swapped), its own
difficulty, its own reversed `profile`, and `reverse_of` naming its sibling's id.

**This forces a decision about route ids, which is why it belongs in the contract
and not in an implementation ticket.** A route id must be stable across rebuilds
and is derived from geometry, because photos, comments and likes key to it. Two
directions share one geometry. So the id has to carry the direction as well:
`<geometry-digest>:fwd` / `:rev`, with the sense fixed by a deterministic rule
— `fwd` is the orientation whose canonical rounded coordinate sequence is the
lexicographic minimum, exactly the `min(forward, backward)` that
`draw/route_id.py::canonical()` already computes — never by generation order and
never by `edge_id`, which `build_network` reassigns on every rebuild. A photo
attached to the anticlockwise walk must not migrate to the clockwise one on the
next rebuild.

**What it costs.** 126 generated loops become 252; the mapped corpus grows by its
circular and linear share. All 102 `destination` routes are unaffected. Retrace
share on the generated corpus is small (mean 0.031 for destinations, 0.045 for
loops), which is the check that matters here: these really are loops, so the
direction really is a choice and not a formality.

### 6. Routes from one start share a corridor, and the document records where they part

The owner raised that routes can share an approach. The measurement says it is the
normal case, not the edge case.

The 228 generated routes come from **12 start vertices** — 12 to 23 routes each.
Of the 2,127 route pairs sharing a start, only **156 diverge immediately**. The
common prefix runs to a median of **286 m**, a p90 of **1,813 m** and a maximum of
**11.8 km**, and **493 pairs share a kilometre or more** of identical approach.

Five results sharing three kilometres of the same track are nearly one answer
presented five times. Two things follow, and they land in different places:

- **In the document**: each route records its **divergence vertex** — the point at
  which it leaves the corridor shared with its siblings from the same terminal —
  and `approach_m`, the length of that shared prefix. This is a fact about the
  route, computed once at assembly against the sibling set, so it belongs here
  rather than being recomputed by every reader. Same argument that put `places` in
  the document.
- **In the query service**: results are diversified on the divergence vertex — at
  most one route per divergence point in a first answer, the rest reachable behind
  "more from this start". That is a ranking rule, it belongs to the reader, and it
  is only implementable because the document carries the field.

### 7. Continuity stays descriptive and never becomes a filter

Of the 752 mapped routes: **621 continuous, 89 broken (2–3 pieces), 42 scattered
(4+)** — 131 in more than one piece. Requiring `continuous` as a hard condition of
being a route would delete 17% of the corpus.

It would delete them for the wrong reason. The breaks are largely a **coverage
clipping artefact**: a route that leaves our bbox comes back as two pieces, which
says something about our ingestion bounds and nothing about the route. So
`continuity` keeps the posture `quality` already has — *carried, never filtered
on*.

Two things change rather than one:

- The terminal test in §1 is evaluated on the **outermost endpoints of the merged
  geometry**, not per piece. A route in nine pieces has two terminals, not
  eighteen.
- `continuity` gains a `reason`: `coverage_edge` | `network_gap` | `unknown`, so
  the 131 can be triaged instead of lumped together. Judging them in QGIS is
  already a tracked next step; this field is where that judgement lands.

`distance_along_m` stays null on a multi-piece route, unchanged: there is still no
single measure along a line that is not one line.

### What this contract does not settle

- **Whether a lane exit out of a settlement is a start.** 2,990 lane exits are
  recorded with "the town continuing" and reviewable in `qa.v_urban_exit`; the
  contract is indifferent to which way that goes, and flipping it stays a one-word
  change in `anchors.py::EXIT_ONTO_TRAIL`.
- **The class boundaries** for §4. They come from measured distributions that have
  not been run yet.
- **The interest weights** in `destinations.py`. They are v0 parameters by design:
  a recalibration is an argument, not a code change.

### What it implies, in order

1. `schemas/route-document.schema.json` 1.2 → **1.3**: `start` → `terminals`
   (array of 1–2, each with its reachability and four-season scoping), a new
   `categories` block, `continuity.reason`, `divergence` / `approach_m`, and
   `reverse_of`.
2. `curate/anchors.py` gains the end side: a `destination_verdict` mirroring
   `poi_verdict`, with `DESTINATION_NOT_START` derived from `INTEREST` so the two
   tables cannot drift.
3. A routed connector: network distance from a terminal to its anchor over
   foot-legal edges, replacing the geodesic `curated.place.distance_m` **in the
   reachability test only** — the snap distance stays what it is.
4. `load/gtfs.py` reads `calendar.txt` and `calendar_dates.txt`, so GTFS terminals
   stop claiming a year-round service they have never been checked for.
5. Direction: the id rule first, then two documents per loop, circular and linear
   route.
6. The divergence vertex at assembly, then diversity on it in the query service.

## The social layer

Photos, comments and likes attach to a route document by `id`. That design is
`docs/social-layer.md`; nothing is built yet.
