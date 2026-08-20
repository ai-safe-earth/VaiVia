# Handoff — VaiVia

Last updated 2026-08-18.

The project was renamed from `get-out-door` to **VaiVia** on 2026-08-17. The
GitHub remote is now `https://github.com/ai-safe-earth/VaiVia.git` and the local
root folder is `A02_VaiVia`. README, LICENSE and CONTRIBUTING have been rewritten
under the new name, and the in-code identifiers followed and are **merged to
`main`** (PRs #2 and #3): package names (`vaivia`, `vaivia-gateway`,
`vaivia-frontend`, both lockfiles relocked), the compose container
(`vaivia-neo4j`), page title and headings, the Overpass User-Agent default, the
FastAPI title, the `graph-model` skill description, and the doc and
`.env.example` headers. All three unit suites pass after the rename (148 / 34 /
33).

Two things the rename touched that are worth knowing. The compose **volumes**
(`neo4j_data`, `neo4j_logs`) are unchanged, so the ingested graph survives; only
the container is renamed and `up -d` recreates it. And renaming the root folder
broke every console-script shim in `backend/.venv` (Windows `.exe` launchers
hardcode the absolute interpreter path, so `uv run black` failed with "Failed to
canonicalize script path"); deleting `.venv` and re-running `uv sync` fixes it.
Anyone else who pulls after the folder rename will hit the same thing.

## Where the project stands

A trail-query chatbot backed by a Neo4j knowledge graph. A working four-tier
monorepo: Next.js frontend, Fastify gateway, FastAPI backend, Neo4j graph.

**The data story changed on 2026-08-18.** It was "OSM geometry fused with
Trailforks curation"; Trailforks turned out to be legally unavailable and OSM
turned out to be enough, so it is now OSM throughout, with open-licensed
enrichment (Wikipedia/Wikidata) over the marquee places. Supabase supplied auth
and Postgres and is currently **switched off** — see the auth note below.

The product works end to end against real infrastructure and has been driven in
a real browser: sign-in, resumed conversation history, a live streamed chat turn
grounded in the graph, and the trail drawn on the map — all of it pinned by a
repeatable Playwright suite. Status stays amber for one reason only: three
credentials were shared in plaintext during development and must be rotated
before anything deploys. Everything else that remains is Phase 6 hardening
(embeddings, deploy plumbing), not unverified core.

## What is built and how far it is verified

| Piece | State | Verification |
|---|---|---|
| Graph schema and ontology | Owner-validated, frozen | Applied to the live database (16 statements, region seeded) |
| Ingestion (OSM) | Reworked 2026-08-18 | Filter widened to connective ways: Lecco now 70,847 routing edges, 3,195 POIs. Trailforks ingestion is a deliberate stub (licensing) |
| Query service (FastAPI) | Complete | Tests against a fake graph client; live `/routes` and `/trails` verified over HTTP |
| Gateway (Fastify) | Complete | 28 tests; real Supabase ES256 token verified against the live JWKS |
| Chat orchestration (OpenAI) | Complete | 33 offline tests, plus 15/15 against the live OpenAI API; live turns persisted to Supabase |
| Frontend (Next.js + MapLibre) | Complete | 33 unit tests; `next build` clean; driven in a real browser |
| Supabase store and quotas | Complete | Schema applied; 12-check live round-trip of `PostgresStore`; gateway quota store queries the real database |
| Supabase auth | **Parked 2026-08-18** | Works, but Supabase is being switched off. `GATEWAY_DEV_NO_AUTH=true` runs everything as `dev-local-user`; gateway refuses to boot with that flag in production |
| Graph, live | Ingested and idempotent | Schema applied to a real Neo4j; both ingesters run twice leave counts identical (`scripts/smoke_graph.py`) |
| Spatial matching | Complete | Fixture re-cut along real OSM ways; 39 `COMPOSED_OF` edges, idempotent |
| Routing (GDS Dijkstra) | Works, but superseded by a decision | Now comfort-weighted (`cost_m`), off-road 17% -> 61-64%. `docs/routing-engine.md` decides in favour of GraphHopper for geometry; not migrated |
| Sign-in + conversations | Complete | Real browser session against the full stack: sign-in, history resumed under RLS, live streamed turn, trail drawn on the map; anon role reads zero rows |
| Playwright e2e | Complete | 4/4 against the live stack in ~10 s; first run caught a real mid-stream remount bug |
| Gateway claim pinning | Complete | iss/aud pinned when SUPABASE_URL is set; right-key/wrong-claim tokens 401 in tests, real token passes live |
| Semantic search | Complete | 503 verified live on the unpopulated index; job idempotent (3 embedded, 0 on re-run); three distinct queries each ranked the intended trail first |
| Query decomposition + composer | Complete | Model decomposes into atomic subqueries; Python composer merges tightest-wins, drops vacuous 0-bounds, clarifies with suggestions when under-specified; 17/17 live containment (adversarial 7/7 clarify) |
| Semantic + filters in chat | Complete | New `semantic_search_trails_filtered` template (vector pool → NULL-idiom filters); degrades to structured search with `semantic_unavailable` while the index is cold |
| Trailforks links | Complete | `trailforks_url` stored at ingestion (from `alias`/explicit URL, never guessed), returned by all trail templates, linked on TrailCard and cited by the answer prompt |
| Golden dataset eval | Complete | `fixtures/golden_questions.json` (24 questions incl. Bergamo + season-hazard cases) + `scripts/eval_golden.py`; live: decomposition 24/24, retrieval 16/21 ranked-first (misses: no bathing_water POI in either bbox; model over-constraining ambiguous phrasings) |
| NEAR_POI proximity edges | Complete, live-verified | `(:Trail)-[:NEAR_POI {distance_m}]->(:POI)` at ingestion (500 m); fixture walks now anchor near a lake/hut, so lake and hut filters return the right trail live |
| POI full-text lookup | Complete, live-verified | `poi_name_fulltext` Lucene index; route resolution queries it first with escaped input (`core/text.py`), CONTAINS as fallback |
| Richer embedding input | Complete | Input now adds activity/difficulty, seasons, and POIs along the way; sha-gated job re-embedded only changed trails |
| Season-scoped hazards | Complete | `hazards_<season>` lists on Trail, `seasonal_hazards` stays the union; queries check the requested season's list (union when unseasoned); unscoped records get the union in every season |
| Bergamo region | Complete, live-ingested | Multi-region config (`REGIONS`), `osm_ingest --region`; 24,859 intersections / 25,755 segments / 51,503 edges / 101 POIs from live Overpass; two new mock trails (Canto Alto Skyline hike, Colli di Bergamo Ride mtb) anchored on real Bergamo POIs |

Totals: 173 backend, 40 gateway, 33 frontend unit tests plus 4 e2e, all
passing. CI runs the three unit suites and stays fully offline; the e2e suite
is a local/pre-deploy check that skips itself without credentials.

## The two properties the redesign exists to guarantee

**The browser never reaches anything but the gateway.** The gateway is the only
public service. It verifies Supabase JWTs, rate-limits per user with an IP
fallback, enforces the origin allowlist, pre-checks the LLM quota, and proxies
only `/trails`, `/routes`, and `/chat`. Everything else 404s there. The backend
trusts only the shared-secret hop and never parses a token.

**The model never writes Cypher.** Its only structured output is a plan of
validated atomic subqueries (`TrailSearchIntent | RouteIntent |
SemanticThemeIntent | ClarifyIntent`). `chat/composer.py` — Python, not the
model — merges them tightest-wins and maps the result onto named, read-only,
parameterized templates; a semantic theme is embedded server-side and reaches
the vector index as a list of floats. No field in the schema can carry a query,
a template name, or an identifier, and one `Clarify` anywhere in the plan stops
the whole turn. Against the live API, seven of seven injection and jailbreak
payloads were contained this way.

## Read these before changing anything

- `docs/plan.md` — the delivery plan, decisions, and per-phase checkboxes.
- `docs/architecture.md` — the graph model, corrected during the redesign.
- `docs/fragilities.md` — known failure modes and the mitigations chosen.
- `CLAUDE.md` — the rules a contributor is most likely to break by accident.

## Supabase is wired

Project `fatktvawkmrytywegjjz` (eu-west-1) is live and the schema is applied:
`conversations`, `messages`, `usage_ledger`, `daily_quotas`, RLS on with one
policy each. Chat history, the cost ledger and quotas now survive a restart.

Two things about the connection are worth knowing before anyone edits an env
file, because both fail in ways that look like something else:

- **The direct host is unusable.** `db.<ref>.supabase.co` publishes an AAAA
  record and no A record, so without IPv6 it does not resolve at all. Every
  `DATABASE_URL` points at the Supavisor pooler
  (`aws-1-eu-west-1.pooler.supabase.com`) in **session** mode, port 5432 — not
  6543, because these services hold long-lived connections.
- **`sslmode=require` in the URL breaks the gateway.** The bundled `pg` treats
  it as `verify-full`, and Supabase's pooler certificate does not chain to a
  public root, so the connection is rejected as self-signed. TLS is instead
  selected in `gateway/src/quotaStore.ts`, which encrypts for any non-local
  host. This matters: a bare connection string connects happily *in plaintext*,
  which is exactly what makes it easy to miss.

## Dependency audit, triaged 2026-08-17

`npm audit` reported 7 findings in the gateway and 8 in the frontend. They are
not equally serious and the counts are misleading, so here is what each one
actually meant.

**The gateway's critical was real and is fixed.**
`@fastify/http-proxy` 10 carried GHSA-gwhp-pf74-vj37 — a client can name a
header in `Connection:` and have the proxy strip it *after* the rewrite hook
added it. That is precisely the gateway's trust mechanism: `app.ts` injects
`x-gateway-secret`, `x-user-id` and `x-user-email` in `rewriteRequestHeaders`.
The saving grace is that both consumers **fail closed** —
`GatewayTrustMiddleware` 401s on a missing or wrong secret, and `/chat` 401s on
an empty `x-user-id` — so the reachable impact was a caller denying its own
request, not forging an identity or evading the quota ledger. Client-supplied
`x-user-id` was never a risk either: the rewrite spreads incoming headers first
and then overwrites. Upgraded to `@fastify/http-proxy` 11.6.0; 34/34 gateway
tests and typecheck pass. Gateway production dependencies are now at zero
findings.

**Everything else was dev- or build-time.** The `vitest`/`vite`/`esbuild` chain
(GHSA-67mh-4wv8-2f99) only exposes a dev server on a developer's machine;
bumping `vitest` to 3 in both packages cleared it, with all tests still passing.

**Three high findings remain in the frontend and are deliberately deferred.**
`postcss` (CSS-stringify XSS and `sourceMappingURL` path traversal) and `sharp`
(inherited libvips CVEs) are both reached only through `next` 14, and npm's only
fix is `next` 16 — a two-major framework migration. Neither is reachable as this
app is built: the CSS is authored in-repo rather than attacker-supplied, and
nothing imports `next/image`, which is what pulls `sharp` into a running server.
Do the Next upgrade as its own piece of work, not as an audit drive-by.

**Merged 2026-08-17.** The dependency audit landed on `main` (PR #4) after a
manual browser pass confirmed SSE still streamed through the new
`@fastify/http-proxy` major.

## Session 2026-08-18: the product turned a corner

Everything below is on **`spike/osm-coverage`**, 15 commits, not merged. The
branch outgrew its name on the first afternoon; treat it as a feature branch and
decide whether to rename or split it before merging.

The short version: the Trailforks dependency was found to be unusable, OSM was
measured and turned out to be enough, and the routing that was supposed to be
the hard part turned out to be broken for a reason nobody had looked for.

**Trailforks is not available, and this is settled.** Their data is API-only
with a granted key, and the Outside terms require prior written consent for
commercial use, use in a software program, and AI use — VaiVia is all three.
Approval is discretionary and explicitly "not guaranteed". The saving grace is
that **nothing was ever taken**: `fetch_live()` is a stub, there is no HTTP
client, and the fixture is synthetic. Full brief with quoted terms and a draft
access request in `docs/licensing.md`. Blocker re-triaged low -> high.

**OSM covers more than assumed.** 302 named CAI *sentieri* across the two
regions against the 5 synthetic trails we ship; `sac_scale` on 33-43% of paths
and `mtb:scale` on 23-27%, both mapping onto our difficulty 1-4. Only
`description` is thin (10-21%), so composed-from-facts stays the primary
description source.

**The routing graph was shattered and nobody knew.** Loop generation returned
0/10, and the cause was not the algorithm: the ingestion filter took only
path/track/cycleway/footway, and trail networks connect *through* roads. Lecco
was 1,627 components with the largest holding 31.7%, and the waterfront the map
opens on sat on an island of 14 intersections. Widening the filter took it to
171 components / 98.1%, and loops went 0/10 -> 10/10. It had gone unnoticed
because routing was only ever verified on a POI pair that happened to share a
component, and every fixture trail was built by tracing existing ways, so it was
connected by construction.

**Routing then preferred roads**, because Dijkstra minimised raw distance and
roads are straighter — a "10 km trail loop" came back ~83% asphalt. `cost_m`
(distance x a per-highway/surface penalty, `core/comfort.py`) fixed it: off-road
share 17% -> 61-64%. The trap it creates is recorded and guarded by a test:
GDS `totalCost` is now a penalised figure in no real unit, so **every distance
shown to a user must be summed from `distance_m`**.

**Decision: adopt GraphHopper for geometry, keep Neo4j for meaning.** Gate
passed — with our comfort model ported to its `custom_model`, off-road is 67.0%
at 15 km and 67.7% at 20 km against our 61.0/64.1, retrace is 0.0-3.2% against
our ~20%, all 30 candidates route, and climb comes back real (296-2,732 m) where
ours is silent because elevation was never ingested. It also decodes `sac_scale`
and `mtb:scale` natively and pruned 13,778 subnetworks on import without being
asked. Full comparison in `docs/routing-engine.md`. **Not migrated.**

**The map-back is proven**, which was the last unknown in that architecture.
GraphHopper does not expose `osm_way_id`, so `graph/route_context.py` joins a
route polyline to the graph *spatially*: a real 13.89 km loop returned 19 POIs
within 150 m, three named saddles at 0.0 m because it crosses them. The spatial
join is arguably better than an id join — it answers "what does this route
pass", and it survives the engine splitting ways differently from our ingestion.

**The POI layer was the real blocker for the product's route model** and is now
fixed. It had 8 types, nodes only — no parking, no peaks, no ermitas. Lecco now
has **3,195 POIs**: 1,511 parking, 569 chapels, 281 peaks (243 named), 127
saddles, 155 lakes. **1,686 of them (53%) are areas** that a nodes-only query
never saw; lakes were 154 areas against 1 node, which is why `NEAR_POI` needed a
500 m radius — that tuning is now worth revisiting.

**Trailheads exist**: 1,511 car parks cluster to **266 `(:Trailhead)` nodes**,
each scored by off-road share within 750 m (46 trail / 145 mixed / 75 urban).
Catalogue size is now predictable at roughly 4,000 routes for Lecco.

**Auth is disconnected** so Supabase can be switched off:
`GATEWAY_DEV_NO_AUTH=true` runs every request as `dev-local-user`. The gateway
**refuses to boot** if that flag is set with `NODE_ENV=production`. Real
credentials are commented out (not deleted) in the three gitignored `.env`
files. Reconnecting means uncommenting **and rebuilding the frontend**, because
`NEXT_PUBLIC_*` is inlined at build time. While parked, LLM quotas are not
enforced and chat history is in-memory.

Also fixed: an unstated `activity` was silently over-constraining every search
(the model reached for `"mixed"` to mean "no preference", the one value that
cannot mean it) — live golden retrieval 16/21 -> 18/21. And OSM data attribution
now credits the data rather than only the basemap tiles.

## Session 2026-08-18 (part two): the catalogue exists

On **`feat/route-catalogue`**, 5 commits, branched from `main` and **not pushed**
— everything below lives only on this machine until it is.

The pipeline in `docs/route-pipeline.md` is now built end to end. Neo4j has
stopped being the routing engine and become the catalogue a chat turn chooses
from.

**Stage 2-5: generate, score, dedup, enrich, persist**
(`graph/route_generation.py`, `graph/route_scoring.py`, `scripts/build_routes.py`).
Generation is deliberately prolific because offline it is cheap; quality comes
from scoring and dedup afterwards. Scoring is pure functions with tests, since
it encodes taste and taste should be arguable in a test rather than buried in a
script. Weights are length 40 / off-road 30 / variety 20 / climb 10.

**Stage 7: `loop_search`** — a new atomic intent beside trail_search / route /
semantic_theme / clarify. It carries only what a walker says out loud (distance,
features, a place to start near, activity, difficulty, ascent) and maps onto
`search_loops`, which filters `(:Route)` and orders by the offline score.
Verified live: *"a 15 km loop on trails past a peak near Lecco"* returns real
catalogue loops over Monte Ocone and Punta Cermenati with no routing in the
turn. `check_intents_live` stayed 17/17 with the adversarial half at 7/7, so
adding an intent did not weaken containment.

**GraphHopper is a real service now** (`infra/docker-compose.yml`,
`infra/graphhopper/`), supplying the two things our own graph cannot:

- **Elevation.** Every `CONNECTS_TO` edge reports 0 m (fragility #6), which made
  duration and difficulty unanswerable even though `core/durations.py` has
  implemented DIN 33466 all along. One config line (CGIAR SRTM) gives every
  route real ascent.
- **Per-activity profiles.** Activity is not a filter you apply to one catalogue
  afterwards — a foot loop over steps and a T4 scramble is impassable on a bike
  — so `hike` and `mtb` generate separate catalogues, with `mtb` excluding steps
  outright. Activity is part of the route id and `CLEAR_ROUTES` is
  activity-scoped, so rebuilding one cannot destroy the other.

Difficulty arrived with them: GraphHopper decodes `sac_scale` to `hike_rating`
and `mtb:scale` to `mtb_rating`, so the filter set the owner asked for — length,
time, difficulty, activity — is now expressible. Time is the one still to
compute, and it only ever needed ascent.

Current catalogue:

| | hike | mtb |
|---|---|---|
| Routes | 255 | 218 |
| Mean score | 0.77 | 0.74 |
| Off-road | 74% | 66% |
| Retrace | 4% | 5% |
| Mean ascent | 1,719 m | 1,631 m |

Retrace 25% -> 4% against our own generator is the headline. The **length gate**
is what bought the score: `round_trip.distance` overshoots, badly in steep
terrain where the only paths out are long, so about half of what was generated
answered a different question than the one it was filed under. Those are dropped
at persistence — not in the scorer, which stays honest — and the drops are
reported per target, because a target that mostly fails is a coverage fact.

The 502 pre-activity routes were deleted after confirmation: no activity, no
elevation, superseded. The query keeps an `activity IS NOT NULL` guard so a
future unlabelled route cannot leak into results.

**Two confident claims made this session were wrong, both recorded in the docs
rather than quietly fixed:**

1. *"A near-constant 113-121 m/km proves the elevation is SRTM noise."* It was a
   selection effect — the catalogue only holds trailheads above 60% off-road,
   which are mountain trailheads. Flat starts give 1-40 m/km. Smoothing was
   added on that false diagnosis and kept only because it is harmless.
2. *"Zero 5 km routes survived the gate, so short loops do not exist at alpine
   trailheads."* A `tail -16` had cut the row off the table. There are 44 hike
   and 43 mtb 5 km loops averaging 5.4 km against target.

Both were the same failure: reading a filtered or truncated view as if it were
the whole.

## Session 2026-08-18 (part three): loops became visible, and one ingestion bug

Still on **`feat/route-catalogue`**, still **not pushed**. One commit landed
(`ef1df5b`); **seven files are modified and uncommitted** — finish or revert
them before anything else (see "Where this was interrupted").

### What shipped

Driving the frontend showed loops as prose only: no name, no card, no line on
the map. Three causes, all now fixed and committed.

**Names.** A route's only id was `1461822581:hike:15000:0`, and trailhead names
would not have helped (37 of 266). But every route already had
`-[:PASSES]->(:POI)` edges carrying a name AND a type, so
`scripts/name_routes.py` derives one from the best thing it passes — peak, then
saddle, lake, castle, waterfall, chapel. **81% of 473 routes are named**, 190
from peaks. Null stays null: the card shows distance rather than inventing
something. No regeneration was needed, which mattered — that would have been
hours of GraphHopper calls.

**Durations** came with it. `core/durations.py` implemented DIN 33466 all along
and only needed ascent; a loop returns to its start, so descent equals ascent.

**The map.** Loop geometry never left the database — `route_geometry` sat in
`queries.cypher` with no caller and no endpoint. `GET /routes/{route_id}/geojson`
now serves it, needing no gateway change since `/routes` was already proxied.
`LoopCard` mirrors `TrailCard`; all returned loops draw at once and clicking one
highlights and zooms to it, via a `selected` feature property so switching is a
restyle rather than a refetch.

The live intent check earned its cost again: after the prompt change the model
began calling "a 2 hour mountain bike ride" a loop, which exposed that
`loop_search` could not express **duration at all** despite routes now having
one. Added `max_duration_min`, tightened the prompt to require a real
circularity signal, and it went back to 17/17 with the adversarial half at 7/7.

### Why "a 5 km route around the lake" still cannot work

Two independent faults, found by chasing that question:

1. **The catalogue has no lakeside routes.** It was built with
   `--min-off-road 0.6`, which is 46 of 266 trailheads and all of them mountain
   ones. Near the Lecco shore there are 62 trailheads and **only 6 made the
   cut** — a lakeside promenade is footway and road by nature. Every `lake` match
   in the catalogue is an alpine tarn or pond.
2. **Lago di Como could never be matched anyway.** It is a relation whose
   centroid sits **5,122 m out on the water**, and the map-back radius is 150 m.
   No bigger radius fixes it: 5 km would sweep in half the region.

Fault 2 is fixed in the working tree: area POIs now keep a sampled (~100 point)
boundary and an `extent_m`, the bounding query widens by that extent, and
`route_context.poi_distance_to_route` measures to the boundary for areas and to
the point for nodes. A test pins it — a shoreline route reads under 150 m from
the lake with a boundary and over 4 km without.

Fault 1 is **not** fixed. It needs a rebuild at `--min-off-road 0.3`, which is
the next real task.

### The ingestion bug this introduced, and what it teaches

Switching POIs to `out geom` (needed for boundaries) made lake and car park
outlines indistinguishable from routing ways — with `out geom` a POI way arrives
carrying geometry AND a node list, exactly like a path. Having no `highway` tag
it took the `"path"` default in

    highway_type=tags.get("highway", "path")

and became routable. **1,673 lake and parking outlines entered the routing graph
as walkable paths.**

It was not caught by a test. It surfaced because the boundary count came back as
12 when ~1,686 was expected, and chasing that discrepancy found it. Every unit
test passed throughout, because none fed a POI way and a routing way through
`extract` together — there is now one that does.

The fix discriminates by TAGS, not by shape: a routing way must have a `highway`
tag, an area POI must not. Cleanup was surgical rather than a wipe: of 2,242
suspect segments, 2,237 claimed `"path"` from 1,673 parent ways (the outlines)
and 5 claimed `"service"` from one way — a genuine parking aisle that is
legitimately both a road and a POI. Deleting all 2,242 would have removed real
road; only the 2,237 were deleted, and segments returned to 104,812.

**The lesson worth keeping:** `tags.get("highway", "path")` is a dangerous
default. Silently calling an untagged way a path is what turned a filter bug
into routable water. `None` plus an explicit skip would have failed loudly at
ingestion instead of quietly corrupting the graph.

### Where this was interrupted

A Lecco re-ingest was **in flight** when the session ended, to give the way-based
area POIs their boundaries under the fixed filter. At the last check the graph
still showed only **12 boundaries** (the relations from the earlier broken run),
so that re-ingest either did not finish or needs re-running. Verify before
trusting any lake matching:

    uv run python -m ingestion.osm_ingest --region Lecco
    # expect ~1,686 POIs with a boundary, segments back at ~104,812

Then, in order: re-run `scripts/build_trailheads` (component ids change with the
routing graph), rebuild the catalogue at `--min-off-road 0.3`, and re-run
`scripts/name_routes`.

## The design that ties it together

`docs/route-pipeline.md` records the architecture the owner set out: build
geometry offline, enrich it, persist to Neo4j, and let chat **select** rather
than compute. Neo4j stops being the routing engine and becomes the enriched,
embedded catalogue. Two things it still needs a decision on: how generation is
bounded (proposed: anchors x distances x top-N), and that Wikipedia/Wikidata is
a supplement rather than a foundation — 48 real descriptions across 3,195 POIs,
and the Wikidata one-liners must NOT be embedded, being ~27-character category
labels that would add noise and make a POI look described when it is not.

## What blocks progress

1. **Credentials shared in plaintext must be rotated before any deployment.**
   The OpenAI key in `backend/.env`, and the Supabase database password, have
   both been pasted into chat transcripts. They work today and are gitignored;
   treat both as compromised.
2. **Trailforks licensing is a product constraint, not a data-plumbing task.**
   Reviewed 2026-08-17 against the primary sources; full brief in
   `docs/licensing.md`. Their Data Use Policy permits use only via the API with
   a granted key, and the Outside Terms of Use (Trailforks is Outside-owned)
   restrict the Services to "personal, noncommercial use" while separately
   naming "development of any software program" and AI use as requiring prior
   written consent. VaiVia is all three. Approval is discretionary and
   explicitly "not guaranteed".

   The good news: **nothing has ever been fetched from Trailforks.**
   `fetch_live()` raises `NotImplementedError`, there is no HTTP client, and the
   fixture is synthetic prose over OSM-traced geometry — so there is no exposure
   to remediate, only a decision to make. Either pursue API access and written
   consent (draft request in the brief), or scope an OSM-only product. Do not
   assume approval in the roadmap.
3. **The account password is `12345678`.** It is eight characters, entirely
   numeric, and has been pasted into a chat transcript. Fine for a scratch
   login today; it must not survive contact with a deployed service.


## Running the graph locally

Docker Desktop is installed and the stack runs. Note two local specifics:

- **Neo4j is on 7688/7475, not the defaults.** An older copy of this project
  (container `god-neo4j`, `restart: unless-stopped`, from
  `…\Learning\google, kaggle, antropic, openai\dev\agentic\get-out-door`)
  already binds 7687/7474 and starts with Docker Desktop. The compose ports are
  now variables; the root `.env` moves this stack aside so both can run. Stop
  that container and clear `NEO4J_*_PORT` if you would rather have the defaults.
- **The first boot after Docker starts can lose GDS.** The plugin installer
  fetches a version manifest over the network, and on a cold Docker Desktop it
  ran before networking was ready: APOC installed, GDS silently did not, and
  Neo4j started anyway. Recreating the container fixed it. If `gds.version()`
  is unknown, that is what happened — recreate rather than debug.

Current state: schema applied, two regions ingested — Lecco (15,937 segments /
31,848 edges) and Bergamo (25,755 segments / 51,503 edges, live Overpass) —
GDS 2.13.12 loaded, five mock trails matched and embedded. The data volume
persists across `docker compose … down`, so `up -d neo4j` restores the graph
without re-running ingestion.

## Suggested order of work

The whole product works end to end and is pinned by a repeatable Playwright
suite (`cd frontend && E2E_EMAIL=… E2E_PASSWORD=… npm run test:e2e`, add
`E2E_LIVE=1` to spend one real OpenAI turn). Its first run earned its keep by
catching a bug the manual browser pass missed: a brand-new chat's first answer
was destroyed mid-stream, because assigning the conversation id remounted the
panel. What remains is deploy plumbing (Caddy TLS, VPS deploy script, backup
cron, uptime check against /healthz) and the credential rotations. Rotate all
three credentials before anything deploys.

Both gateway findings from the auth verification are resolved. The "missing
health endpoint" turned out to be a wrong finding: the gateway has always served
`/healthz` (matching the backend's path) — the check that produced the finding
curled `/health`. And the gateway now pins `iss` (`<project-url>/auth/v1`) and
`aud` (`authenticated`) whenever `SUPABASE_URL` is configured, verified both by
negative tests (right key, wrong issuer or audience -> 401) and live: a real
Supabase token still passes with pinning active.

## Running it locally

```bash
# Neo4j (needs Docker)
docker compose --env-file .env -f infra/docker-compose.yml up -d neo4j

# Backend
cd backend && uv sync
uv run python -m scripts.init_schema
uv run python -m ingestion.osm_ingest
uv run python -m ingestion.trailforks_ingest --mock
uv run uvicorn api.main:app --reload

# Gateway
cd gateway && npm install && npm run dev

# Frontend
cd frontend && npm install && npm run dev
```

Test suites: `uv run pytest tests/ -v` in `backend/`, `npm test` in `gateway/`
and `frontend/`. After changing chat prompts or intents, re-run
`uv run python -m scripts.check_intents_live` — it costs money and the
adversarial half must stay at seven of seven.

## Knowing what a session cost

`backend/scripts/cost_by_commit.py` attributes Claude Code token spend to git
commits, sessions, or models. It reads the local transcripts under
`~/.claude/projects/`, buckets each request into the commit whose interval
contains its timestamp, and prices the tokens at list rates. Stdlib only, so it
runs without `uv sync`.

```bash
python backend/scripts/cost_by_commit.py              # per commit
python backend/scripts/cost_by_commit.py --by session # or model / branch
python backend/scripts/cost_by_commit.py --json       # feeds sessions[].credits
```

Two things it gets right that a naive count does not. Each API request writes
several assistant records to the transcript — one per content block — and every
one repeats the *same* usage object, so the script dedupes on `requestId`;
summing raw records roughly doubles the figure. And cache reads dominate the
token volume by an order of magnitude while billing at a tenth of the input
rate, so 5-minute writes, 1-hour writes, and reads are priced separately.

The numbers are list-price API equivalents. On a subscription plan nothing here
is billed per token — `/usage` is what reflects real plan consumption. Build
cost through Phase 5 was roughly $62.

## 2026-08-19 - Brand system v1.0 on the frontend

The frontend now wears the brand system in `assets/brand/` (spec, tokens, four
logo files, ten icons): dark ground, two accents with fixed meanings, square
corners, 1px hairlines, no shadows. `assets/` had never been committed; it is in
this commit alongside the code that implements it.

The visible changes: the transcript is a ruled document rather than chat
bubbles, the route card is a hairline-delimited band with a figure row (distance
lime, then ascent, walking time and grade), hazards are a 6px flare bar with a
calm sentence instead of red pills, and every route carries a Sources disclosure
saying in the UI that the two sources are matched by proximity and never merged.
Tokens are imported once globally and no component holds a hex literal; the only
exception is MapView, which resolves `--vv-lime` off the root because MapLibre
paint properties cannot read a CSS variable. The basemap is desaturated and
darkened at the raster layer rather than by a CSS filter over the canvas, which
would have taken the route line down with it.

### Five blocks ship inactive, and each is waiting on the backend

They are in the UI, in their right places, visibly unavailable rather than
absent, so the work needed to finish them is obvious instead of forgotten. The
unavailable map tabs use the brand system's own `--vv-muted` unavailable state
and are really `disabled`, not merely grey.

| Block | Where | Waiting on |
|---|---|---|
| "How I read it" | between the answer and the routes | `/chat` streams results, not the plan the composer merged |
| Elevation profile | map bottom panel | the payload has total ascent, not a height series |
| Places layer | map tab | POIs do not travel with map geometry |
| Hazards layer | map tab | hazards are per trail, not per segment, so there is nothing to draw them on |
| Coverage layer | map tab | nothing exposes where coverage stops |

The Sources disclosure has the same shape of gap. The brand spec asks it to show
OSM way ids, the Trailforks id and a MAPS_TO distance; the payload carries none
of them, so it shows what is real (graph id, OSM/ODbL) and the component already
takes `osmWayIds` and `matchDistanceM` for the day the API returns them. The
mockups' sample ids are deliberately not shipped. Note also that **MAPS_TO does
not exist** - the model's single link is `COMPOSED_OF {seq, match_confidence}` -
so the spec's label is stale and the row is labelled `link`.

### Judgement calls worth knowing

- The Trailforks credit ("terms pending", per the spec) renders only when a
  trail actually carries a `trailforks_url`. No Trailforks data has ever entered
  the system, and an unconditional credit would claim a source we do not use.
- The composer placeholder is "Type what you want to do", not the spec's "Speak,
  or type": there is no voice input on web, and the spec's own web composer has
  no mic button. The banned "Find me a trail..." string is gone.
- SAC difficulty squares render only for hiking routes with a `hike_rating`,
  filled to the grade and hollow beyond, never flare - there is no user-stated
  limit in the payload to compare against. MTB routes show the worded grade;
  rendering `mtb:scale` as SAC squares would invent a grade.
- MapLibre's stylesheet is imported by the component and so lands after
  `globals.css`; its white attribution pill and control shadow won at equal
  specificity. The overrides are scoped under `.map`.
- IBM Plex Mono is not loaded - the token's `ui-monospace` fallback is what
  renders. Loading it means `next/font` and a build-time font fetch.

Verified: 40 frontend unit tests, `tsc --noEmit` and `next build` all clean, and
the result driven in a real browser against fixture data - zero rounded corners,
zero box-shadows, ground `#0D0F0E` on every surface, attribution a full-width
dark row. The harness route that rendered the fixtures was deleted before the
commit.

Two things this session found that are not about branding. `npm run lint` in
`frontend/` is broken independently of this work: `next lint` is deprecated and
prompts interactively because there is no ESLint config in the repo. CI runs
`npm test` and `npm run build`, so nothing is red - but the lint script does not
work if you type it. And `CLAUDE.md` had drifted two tiers behind the repo (no
`pipeline/`, gateway and frontend still marked as future phases); it was brought
up to date in the same commit.

Three blockers below were removed because the repo contradicts them: Supabase is
back on against the local stack (commit 4b3c445), and `feat/route-catalogue` is
both pushed and merged into `develop`, with a clean working tree. The credential
rotations are still open.

## 2026-08-19 (later) - The QA loop closes: repairs, and a missing rule

`topology/repair.py` now exists — the second half of the loop `qa.py` opens, and the
thing the 2 m tolerance was measured for. It repairs the FINDINGS of the latest QA run
rather than a fresh scan, so what was judged in QGIS is exactly what changes.

Against the two provinces, all four repairable rules went to zero and the network kept
every metre:

| rule | before | after |
|---|---|---|
| gap_dangle_pair | 9 | 0 |
| gap_dangle_edge | 92 | 0 |
| gap_dangle_junction | 15 | 0 |
| degenerate | 488 | 0 |
| island | 389 | 370 |
| overlap | 128 (972.6 m) | 164 (1,168.5 m) |

Loose ends 14,769 -> 14,586. Components 407 -> 387. **Total length 9,238.0 km, unchanged** —
that is the check that matters, because a repair pass which moves the length of the
network has either invented ground or thrown some away.

### A third gap class nobody could see

Reconciling the histogram against the detectors was supposed to be bookkeeping and instead
found a missing rule. Of the 231 loose ends within 2 m of an edge they are not joined to:
129 are **stubs** (their own edge ends at a junction under 2 m away, so that junction's
other edges register as a near miss — nothing is broken), and 102 are real. The pair rule
saw 14 of those, the edge rule 92, and the remaining ones were invisible: a loose end
stopping just short of an EXISTING junction. The pair rule needs both ends to be dangles;
the edge rule excludes anything near an endpoint, to avoid double-reporting the pair case,
which excluded near-junction gaps along with it. That is `gap_dangle_junction`, 15 of them,
and it now has a QGIS layer like every other rule.

### Two mistakes in the degenerate rule, both the same mistake

Both were caught by looking at the numbers after the first pass rather than trusting it,
and both are a defect in the ROUTING GRAPH being treated as a defect in the GROUND:

- **Self-loops are real.** A loop trail mapped as one closed way has source = target, which
  no shortest path can enter. Deleting them removed **26.3 km of network**, the longest a
  640 m loop way. They are split at the midpoint now.
- **Sub-metre edges carry a connection.** Deleting 245 of them severed the joins they
  carried and created **129 new loose ends**. They are collapsed now — weld the two ends,
  the edge disappears, the neighbours stay joined.

The network was rebuilt from staging to undo that first pass, which is exactly what
"replace, not merge" in build_network is for. Only a zero-length ring is deleted now.

### A materialised view that lied

`curated.vertex_degree` is read by every detector, and 0004 said `build_network.py`
refreshed it after a rebuild. It did not. A rebuilt network was therefore measured with the
PREVIOUS network's degrees — the same 101,870 edges reported 9 dangle pairs before a
rebuild and 19 after, with nothing changed in between, and a repair pass driven by those
numbers would have welded vertices chosen off a graph that no longer existed. The refresh
now happens where the comment always claimed it did, and `topology/qa.py` refuses to run
when the matview's row count does not match `curated.vertex`.

This is the one to remember: it was silent, it was in a file whose comment asserted the
opposite, and only a number that changed when nothing had changed exposed it.

### Where the review bundle stands

`export/review_bundle.py` gained a `gap_dangle_junction` layer and a `fix` layer (what the
last pass changed, with how far each end moved), and two fixes of its own: context is
scoped to the latest run (it had been accumulating every run's neighbourhoods — 7,219
edges of "context" for nine findings) and now follows overlap as well, since overlap is
what is left to judge. A refreshed bundle was written outside the repo because QGIS held
the old GeoPackage open.

### Open, and honest about it

- **Overlap is the one number that moved the wrong way**: 128 findings / 972.6 m before,
  164 / 1,168.5 m after. Welding two near-duplicate ways together makes duplication
  measurable where before it was two lines with a gap between them. Not repaired
  automatically — it needs judgement per case, and it sits in `qa.v_overlap` waiting for it.
- 370 islands remain, deliberately. They are a coverage fact, not a defect.
- `routable_bike` is true on 97.9% of edges. Legality works (bicycle=no is honoured, steps
  are excluded, access is honoured with the specific-key override), but footways with no
  bicycle tag stay bikeable by design, and the comfort model that justifies that lives in
  `backend/core/comfort.py` — which no longer produces data. 198 edges at sac_scale T4 or
  worse are bike-routable and only 15 of those carry any mtb:scale. A SAC ceiling in
  `load/legality.py` is a small, testable change; it needs a reload to take effect.

## 2026-08-20 - The network has names: route relations joined

752 OSM route relations had been loaded on 2026-08-19 and read by nothing. Their members
are OSM way ids and `curated.edge.way_id` is the same id, so the join already existed in
the data and had simply never been written. `pipeline/curate/routes.py` writes it into
`curated.edge_route`.

| | |
|---|---|
| relations joined | **752 of 752** |
| links written | 25,719 |
| distinct member ways the network holds | 10,246 of 15,392 |
| edges carrying a named route | 17,118 - **2,469.5 km** of 9,238.0 |
| edges with no `name` of their own that now carry one | **10,361** |
| routes that merge into a single continuous line | 621 of 752 |
| edges carrying more than one route | 5,295 |

The network itself is untouched, and the check is the same one every pipeline pass uses:
9,238.0 km before, 9,238.0 km after. The 5,146 member ways the network does not hold are
outside the two region bboxes or were excluded by `load/legality.py` - expected, and the
reason `qa.v_route_coverage` exists.

### Why it is a table and not a column

5,295 edges belong to more than one relation - a sentiero shared with a Bicitalia route, a
variante rejoining its parent. A column on `edge` would pick one and silently discard the
rest. The key is `(edge_id, rel_id, member_index)` rather than `(edge_id, rel_id)`, because
a way may appear **twice in the same relation**: 140 measured cases, an out-and-back leg
walked in both directions, and collapsing the second visit loses half the route.

The relation's own tags are deliberately not copied in. `staging.osm_relation` stays the
source of truth for ref/name/network/osmc:symbol and the views do the join - the same
argument that keeps edge tags inside `tags` instead of promoting them to columns.

Direction is not resolved either. A member way can be walked backwards along the route, and
`member_index` + `piece_index` state the ORDER without claiming the heading. Resolving it is
route assembly's job (`pipeline/docs/metadata-rules.md`, "on join"). Store provenance,
derive direction later - the rule everywhere else in `curated`.

### The link describes one build of the network, and says so

`edge_route` holds `edge_id`s, so it is true only of the network that produced them.
`build_network` replaces the network and `topology/repair` splits and deletes edges; both
now clear the table and print that they did, and `curate.routes --check` reports staleness
by comparing the network run ids recorded in `build_run` against the ones now in
`curated.edge`. The foreign key makes it impossible to forget: PostgreSQL refuses to
TRUNCATE `curated.edge` while `edge_route` references it.

That is `curated.vertex_degree`'s lesson from 2026-08-19 applied before it could be
repeated. A partly-stale link table would have been silent in exactly the same way.

### Two new judgement queues, both visible in QGIS

The join produced numbers no rule can decide, so they are layers rather than repairs:

- **27 routes match less than 20% of their member ways.** These are long-distance routes
  that only clip the two provinces: BI-12, the Ciclovia Pedemontana Alpina from Trieste to
  Savona, matches 2 of its 646 ways. A route generator must filter on `matched_fraction` -
  two matched ways under a famous name is a fragment, not a route.
- **131 routes come out in more than one piece** (`qa.v_route.pieces`, worst is 29). Some
  of that is coverage clipping at the bbox edge; some is a real gap in the network along a
  named route, which is a different defect from anything the topology rules can see - they
  look at loose ends, not at whether a route runs through.

Three layers: `qa.v_route` (752 lines, one per route - open this one first),
`qa.v_route_edge` (every edge that carries a route, route identity as real columns), and
`qa.v_route_coverage`. `route` also travels in the review bundle now, so the judgement can
happen off-machine.

### Where this leaves the pipeline

Of the six staged sources, two are now in use: OSM ways and the route relations. The DEM,
POIs, settlements and GTFS stops are still loaded and unread. The order in
`Oscar_continua_desde_aqui.md` section 8 is unchanged - the 164 overlaps are still the only
open QA queue, and elevation is the next join, because everything about difficulty needs it.

Pipeline suite: 62 tests, 11 of them new and all pure - the expansion of a relation's
members into links is per-feature branching (member types to skip, an empty role, a way
listed twice, a member way the network does not hold), so it lives in Python where a test
can pin it, and each case in the test file was measured against the real 752 first.

## 2026-08-20 (later) - Height on the network, and a view that was quietly wrong

225 Copernicus GLO-30 tiles had been loaded on 2026-08-19 and read by nothing.
`pipeline/curate/elevation.py` samples them onto the network: an elevation on every vertex,
and on every edge an altitude profile with one value per geometry point.

80,056 vertices carry a height (192 to 2,396 m); 101,951 edges carry a profile and 101,876
carry ascent and descent. **592,685 m up, 555,837 m down** across 9,238 km. The run takes
about five minutes.

### Both defaults were wrong, and both were measured rather than argued

**Bilinear, not nearest-neighbour.** OSM points sit a median 9.4 m apart and the DEM cell is
30 m, so the network is sampled three times finer than the raster it reads.
Nearest-neighbour therefore returns the same cell value several points running and then
jumps a whole cell:

| sampling | median abs dz | p90 abs dz | pairs implying >100% slope | ascent over the sample |
|---|---|---|---|---|
| nearest | 0.024 m | 8.61 m | 1,926 (5.83%) | 42,014 m |
| bilinear | 1.049 m | 4.13 m | 38 (0.12%) | 28,610 m |

A median of 24 mm punctuated by 8 m steps is a staircase, not a hillside. Summing the
positive part of a staircase invents **47% of the climb**. Same family as the ST_Dimension
trap from #13: plausible, cheap, and wrong.

**No noise threshold.** The obvious next move is to discard small dz as DEM noise. Binning
the same pairs by point spacing says not to: median abs dz runs 0.12 / 0.50 / 0.93 / 1.46 /
2.09 m across the 0-2, 2-5, 5-10, 10-20 and 20-30 m bands, scaling with distance and never
plateauing, while the median implied slope holds at 9-14% throughout - which is what a
mountain path is. There is no noise floor to subtract, so a threshold would only delete
real terrain. Under nearest-neighbour the same table is bimodal; that bimodality WAS the
artefact, and bilinear removed it at the source instead of filtering it afterwards.

### Judge the DEM on saddles, never on peaks

Against the OSM `ele` tag: saddles n=160, median error **4.1 m**; peaks n=385, mean bias
**-23.3 m**. A 30 m cell averages a summit with the slopes falling away from it, so sharp
convex features read low by design. Trails run on slopes, so the saddle figure is the one
that describes this network - but a peak's height must come from its `ele` tag, never from
the DEM.

Two checks that the climb is not merely self-consistent. Sentiero 33, Pasturo to Grignone,
reads 9.27 km and **1,827 m of ascent, 649 to 2,393 m**; Pasturo sits at ~640 m and the
Grignone summit is 2,410 m, so the real gain is ~1,770 m against a measured net of 1,744.
And the steepest edges in the network, found purely from the raster, are Ferrata Maurizio,
Canalone Belasa, Canale dei Camosci and Cresta OSA - every one already tagged
`sac_scale=alpine_hiking` or harder by a mapper who never saw this DEM.

### What is stored, and the rule for a gap

The profile is kept, not just its summary, because metadata-rules.md requires a route's
ascent to come from the altitude profile - so the profile has to survive assembly.
`profile_m` is aligned to `geom` point by point and the sampler refuses to write climb if
the lengths disagree. `ascent_m` / `descent_m` are **directional** in the same sense as
`oneway` and `incline`: reversing a piece swaps them.

**A gap makes the climb unknown, not smaller.** 57 vertices sit north of 46.0001 where the
single GLO-30 tile ends - the loader keeps a whole way that touches a region bbox, so ways
spill past it - and the 75 edges touching that band (56.8 km) get NULL ascent rather than
the climb of the covered part. Fetching tile N46 E009 closes it.

### A view from this morning was quietly double-counting

`qa.v_route`, written earlier the same day, summed edge length per LINK. `curated.edge_route`
is keyed on (edge_id, rel_id, member_index) precisely so a way listed twice in one relation
keeps both visits, and that grain is right for the link and wrong for any aggregate over
edges. 123 links across 20 relations resolve to an edge already counted, so the Dorsale
Orobica Lecchese reported 44.17 km against an actual 41.13.

The tell is worth keeping: the edge COUNT beside it was right, because it was already
`count(DISTINCT edge_id)`. A view can be half-correct in a way that looks entirely correct.
`sql/0009` collapses the join through `SELECT DISTINCT rel_id, edge_id` before aggregating,
in both that view and the coverage one.

### Where this leaves the pipeline

Three of the six staged sources are now in use: OSM ways, the route relations, the DEM.
POIs, settlements and GTFS stops remain loaded and unread, and they are the same shape of
job as each other - nearest vertex within a threshold - which is the next step. The 164
overlaps are still the only open QA queue.

Pipeline suite: 71 tests, 9 of them new. Sampling is one statement over a whole table so
PostGIS does it; turning a profile into ascent and descent is per-feature branching over
missing samples, so Python does it, in `curate/profile.py`, where the tests pin the rule.

## 2026-08-20 (third) - Places on the network, and the staging shelf is empty

POIs, settlements and Trenord stops snapped to the routing graph: `curated.place`, 12,476
rows, **8,258 of which can begin a walk**, on 6,112 distinct vertices. With this, all six
staged sources are read by something. Nothing is left sitting in staging.

| source | snapped | can start a walk | p50 | p90 | max |
|---|---|---|---|---|---|
| poi | 10,422 | 7,520 | 7.6 m | 52.9 m | 1,124 m |
| settlement | 2,037 | 721 | 12.0 m | 68.0 m | 417 m |
| gtfs_stop | 17 | 17 | 11.5 m | 20.6 m | 31 m |

### No threshold, and that is the decision

Nothing is dropped for being far; `distance_m` is stored and consumers filter on it. How
close a car park must be to count as a trailhead is a product decision, and a build step
that silently discards the ones past 50 m has made that decision where nobody can see it.
docs/route-pipeline.md settled the same argument for the off-road score - descriptive, not
a filter - and it holds here. It also means there is no tolerance to justify from a
histogram, because there is no tolerance.

### A far snap is usually not a bad snap

Every hut is within 88 m of a path and every car park within 143 m, which is what those
things are. Peaks are the outlier and correctly so: p50 55 m, p90 320 m, and Corna del
Colonnello at 1,124 m because no path goes there. For a summit, `distance_m` is the column
that separates a walk from a scramble - a coverage fact like the 370 islands, not a defect.

### Nearest vertex, not nearest edge

A place is attached so a route can START there, and a route starts at a routing vertex.
Which places a route PASSES is deliberately not answered here: metadata-rules.md settles it
at assembly, positioning each POI along the MERGED line with ST_LineLocatePoint.
Precomputing a place-to-edge table would answer it with a radius nobody chose, and it is
not small - 66,572 pairs at 25 m, 116,855 at 50 m.

### Two indexes on the same geometry, both earning their space

0004 added `::geography` indexes so ST_DWithin could work in metres. Those serve a RANGE
predicate well and a NEAREST NEIGHBOUR over polygons badly: 7,471 car parks resolve in
2.6 s through a new GiST index on ST_Transform(geom, 32632) and did not finish in four
minutes through a geography range join. So the search is planar and the stored distance is
geodesic - the same true-metres measure as every qa.finding, because one number in the
store meaning "metres in UTM" while its neighbour means "metres on the ellipsoid" is a trap
laid for later.

Polygons are measured whole - 7,280 of 7,471 car parks, 376 of 377 lakes, 66 of 74 huts are
areas - so a car park 60 m across that touches a lane is 0 m from the network, not 30.
`place.geom` is ST_PointOnSurface, a marker for drawing only; a centroid would fall outside
a C-shaped car park.

### Verdicts are recorded, not applied

`is_start` and `start_note` come from `curate/anchors.py`, in the same shape as
`load/legality.py`: a verdict plus the reason it went that way, so a rejection is auditable
instead of invisible. 1,179 "a chapel is passed, not started from", 999 "a residential area
is a polygon, not a point a walk begins at", 405 "a summit is a destination, not a
trailhead".

### What this leaves to judge

- **86 start vertices are not on the main component.** A trailhead on an island is a place
  you can begin and get nowhere - worth seeing before a route is generated from one.
  `qa.v_start` carries `component_id`, so the filter is a comparison.
- **33 car parks sit over 100 m from the network**, which is either a missing access road
  in OSM or a polygon somewhere odd. That distinction is a look, not a rule.
- **Trailheads still have no names.** `qa.v_start.names` is whatever the anchors carry,
  which for car parks is usually nothing. route-pipeline.md recorded 37 of 266 named and
  called naming them unsolved; it still is.

`qa.v_place_link` is the layer for all of this - a line from each place to the vertex it
attached to. A wrong snap is invisible as a number and obvious as a line reaching across a
valley.

Pipeline suite: 95 tests, 24 of them new.

## 2026-08-20 (fourth) - The review bundle becomes the review surface

Oscar's loop is visual: open a layer, colour it by a category, see whether the step did
what it claimed. The layers did not support that. They carried raw measures - gradient as a
float, matched_fraction as a float, distance_m as a float - and every one of them needed a
QGIS expression and a hand-built class ramp before it showed anything. A class ramp built in
a dialog also lives in one .qgz on one machine rather than in the database that is the
product.

Three changes, and a rule so it does not decay.

### Every styled field has a category twin

steepness_class, difficulty_class, surface_class, route_class, access_class, profile_class,
continuity_class, climb_class, length_class, scope_class, coverage_class, distance_band,
role_class, reachability_class, naming_class. They live in the qa.v_* views, not in the
exporter, so a direct QGIS-to-PostGIS connection gets them too.

Boundaries come from the distributions already measured, not from round numbers: the
gradient bands are where the network actually falls (48.6% under 5%, 0.3% over 50%), the
place bands where the snap distributions separate.

The leading digit on every value is deliberate. QGIS sorts categories by value, so
"gentle / moderate / steep / very steep / flat" puts flat between gentle and moderate and
the ramp reads backwards. A digit fixes the order for every renderer and survives export to
GeoPackage, which an ordering defined in a style file does not.

One category earns its place on its own: difficulty_class has a "9 invalid tag" bucket and
12 edges land in it. Folding raw OSM junk into "ungraded" would have hidden it for good.

### The bundle README is generated, never written

review/README.md now comes out of live queries at export time: state, what is settled, what
is open with a count and a sentence each, every layer with the field to colour it by and the
field to sort by, and every field with its meaning and its full list of categories and
counts. The only hand-maintained part is what a field MEANS.

That last table is the useful one - it says what you will get when you press Classify
before you press it.

review/REVIEW.md stays the opposite and is still preserved across rebuilds: hand-written,
saying what is being asked of this round.

### The full network is in the bundle now, which reverses an earlier decision

It was left out while the bundle only had to explain nine gap findings. Once the layers grew
names, height and places, a review without the network underneath them is a review of marks
on white. One layer and not two: the elevation columns sit on network rather than in a second
copy of the same 101,951 geometries, which is the difference between a 65 MB file and a
99 MB one.

### A migration bug this uncovered, and it was the serious part

migrate.py replays the WHOLE chain every run, and its own docstring says replay is the normal
case, not an error. Adding a class column beside the measure it classifies inserts a column
in the MIDDLE of a view, which CREATE OR REPLACE VIEW refuses outright - and worse, once 0011
widened qa.v_elevation, the next replay ran the narrower definition from 0008 over it and
failed with "cannot drop columns from view". The store had become un-migratable from its own
history.

Every view in 0005-0011 is now DROP VIEW IF EXISTS + CREATE VIEW, which makes the chain
order-independent. The single exception is qa.latest_run: every rule view selects from it, so
a plain DROP fails on the dependency and a DROP CASCADE would take them all and rely on the
rest of the chain rebuilding them in the right order. Its column list is frozen at one column
for exactly that reason. Verified by running migrate.py three times in a row.

### The rule

CLAUDE.md now carries it, so it is not something anyone has to remember: refresh the bundle
after every step that changes the store, give every styled field a category twin, and drop
views rather than replacing them. A bundle that lags the database is worse than no bundle,
because it looks current.

## 2026-08-20 (fifth) - The product is the route document, not the database

A framing correction from Oscar, and it is the load-bearing kind.

CLAUDE.md said "the database is the product", which came out of a real fix: the backend used
to ingest OSM and derive its own geometry, and moving that upstream into PostGIS stopped two
tiers producing the same data differently. That part stands. But PostGIS is where the VALUE
accumulates, not what the project DELIVERS. What VaiVia hands downstream is a structured
JSON and a map, one per route.

So: **the route document is the product; PostGIS is the working store that holds the
value.** docs/route-document.md is the ratified statement of it.

### Everything else is a reader

The same inversion that made backend/ stop producing data now applies one level up. Neo4j
holds the document for graph and vector search, the API serves it, the frontend renders it,
user content will key to its id - and none of them redefines a route. A field a reader needs
goes IN the document, never into that reader, or two tiers describe a route differently
again.

Three consequences worth keeping:

- The document is **versioned** (schema_version), because readers outlive producers.
- The document is **self-contained**: attribution, licence and provenance travel inside it.
  The document IS the ODbL Produced Work, so a consumer rendering the geometry elsewhere
  cannot strip the obligation by accident. The schema REQUIRES a non-empty sources array and
  a test asserts that it does - otherwise the next producer omits it and nothing notices.
- Two runs of the same route produce **byte-identical** JSON. A diff means the data moved.

### 752 documents exist, and they are real

Emitted from the 752 OSM route relations - the routes that exist today. pipeline/draw/ will
generate its own and emits through the same module: a generated route is a different kind,
not a different document.

All 752 validate against the schema. 724 carry a full altitude profile. 11,541 place
references across them. Difficulty by the 5% rule: 384 mountain hiking, 133 hiking, 106
ungraded, 91 demanding mountain, 34 alpine, 3 demanding alpine, 1 difficult alpine.

214 carry a quality warning and are emitted anyway, because a route this network holds in
three pieces is still a real route and the reader deciding whether to show it needs to know
which one it is.

### Three rules carried in rather than reinvented

**Difficulty is the hardest grade covering at least 5% of the length**, never the max - the
rule backend/graph/graphhopper.py::_weighted_max already proved. **Surface is a distribution
kept whole** plus a dominant; untagged length is reported as unknown rather than
renormalised away. **Absent is not zero**: a route with any unprofiled edge reports ascent_m
null, not a partial sum.

And **duration is deliberately absent**. DIN 33466 rates the classic Grigna ascent at 10
hours against a guidebook 6-8, so the figure this codebase can compute today is one a user
would not trust. measures is a CLOSED object in the schema so a miscalibrated figure cannot
arrive by accident. An absent field invites the calibration; a wrong one ships.

### What a route passes is computed here, and that vindicates an earlier decision

metadata-rules.md puts POI positioning at assembly, against the MERGED line with
ST_LineLocatePoint - which is exactly why curated.place snaps to a vertex and there is
deliberately no precomputed place-to-edge table. That table would have answered "what does
this route pass" with a radius nobody chose, at 66,572 rows for 25 m. The one bound here is
100 m, which is where qa.distance_band already puts "near" (measured: median 7 places per
route, p90 34, against 12 and 59 at 250 m), and every place carries offset_m so a reader
wanting 30 m filters on it.

### The social layer, designed and not built

docs/social-layer.md: photos, comments and reactions as three Mongo collections keyed to
route.id, binaries in object storage rather than GridFS, reactions as documents rather than
a counter, status on everything instead of hard deletes, EXIF stripped on upload because a
photo taken at home carries the user's home coordinates.

The honest note is in there too: this is a THIRD datastore, and the Supabase Postgres
already running could hold all of it with RLS putting the ownership check in the database
rather than in application code. What earns Mongo its place is the expectation that these
shapes move. Worth revisiting when the feature is specified.

**And the requirement that lands NOW, before pipeline/draw/ is written: a route id must be
stable across rebuilds.** A comment keys to it. osm-relation-<id> already is; a generated
route's id must come from its geometry, never a sequence number, a run_id, or a vertex id -
build_network truncates and reassigns those. Finding that out after people had commented
would be expensive.

### Two bugs found on the way

**The review bundle would have deleted the product.** review_bundle.py rmtree'd the whole
review/ directory, which was safe while the bundle was the only thing in it and stopped
being safe the moment route documents landed in review/routes/. It now removes only the
files it owns.

**curated.place had no planar index.** 0010 added one to every table the snap READS and not
to the table it WRITES, because nothing read it yet. Emitting documents reads it once per
route, and without the index that was a sequential scan of 12,476 places with a
reprojection each. Same lesson 0004 already recorded: a predicate that reads naturally and
quietly cannot use an index. With it, plus materialising each route's merged line once
instead of rebuilding it in four queries per route, the run went from about 18 minutes to
2m45s.

Pipeline suite: 122 tests, 27 of them new.

## 2026-08-20 (seventh) - Routes are generated: pipeline/draw/ exists

Branch feat/pipeline-draw, on top of the metadata branch. The step everything else this
week existed for: anchor x distance x seed -> pgRouting draws a loop over our own edges ->
assembly along the walked sequence -> score -> keep the best distinct few. First
catalogue: **72 distinct routes from 12 car-free starts** (stations and Trenord stops) in
139 seconds, 93% true loops (retrace <= 10%), every one with a full altitude profile, 20
MTB-rideable. All 72 emitted as route documents that validate against the schema, beside
the OSM ones in review/routes/.

### The two rules that were waiting for this, landed

**The route id derives from the ground** (draw/route_id.py): coordinates rounded to 5
decimals (~1.1 m), direction-normalised, hashed. A weld moving an endpoint 40 cm cannot
rename a route - a comment would orphan (docs/social-layer.md) - while a real reroute is a
NEW route. Never a sequence number, a run_id, or a vertex id; vertex ids do not survive a
rebuild.

**Direction is explicit per walked edge.** pgr_dijkstra's node tells which way each edge
is entered; route_edge.forward stores it; a reversed edge SWAPS ascent and descent and
reverses its profile - the oneway/incline inversion from metadata-rules.md, finally
executing. The test that pins it is the one that catches a mountain loop reported as flat.

### The spike's caveat, repaired where it said it would be

The MTB verdict runs the access conjunction ALONG THE SEQUENCE - a route that walks a
forbidden edge is not a bike route, and there is no corridor for a 6 m crossing slip to
flip a verdict. Difficulty is the ratified >=5% rule over walked spans; climb is null if
any edge is unprofiled (absent is not zero); score is descriptive and never a filter,
with weights as parameters.

### Calibrated on first data, same discipline as the 2 m tolerance

The via-ring radius began at target/3.6 (equilateral arithmetic). The first catalogue
measured median actual/target at 1.43 (5 km asks at 1.62), so the constant is now
target/5.0 - medians 1.05-1.25 after. Loop legs soft-penalise already-walked edges (x3
cost) rather than excluding them: sometimes the same valley is the only way home, and
retrace_share reports it instead of hiding it.

### Honest numbers from the first catalogue

42 of 72 routes are under 30% off-road, because the 12 starts are car-free by design and
stations sit on valley floors. That is a start-selection fact, not a generation defect:
the next catalogue mixes in high-anchor car parks. qa.v_draw carries offroad_class /
climb_class / shape_class / mtb_class, is in the review bundle, and the emitters now each
delete only the files they own (the review_bundle lesson, applied before it bit again).

21 new pure tests (143 in the pipeline suite). Schema 0013; build_network and repair
clear the catalogue like every derived table.

## 2026-08-20 (eighth) - The exigent join, and bike loops by construction

Owner feedback on the first catalogue: the loops "appear not joined", and when segment
data disagrees the route should take the MOST DEMANDING value - a loop with one segment
not for bikes is not for bikes; and a variation sharing most of those segments could be
bike-friendly if all of its own are.

First, the diagnosis. The loops ARE geometrically joined - 12,054 edge transitions,
zero gaps over 0.5 m, every loop closes to 0.0 m - and my first check flagged nothing but
normal OSM point spacing. What looked un-joined was the DATA, and the owner was right
about it twice over: the median route carries a SAC grade on only 16% of its metres, so
the >=5% character rule read "ungraded" on routes visibly containing graded trail; and 52
routes said mtb=false with the reason hidden - one was blocked by 6 metres of steps and
read identically to one blocked by 1.6 km.

### The rule, applied: character and exigent, both carried

Every route (and every route document, schema 1.1) now carries sac_scale (CHARACTER: the
>=5% rule, the label a route wears) AND sac_max (EXIGENT: hardest graded metre walked,
any length - what you must be able to handle), plus graded_share so sparse grading reads
as a mapping fact rather than a failed join, and bike_blocked_m so a "no" says why in
metres. The rule lives once, in export/document.py, so OSM routes carry it identically.
qa.v_draw's difficulty_class now colours by the exigent grade; the character stays as a
column. Measured where the two diverge: 16 foot routes, including 8 "ungraded" whose
exigent grade is real.

### The second half, made mechanism: --activity mtb

"Another loop that shares many of the segments could be bike friendly if all the segments
are" is not a filter - it is a CONSTRUCTION. The mtb catalogue is drawn over
routable_bike edges only, so the router detours around forbidden segments instead of a
verdict flagging them afterwards. Second catalogue: 54 mtb loops, all 54 at 0 blocked
metres. And the owner's exact observation held in the data: 18 mtb asks produced the same
ground as an already-legal foot loop and FOLDED INTO IT by the geometry-derived id - a
fully bike-legal foot loop IS the mtb loop over that ground, and the id knew.

Catalogues are replaced per activity now (foot and mtb are siblings; regenerating one
must not delete the other). 878 route documents on disk - 752 OSM + 126 generated - all
valid against schema 1.1.

Also this session: PR #16 had merged into #15's branch rather than develop (GitHub only
retargets a stacked PR when its base branch is deleted); #17 landed it mechanically.

145 tests in the pipeline suite.

## 2026-08-21 - Destination routes: out to somewhere worth going, and back

Owner (before the Neo4j work): routes are not just loops - some go from a start to an
INTERESTING POI (views, a peak...) and come back. The anchors module knew half of this
("a summit is a destination, not a trailhead"); draw/ only drew loops. Now
`--shape destination` draws the other kind, and the catalogue replaces per
(activity, shape) so the four families coexist.

The mechanism: for each start x target, rank the reachable interesting places
(draw/destinations.py - v0 weights as parameters, peaks and viewpoints on top, heavy
named bonus, springs and picnic sites are waypoints not destinations), route out to the
best few, route back with the out leg soft-penalised. Where the ground allows, the return
comes home a different way - measured retrace on the named foot routes: 0-4%. Where the
valley allows one way, it honestly retraces and shape_class says so.

The crow-flies band is deliberately generous (measured wander spans 1.4-3.3 in these
mountains, so no band can promise length); the score's length-fit term judges the actual
routed distance. Selection requires main-component reachability and the POI within 100 m
of the network - structural facts, not judgement.

**Generated routes have names now.** Trailhead naming is unsolved, but destination naming
is free: 97 of 102 destination routes are named - "To Corno dell'Arco, 11.0 km, 1050 m
up, sac_max T3, retrace 2%" reads like a guidebook line, and "To Rifugio Elisa" is an
answer where "generated-9f2c1ab4" is not. The name travels into the route document
(identity.name, identity.to, and a generation.destination block).

Catalogue after this session: 228 generated routes - 72 foot loops, 54 mtb loops (legal
by construction), 59 foot destination routes, 43 mtb destination routes - plus the 752
OSM relations. All 228 generated documents valid against schema 1.1. The draw layer in
the bundle carries route_shape_class beside the existing classes.

One number worth noticing: destination routes run visibly wilder than loops (off-road
39-48% against the loops' 33% mean) - a destination pulls the route uphill out of the
valley towns the car-free starts sit in.

153 tests in the pipeline suite. Next: the Neo4j export of the full catalogue - the
inversion the pipeline exists for.

## 2026-08-21 (later) - The inversion: the catalogue is in Neo4j

pipeline/export/neo4j_load.py reads the 980 route documents and loads what selection
needs into the compose Neo4j: 980 :Route (752 OSM + 228 generated, 370 named), 8,156
:Place, 29,456 PASSES, 276 :Start. It replaced the 250 orphan :Route nodes left by the
2026-08-18 backend catalogue, whose branch never merged and which develop's backend never
queried.

The design holds the document canonical: identity, measures, both difficulty grades, the
MTB verdict and the route-place relationships travel; geometry and the profile
deliberately do NOT - they are fetched from the document by route_id, because a second
home for geometry is how two truths start. The export owns :Route/:Place/:Start and
replaces them wholesale; the backend's Trail/Segment/Intersection graph is untouched.
Cypher is named templates in export/catalogue.cypher run with parameters only - the
backend/graph discipline applied pipeline-side.

The smoke test at the end of every export is the product's own query shape - clean routes
8-16 km passing a peak - and it earned its warnings filter honestly: the first run
without it surfaced 0.0 km OSM fragments wearing famous names, top of the list. Any
consumer of this catalogue must filter on the quality block; the query now demonstrates
how, and answers "To Corno dell'Arco, 11 km, up 1050, T3, passes Corno dell'Arco."

On the way: place coordinates joined the documents (schema 1.1 places now carry lon/lat -
the spike had them, the emitters did not, and :Place nodes need to sit on a map), all 980
documents re-emitted and re-validated; env_value() restored into core (it had lived only
on the spike branch).

7 new tests (160 in the pipeline suite). The next step is backend work: point
queries.cypher templates at :Route/:Place so /chat selects from this catalogue instead of
computing over Trail/Segment.

<!-- pmctl:handoff v1 -->
```json
{
  "project": "VaiVia",
  "org": "ai safe earth",
  "status": "amber",
  "updated": "2026-08-21",
  "deadline": null,
  "people": [
    "oscar"
  ],
  "plans": [
    {
      "name": "redesign",
      "path": "docs/",
      "status": "active"
    }
  ],
  "phases": [
    {
      "name": "Phase 0 - Foundations",
      "status": "done",
      "start": "2026-08-15",
      "end": "2026-08-15",
      "plan": "redesign",
      "decisions": [
        {
          "date": "2026-08-15",
          "text": "Fastify gateway is the only public ingress; backend and Neo4j stay internal and trust a shared-secret hop"
        },
        {
          "date": "2026-08-15",
          "text": "Monorepo restructured in place: backend/, gateway/, frontend/, infra/"
        },
        {
          "date": "2026-08-15",
          "text": "uv with pyproject.toml instead of pip and requirements.txt"
        },
        {
          "date": "2026-08-15",
          "text": "Neo4j Community rather than Enterprise, which needs a paid license"
        },
        {
          "date": "2026-08-15",
          "text": "Supabase supplies both auth and the Postgres store for history, ledger and quotas"
        },
        {
          "date": "2026-08-15",
          "text": "SSE streaming end to end from day one"
        },
        {
          "date": "2026-08-15",
          "text": "Beta data scope limited to the Lake Como and Lecco bbox"
        }
      ]
    },
    {
      "name": "Phase 1 - Graph core and ingestion",
      "status": "done",
      "start": "2026-08-15",
      "end": "2026-08-15",
      "plan": "redesign",
      "decisions": [
        {
          "date": "2026-08-15",
          "text": "Routing graph is Intersection to Intersection; segments carry edge data and are not routing vertices"
        },
        {
          "date": "2026-08-15",
          "text": "MAPS_TO dropped as redundant; one ordered COMPOSED_OF with seq and match_confidence"
        },
        {
          "date": "2026-08-15",
          "text": "All distances in metres and durations in minutes, converted only for display"
        },
        {
          "date": "2026-08-15",
          "text": "Ontology extended by the owner: difficulty label plus numeric level plus free-text notes, per-activity durations, elevation gain and loss at trail, segment and per-direction edge, seasonality lists, landscape_description feeding the embedding"
        },
        {
          "date": "2026-08-15",
          "text": "Hiking duration follows DIN 33466; MTB uses speed by difficulty plus a climbing penalty, documented as recalibratable"
        }
      ]
    },
    {
      "name": "Phase 2 - Query service",
      "status": "done",
      "start": "2026-08-15",
      "end": "2026-08-15",
      "plan": "redesign",
      "decisions": [
        {
          "date": "2026-08-15",
          "text": "Named Cypher template library rather than inline query strings, so the LLM boundary is enforceable by construction"
        },
        {
          "date": "2026-08-15",
          "text": "Guard tests fail the build if a template mutates data, traverses semantic edges in a path, or leaves a traversal unbounded"
        },
        {
          "date": "2026-08-15",
          "text": "GDS Dijkstra templates written but not wired to the endpoint until they can be verified against a live GDS instance"
        }
      ]
    },
    {
      "name": "Phase 3 - Gateway",
      "status": "done",
      "start": "2026-08-15",
      "end": "2026-08-15",
      "plan": "redesign",
      "decisions": [
        {
          "date": "2026-08-15",
          "text": "Pipeline ordered identify then rate limit then authenticate, so limits key on the verified user and unauthenticated floods are still IP-counted instead of escaping on an early 401"
        },
        {
          "date": "2026-08-15",
          "text": "Quota checks fail open on a Postgres error: a database blip degrades cost control, not availability"
        }
      ]
    },
    {
      "name": "Phase 4 - Chat orchestration",
      "status": "done",
      "start": "2026-08-15",
      "end": "2026-08-15",
      "plan": "redesign",
      "decisions": [
        {
          "date": "2026-08-15",
          "text": "The model returns only a validated intent; Python maps intent to a read-only template, so no field can carry a query, template name or identifier"
        },
        {
          "date": "2026-08-15",
          "text": "OpenAI strict structured outputs reject oneOf and discriminator, so to_strict_schema rewrites the tagged union to anyOf"
        },
        {
          "date": "2026-08-15",
          "text": "Quota enforced in the orchestrator as well as the gateway, since the orchestrator is the authoritative point before spending"
        }
      ]
    },
    {
      "name": "Phase 5 - Frontend",
      "status": "done",
      "start": "2026-08-15",
      "end": "2026-08-15",
      "plan": "redesign",
      "decisions": [
        {
          "date": "2026-08-15",
          "text": "The gateway client is the app's only network surface; no path exists to backend, Neo4j or OpenAI"
        },
        {
          "date": "2026-08-15",
          "text": "Incremental SSE parser holding a remainder across chunks, since a network chunk can split a frame anywhere"
        },
        {
          "date": "2026-08-15",
          "text": "Map draws geometry from the same segments the answer was grounded in, so prose and map cannot disagree"
        },
        {
          "date": "2026-08-15",
          "text": "Sign-in page deferred rather than built speculatively against a Supabase project that does not exist"
        }
      ]
    },
    {
      "name": "Phase 6 - Beta hardening",
      "status": "active",
      "start": "2026-08-16",
      "end": null,
      "plan": "redesign",
      "decisions": [
        {
          "date": "2026-08-16",
          "text": "Claude Code spend is attributed per commit by bucketing transcript usage into commit time intervals, deduped on requestId because one request writes several assistant records that each repeat the same usage object"
        },
        {
          "date": "2026-08-16",
          "text": "DATABASE_URL points at the Supavisor pooler in session mode because the direct host db.<ref>.supabase.co publishes an AAAA record only and does not resolve without IPv6"
        },
        {
          "date": "2026-08-16",
          "text": "Gateway selects TLS in quotaStore.ts for any non-local host rather than via sslmode in the URL: node-postgres sends no SSLRequest by default so a bare connection string is silently plaintext, while sslmode=require is aliased to verify-full by the bundled pg and fails against Supabase's pooler certificate"
        },
        {
          "date": "2026-08-16",
          "text": "Migrations are applied by scripts/apply_migrations.py rather than the Supabase CLI, which expects its own supabase/migrations layout; every migration must be idempotent, so the RLS policies now drop-if-exists first"
        },
        {
          "date": "2026-08-16",
          "text": "Backend tests pin gateway_shared_secret and database_url to empty via an autouse fixture; they previously inherited the developer's .env, so a populated secret 401'd 23 tests and a populated DATABASE_URL opened a real Postgres pool during unit tests"
        },
        {
          "date": "2026-08-16",
          "text": "Gateway builds from tsconfig.build.json with rootDir src: the base config includes test/ for typecheck, which made tsc emit dist/src/... so npm start could not find dist/server.js and the built artifact was unstartable"
        },
        {
          "date": "2026-08-16",
          "text": "Gateway dev and start load gateway/.env via node --env-file-if-exists rather than a dotenv dependency; nothing read that file before, so its Supabase settings were inert"
        },
        {
          "date": "2026-08-16",
          "text": "The auth plugin throws a named error when SUPABASE_JWT_JWKS_URL is empty; config only requires it in production, so outside production the empty string reached new URL and killed boot with a bare ERR_INVALID_URL"
        },
        {
          "date": "2026-08-16",
          "text": "run_cypher_file strips // comments before splitting on semicolons; splitting first cut comments containing semicolons in half and executed the tail as Cypher, which is why applying the schema to a real database failed on 'durations are MINUTES.'"
        },
        {
          "date": "2026-08-16",
          "text": "The Overpass client sends a descriptive User-Agent, overridable via OVERPASS_USER_AGENT; Overpass answers the default python-httpx UA with 406, which is not retryable, so live OSM ingestion could never have worked"
        },
        {
          "date": "2026-08-16",
          "text": "Compose host ports for Neo4j are variables defaulting to 7474/7687, because an older copy of this project already binds those on the dev machine and starts with Docker Desktop"
        },
        {
          "date": "2026-08-16",
          "text": "Compose uses the Neo4j 5 server.memory.* setting names; the dbms.memory.* forms worked but warned on every boot"
        },
        {
          "date": "2026-08-16",
          "text": "The fixture's trail geometry is generated from the ingested graph by scripts/make_trailforks_fixture.py rather than hand-written, so it always traces real ways and spatial matching is exercised offline; metadata is preserved because tests pin it"
        },
        {
          "date": "2026-08-16",
          "text": "/routes prefers GDS Dijkstra over a per-request bbox projection with a unique name dropped in finally, and falls back to shortestPath when GDS is unavailable, because the GDS plugin silently skips installation when its network fetch fails at container start"
        },
        {
          "date": "2026-08-16",
          "text": "GDS streams node ids only, so the route_edge_details template maps consecutive node pairs back onto CONNECTS_TO to recover gain, surfaces and way ids; parallel edges resolve to the shortest, matching what Dijkstra weighted by"
        },
        {
          "date": "2026-08-16",
          "text": "The browser reads conversations and messages directly from Supabase under the migration's select-only RLS policies (auth.uid() = user_id); this is what those policies were written for and does not breach the gateway-only rule, which guards backend, Neo4j and OpenAI. Writes still go only through the backend"
        },
        {
          "date": "2026-08-16",
          "text": "Switching conversations remounts ChatPanel via a React key instead of syncing state with effects, so no message or stream state can leak across conversations"
        },
        {
          "date": "2026-08-16",
          "text": "The panel remount key changes only on explicit navigation, never when a fresh chat's first turn is assigned a conversation id: keying on selected remounted the panel mid-stream and destroyed the arriving answer, a bug the manual browser pass missed and the first scripted e2e run caught"
        },
        {
          "date": "2026-08-16",
          "text": "The e2e suite reads credentials from E2E_EMAIL/E2E_PASSWORD and skips when unset so CI stays offline; the live OpenAI turn is additionally gated behind E2E_LIVE=1"
        },
        {
          "date": "2026-08-16",
          "text": "The gateway pins token iss to <project-url>/auth/v1 and aud to authenticated whenever SUPABASE_URL is configured; unset leaves behaviour unchanged for dev without Supabase"
        },
        {
          "date": "2026-08-16",
          "text": "Semantic search embeds the user's text server-side and passes the vector as a query parameter, so free text never approaches Cypher; the endpoint returns 503 while the vector index is unpopulated rather than an empty list"
        },
        {
          "date": "2026-08-16",
          "text": "The embedding job stores a sha256 of the owner-ratified input text on each Trail and skips unchanged trails on re-run, making it idempotent and safe to run after every ingestion; vectors are written with db.create.setNodeVectorProperty so the index sees a typed vector"
        },
        {
          "date": "2026-08-16",
          "text": "Chat decomposes each message into atomic subqueries (trail_search, semantic_theme, route, clarify) and a Python composer merges them tightest-wins onto templates; one clarify anywhere poisons the whole plan so a half-adversarial decomposition never half-runs"
        },
        {
          "date": "2026-08-16",
          "text": "The composer nullifies non-positive bounds: under strict structured outputs the model occasionally writes 0 to mean no-limit, and a 0-metre max silently filters out every trail (found by the golden eval, g09)"
        },
        {
          "date": "2026-08-16",
          "text": "Semantic themes compose with structured filters in one template (vector candidate pool then NULL-idiom WHERE); while the index is unpopulated chat degrades to structured search and flags semantic_unavailable instead of 503ing the turn"
        },
        {
          "date": "2026-08-16",
          "text": "trailforks_url is stored only when the source record names it (alias or explicit URL) — never guessed from an id; mock fixture aliases are synthetic so their links 404 until real Trailforks data lands"
        },
        {
          "date": "2026-08-16",
          "text": "Golden eval (scripts/eval_golden.py) scores decomposition and retrieval separately so a failure names its layer; retrieval misses are all POI-coverage gaps in the mock graph, not pipeline bugs"
        },
        {
          "date": "2026-08-16",
          "text": "Trail-level NEAR_POI proximity edges (500 m, computed at ingestion with delete-then-recreate) complement segment-level PASSES_BY; 500 m because area features ingest as one node — the lake's node sits ~400 m off its own shoreline path"
        },
        {
          "date": "2026-08-16",
          "text": "Fixture trail walks anchor at the intersection nearest a lake/hut POI so the traced geometry passes the features its prose describes; owner declined season-scoped hazards for now"
        },
        {
          "date": "2026-08-16",
          "text": "POI name resolution goes Lucene full-text first with Python-side escaping (core/text.py), CONTAINS as fallback; CALL subqueries modernized to the CALL (t) scope-clause form after live deprecation warnings"
        },
        {
          "date": "2026-08-16",
          "text": "Embedding input extended (owner-ratified) with activity/difficulty, best seasons, and POIs along the way; the sha gate re-embedded only changed trails"
        },
        {
          "date": "2026-08-16",
          "text": "Grouping variables cannot appear inside an aggregation expression in one WITH (direct + collect(x) is a syntax error live); offline FakeDb cannot catch Cypher syntax, only the live run did"
        },
        {
          "date": "2026-08-16",
          "text": "Hazards are season-scoped (hazards_spring/summer/autumn/winter; seasonal_hazards stays the union for display); a hazard filter with a season checks that season only, and unscoped source records put the union in every season as the conservative reading"
        },
        {
          "date": "2026-08-16",
          "text": "Coverage is multi-region via the REGIONS setting (Lecco, Bergamo); Bergamo's bbox starts at the city and runs north into the hills so the plains' road grid stays out of the graph; trail-region links recompute from geometry each run, deleted first so a moved trail drops its stale region"
        },
        {
          "date": "2026-08-16",
          "text": "Fixture anchors carry an optional near-point: with Bergamo data present, a type-only 'nearest hut' anchor silently relocated the Lecco traverse onto a Bergamo bivouac, so anchors that mean a specific area must say so"
        },
        {
          "date": "2026-08-17",
          "text": "Project renamed get-out-door to VaiVia: remote is github.com/ai-safe-earth/VaiVia.git, packages are vaivia / vaivia-gateway / vaivia-frontend, container is vaivia-neo4j. The compose volumes keep their names so the ingested graph survives the rename; only the container is recreated"
        },
        {
          "date": "2026-08-17",
          "text": "Renaming the root folder invalidates every console-script shim in backend/.venv, because Windows .exe launchers hardcode the absolute interpreter path; uv run black failed with 'Failed to canonicalize script path' until .venv was deleted and uv sync re-run"
        },
        {
          "date": "2026-08-17",
          "text": "@fastify/http-proxy upgraded 10 to 11.6.0 for GHSA-gwhp-pf74-vj37 (Connection-header abuse strips proxy-added headers, which is exactly how the gateway injects x-gateway-secret and x-user-id). Impact was bounded because both consumers fail closed: the trust middleware 401s on a bad secret and /chat 401s on an empty x-user-id, so the attack denied the caller's own request rather than forging identity"
        },
        {
          "date": "2026-08-17",
          "text": "next 14 to 16 deferred rather than taken as an audit fix: postcss and sharp are reachable only through next, the CSS is authored in-repo rather than attacker-supplied, and nothing imports next/image, so a two-major framework migration is not justified by these advisories"
        },
        {
          "date": "2026-08-17",
          "text": "chore/dep-audit merged to main on a manual browser verification of the SSE-proxied chat turn rather than the Playwright suite, since credentials for the automated run were not available in-session; sign-in, streaming and the map all worked through the new @fastify/http-proxy major"
        },
        {
          "date": "2026-08-17",
          "text": "An unstated activity was silently over-constraining every search: under strict structured outputs the model must fill the field, and it reached for 'mixed' to mean 'no preference'. The template already matches 'mixed' trails against any activity, so a 'mixed' filter is strictly narrower than null and often returned nothing. Fixed in the prompt and, independently, by mapping it to None in composer.sanitize so the boundary does not depend on model compliance. Live golden retrieval 16/21 to 18/21"
        },
        {
          "date": "2026-08-17",
          "text": "OSM attribution now credits the data, not just the tiles: the map control links to openstreetmap.org/copyright and the ODbL and renders expanded rather than behind the compact toggle, and a persistent footer carries the credit in the chat column. The footer exists because OSM-derived facts reach users through the written answers too, so a map-only credit would miss anyone who never opens the map"
        },
        {
          "date": "2026-08-17",
          "text": "Trailforks licensing re-triaged from low to high after reading the primary sources: use is API-only with a granted key, and the Outside ToU restricts the Services to personal noncommercial use while separately naming software development and AI use as needing prior written consent. Nothing has ever been fetched (fetch_live is a stub, the fixture is synthetic), so the position is clean and the choice is consent-or-OSM-only. docs/fragilities.md #4 and docs/data-sources.md were also corrected: both described live-API backoff, bbox chunking and a response cache that do not exist"
        },
        {
          "date": "2026-08-18",
          "text": "Trailforks abandoned as a data source rather than deferred: API-only with a discretionary key, and the Outside terms require prior written consent for commercial, in-software and AI use. OSM measured as sufficient instead - 302 named CAI sentieri, sac_scale on 33-43% of paths, mtb:scale on 23-27%"
        },
        {
          "date": "2026-08-18",
          "text": "The ingestion filter must include connective road ways. Trail-only ingestion shattered Lecco into 1,627 components with the largest at 31.7%, because paths connect through lanes; widening it gives 171 components at 98.1% and loop generation goes from 0/10 to 10/10. motorway/trunk/primary stay excluded"
        },
        {
          "date": "2026-08-18",
          "text": "Routing weights cost_m (distance x per-highway/surface penalty) rather than raw distance, because roads are straighter and a distance-optimal trail loop came back ~83% asphalt. Consequence: GDS totalCost is a penalised figure in no real unit, so every distance shown to a user must be summed from distance_m"
        },
        {
          "date": "2026-08-18",
          "text": "Untagged surface is deliberately NOT penalised in the comfort model: ~38% of paths lack the tag and are disproportionately the small trails the app exists to find, so penalising unknown would turn a mapping gap into a routing preference against them"
        },
        {
          "date": "2026-08-18",
          "text": "Adopt GraphHopper for geometry, keep Neo4j for meaning. With our comfort model ported, off-road is 67.0% and 67.7% at 15 and 20 km against our 61.0 and 64.1, retrace 0.0-3.2% against ~20%, 30/30 candidates route, and climb comes back real where ours is silent. It also decodes sac_scale and mtb_rating natively. Decided, not yet migrated"
        },
        {
          "date": "2026-08-18",
          "text": "The route-to-graph join is spatial, not by osm_way_id, which GraphHopper does not expose. A spatial join answers what a route passes rather than which exact ways it traversed, and survives the engine splitting ways differently from our ingestion - the mismatch that orphaned 5,489 edges"
        },
        {
          "date": "2026-08-18",
          "text": "core/geo.min_distance_to_polyline_m measures to vertices, not perpendicular, and reported a POI 7.8 m off a line as 556 m away. Added distance_to_polyline_m for engine output and left the vertex-based one untouched, because changing it would alter every PASSES_BY edge"
        },
        {
          "date": "2026-08-18",
          "text": "POIs are ingested in two roles: ANCHORS to start from (parking, station) and DESTINATIONS worth reaching (peak, saddle, chapel/ermita, beach, waterfall, castle). Parking is deliberately not exposed to chat, since nobody asks for a walk past a car park. Area POIs come from a second out-center statement; 53% of POIs are areas a nodes-only query never saw"
        },
        {
          "date": "2026-08-18",
          "text": "Trailheads are derived nodes rather than labels on Intersection, so re-running ingestion cannot clobber them. 1,511 car parks cluster to 266, each scored by off-road share within 750 m; the score is descriptive rather than a filter because what counts as enough trail is a product decision"
        },
        {
          "date": "2026-08-18",
          "text": "Wikipedia and Wikidata are a supplement, not a foundation: 48 real descriptions across 3,195 POIs. The Wikidata one-liners must NOT be embedded - at ~27 characters they are category labels that add noise and make a POI look described when it is not. Attribution is stored per POI so CC-BY-SA text can always be credited"
        },
        {
          "date": "2026-08-18",
          "text": "GATEWAY_DEV_NO_AUTH lets the app run with Supabase off, and loadConfig THROWS if it is set with NODE_ENV=production. A switch that disables authentication must not be one env var away from being live; failing to boot is the only refusal a misconfigured deploy cannot ignore"
        },
        {
          "date": "2026-08-18",
          "text": "Neo4j stops being the routing engine and becomes the catalogue a chat turn selects from. Generation, scoring, dedup and the POI map-back all run offline in scripts.build_routes, so a turn is a filter and an ORDER BY. Generation is bounded as trailheads x distances x keep, which makes catalogue size predictable and coverage auditable"
        },
        {
          "date": "2026-08-18",
          "text": "Route scoring is pure functions with tests (length 40 / off-road 30 / variety 20 / climb 10) because it encodes taste, and taste should be arguable in a test rather than buried in a script. Unknown climb scores neutral rather than zero, so a route is not punished for missing instrumentation; nothing is filtered inside the scorer, because good-enough-to-offer is a product decision"
        },
        {
          "date": "2026-08-18",
          "text": "loop_search is its own atomic intent rather than a flag on trail_search: a circular outing is a different ask from a named trail and from point-to-point directions. Its field set is pinned by a test so each addition is checked against the LLM boundary rule deliberately"
        },
        {
          "date": "2026-08-18",
          "text": "A single stated loop distance is a point estimate, not an interval. The model returns '15 km' as min=max=15000 and real routes are 15,771 m, so it matched nothing while 500 loops sat in the catalogue. widen_narrow_band fixes it in Python rather than as another prompt rule, the same reasoning as the 0-bound scrub"
        },
        {
          "date": "2026-08-18",
          "text": "GraphHopper runs as a real service for geometry and elevation. One config line (CGIAR SRTM) replaced the elevation backfill we never built, which is what blocked duration and difficulty; core/durations.py has implemented DIN 33466 all along and only ever needed ascent"
        },
        {
          "date": "2026-08-18",
          "text": "Activity is generated, not filtered: hike and mtb are separate GraphHopper profiles producing separate catalogues, mtb excluding steps outright rather than penalising them, because a foot loop over steps and a T4 scramble is impassable on a bike. Activity is part of the route id and CLEAR_ROUTES is activity-scoped so one rebuild cannot destroy the other"
        },
        {
          "date": "2026-08-18",
          "text": "Difficulty comes from GraphHopper decoding sac_scale to hike_rating and mtb:scale to mtb_rating. The rating stored is the hardest covering at least 5% of the route, since a plain max would let 30 m of scramble label a 20 km valley walk alpine. Our 1-4 level maps onto both scales but only the one matching the activity is applied"
        },
        {
          "date": "2026-08-18",
          "text": "build_routes declines to STORE a route whose length fit is poor, and reports the drops per target. round_trip.distance overshoots badly in steep terrain, so half of what was generated answered a different question than the one it was filed under. This is filtering at persistence, not in the scorer: mean score went 0.65 to 0.77"
        },
        {
          "date": "2026-08-18",
          "text": "Two confident diagnoses this session were wrong and are recorded in the docs rather than quietly fixed. A near-constant 113-121 m/km was read as SRTM noise and was a selection effect (the catalogue only holds mountain trailheads; flat starts give 1-40 m/km). And 'no 5 km routes survived' came from a truncated table; there are 44. Both were reading a filtered or truncated view as the whole"
        },
        {
          "date": "2026-08-18",
          "text": "A route is named after the most prominent POI it passes (peak, then saddle, lake, castle, waterfall, chapel), with no 'loop' suffix, owner's choice. 81% of routes get a name this way and no regeneration was needed, since the PASSES edges already carried name and type. Null stays null and the card shows distance: 'Route 4312828180' must never reach a user"
        },
        {
          "date": "2026-08-18",
          "text": "Loop geometry is served per route from GET /routes/{route_id}/geojson rather than inlined in the chat payload, because the same results dict is handed to the answer model and a few hundred coordinate pairs per route would put tens of kilobytes of numbers in the prompt every turn"
        },
        {
          "date": "2026-08-18",
          "text": "The map draws every returned loop and highlights the clicked one, styled data-driven on a `selected` feature property so switching selection is a restyle rather than a refetch. A trail (one feature, no such property) renders exactly as before"
        },
        {
          "date": "2026-08-18",
          "text": "Area POIs keep a sampled ~100-point boundary and an extent_m, and the map-back measures to the boundary for areas and to the point for nodes. A centroid cannot answer 'does this path run along the lake': Lago di Como's sits 5,122 m out on the water, and no radius fixes that without sweeping in half the region"
        },
        {
          "date": "2026-08-18",
          "text": "Routing ways and area POIs are told apart by TAGS, not by shape. With `out geom` a lake outline arrives with geometry and a node list exactly like a path, and having no highway tag it took the 'path' default and became routable -- 1,673 outlines entered the routing graph. A routing way must now have a highway tag and an area POI must not. The dangerous part was the default itself: tags.get('highway', 'path') turned a filter bug into routable water instead of failing loudly"
        },
        {
          "date": "2026-08-19",
          "text": "Repairs land as their own pass over the latest QA run findings, never a fresh scan, so what was reviewed in QGIS is what changes. Self-loops are split rather than deleted and sub-metre edges collapsed rather than deleted: a defect in the routing graph is not a defect in the ground. The check that a pass went right is that the total length of the network did not move."
        },
        {
          "date": "2026-08-19",
          "text": "qa.py refuses to run when curated.vertex_degree is stale. A matview left over from an earlier network made the same 101,870 edges report 9 dangle pairs and then 19, with nothing changed between - and repairs would have been chosen from those numbers. build_network now refreshes it, where 0004 always claimed it did."
        },
        {
          "date": "2026-08-20",
          "text": "Route-relation membership is a link table (curated.edge_route) keyed on (edge_id, rel_id, member_index), never a column on edge: 5,295 edges carry more than one route and 140 way-in-relation pairs repeat. The relation's tags stay in staging; direction is not resolved at the link, only order"
        },
        {
          "date": "2026-08-20",
          "text": "DEM sampling is bilinear with no noise threshold, both measured: nearest-neighbour invents 47% of the climb on this network because OSM points are 3x finer than the 30 m cell, and |dz| never plateaus as spacing falls so there is no noise floor to subtract"
        },
        {
          "date": "2026-08-20",
          "text": "DEM accuracy is judged on saddles (median error 4.1 m), never on peaks (-23.3 m mean bias): a 30 m cell reads sharp convex features low by design, so a peak's height comes from its OSM ele tag"
        },
        {
          "date": "2026-08-20",
          "text": "The altitude profile is stored per edge, not just ascent and descent, so a route's climb can come from the profile as metadata-rules.md requires; ascent/descent are directional and swap when assembly reverses a piece"
        },
        {
          "date": "2026-08-20",
          "text": "Places are snapped with NO distance threshold: distance_m is stored and consumers filter, because how close a car park must be to count as a trailhead is a product decision that a build step would hide"
        },
        {
          "date": "2026-08-20",
          "text": "Places snap to the nearest VERTEX, not the nearest edge; which places a route passes is computed at assembly against the merged line, not precomputed with a radius nobody chose"
        },
        {
          "date": "2026-08-20",
          "text": "Proximity search is planar (GiST on ST_Transform(geom,32632)) while stored distances stay geodesic: geography indexes serve range predicates well and polygon nearest-neighbour badly, 2.6 s against four minutes for 7,471 car parks"
        },
        {
          "date": "2026-08-20",
          "text": "The review bundle is refreshed after every step that changes the store, and its README is generated from live queries rather than hand-written, so it cannot look current while lagging the database"
        },
        {
          "date": "2026-08-20",
          "text": "Every field that gets styled carries a *_class or *_band twin in its qa view with a leading digit, so a review is Categorized-and-Classify rather than a hand-written QGIS expression and a class ramp trapped in one .qgz"
        },
        {
          "date": "2026-08-20",
          "text": "Views are DROP + CREATE, never CREATE OR REPLACE (except qa.latest_run, which others depend on): migrate.py replays the whole chain, so a later migration widening a view otherwise makes the earlier one fail on the next replay"
        },
        {
          "date": "2026-08-20",
          "text": "CORRECTION to the earlier framing: the route document (structured JSON + map, one per route) is the product; PostGIS is the working store that holds the value. Neo4j, the API, the frontend and any social layer are readers of that document and none of them redefines a route"
        },
        {
          "date": "2026-08-20",
          "text": "The route document is versioned and self-contained: attribution, licence and provenance travel inside it because the document is the ODbL Produced Work, and the schema requires a non-empty sources array"
        },
        {
          "date": "2026-08-20",
          "text": "Duration stays out of the route document until DIN 33466 is calibrated; measures is a closed object in the schema so a figure a user would not trust cannot arrive by accident"
        },
        {
          "date": "2026-08-20",
          "text": "Photos, comments and likes will be three MongoDB collections keyed to route.id with binaries in object storage (docs/social-layer.md, designed not built), which imposes now that a generated route's id be derived from geometry so it survives a rebuild"
        },
        {
          "date": "2026-08-20",
          "text": "Routes are generated over our own edges with pgRouting, so every route is a walked edge sequence: direction explicit per edge (reversed swaps ascent/descent), MTB conjunction along the sequence, no corridor matching anywhere in generation"
        },
        {
          "date": "2026-08-20",
          "text": "Generated route ids derive from rounded, direction-normalised geometry (draw/route_id.py) so they survive rebuilds; the via-ring radius is calibrated from measured overshoot (target/5.0), not assumed"
        },
        {
          "date": "2026-08-20",
          "text": "Owner rule: when segments disagree, a joined attribute takes the MOST DEMANDING value. Every route carries both grades - sac_scale (character, >=5%) and sac_max (exigent, hardest metre) - plus graded_share and bike_blocked_m, so a verdict is never invisible data"
        },
        {
          "date": "2026-08-20",
          "text": "Activities are construction, not filters: the mtb catalogue routes over routable_bike edges only, so every mtb loop is bike-legal by construction; same-ground loops across activities fold into one row by the geometry-derived id"
        },
        {
          "date": "2026-08-21",
          "text": "Routes have two shapes: loops, and out-and-backs to an interesting destination (owner rule). Destination choice is ranked interest with a heavy named bonus; the return leg soft-penalises the out leg; the catalogue replaces per (activity, shape) so the four families coexist"
        },
        {
          "date": "2026-08-21",
          "text": "Generated routes are named after their destination (To Rifugio Elisa) - destination naming is free where trailhead naming stays unsolved; an unnamed destination gives no name rather than a bad one"
        },
        {
          "date": "2026-08-21",
          "text": "Neo4j holds the catalogue's selection surface only: identity, measures, both grades, MTB, route-place relationships. Geometry and profile stay in the canonical route document, fetched by route_id - a second home for geometry is how two truths start"
        },
        {
          "date": "2026-08-21",
          "text": "The export owns :Route/:Place/:Start and replaces them wholesale per run; the backend's Trail/Segment graph is untouched. Any consumer of the catalogue filters on the quality block (warnings = 0) - proven necessary by the first smoke run surfacing 0 km fragments"
        }
      ]
    }
  ],
  "blockers": [
    {
      "text": "OpenAI API key was shared in plaintext and must be rotated before any deployment",
      "severity": "high",
      "owner": "oscar",
      "since": "2026-08-15"
    },
    {
      "text": "Supabase database password was shared in plaintext and must be rotated before any deployment",
      "severity": "high",
      "owner": "oscar",
      "since": "2026-08-16"
    },
    {
      "text": "Trailforks is unavailable and this is settled: API-only with a granted key, and the Outside terms need prior written consent for commercial, in-software and AI use, which VaiVia is all three of. Nothing was ever taken (fetch_live is a stub, the fixture is synthetic), so the position is clean. The product moved to OSM instead, so this blocks nothing now unless someone tries to use their data. See docs/licensing.md",
      "severity": "medium",
      "owner": "oscar",
      "since": "2026-08-15"
    },
    {
      "text": "The Supabase account password is 12345678 and was shared in plaintext; it must be changed before any deployment",
      "severity": "high",
      "owner": "oscar",
      "since": "2026-08-16"
    }
  ],
  "nextSteps": [
    {
      "title": "Point backend/graph/queries.cypher templates at :Route/:Place so /chat selects from the catalogue - and filter on warnings = 0, as the export smoke test demonstrates",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Give generated routes an id derived from geometry, not a sequence number or run_id, so photos and comments cannot orphan on a rebuild (docs/social-layer.md)",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Return the composed plan with /chat results so the \"How I read it\" block can render the constraints it understood",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Return a height series with route geometry so the elevation profile can be drawn",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "text": "feat/route-catalogue holds 5 commits of the whole route pipeline and is NOT pushed — it exists only on the dev machine. spike/osm-coverage was merged to main; this one has not been",
      "severity": "high",
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Attach hazards to segments rather than only to trails so the Hazards map layer has geometry to draw",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Expose where coverage stops so the Coverage layer can name the edge instead of failing silently",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Return OSM way ids and the proximity match distance on trail results so the Sources disclosure shows them",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Add an ESLint config to frontend/ - npm run lint currently prompts interactively and does nothing",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Judge the 164 overlap findings in QGIS: duplicate, bridge, or a legitimately shared stretch. The only QA rule that cannot be automated",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Add a SAC ceiling to load/legality.py so alpine terrain is not bike-routable without an explicit mtb:scale or bicycle=yes (198 edges at T4+, 15 with any MTB grade). Needs a reload",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Flatten bicycle/access/mtb:scale/tracktype into qa.v_network so QGIS can style on them without a jsonb expression",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Finish the interrupted Lecco re-ingest and verify ~1,686 POI boundaries and ~104,812 segments, then commit the seven modified files",
      "est": 0.25,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Rebuild trailheads and the catalogue at --min-off-road 0.3 so lakeside and valley routes exist at all; 220 of 266 trailheads are currently unbuilt",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Push feat/route-catalogue and merge it; the whole route pipeline exists only on the dev machine",
      "est": 0.25,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Rotate the exposed OpenAI API key and the Supabase database and account passwords before any deployment",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Make a catalogue rebuild atomic: CLEAR_ROUTES then MERGE leaves it briefly empty, and a live query in that window honestly returns nothing",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Calibrate duration. DIN 33466 rates the classic Grigna ascent (12 km / 1,600 m) at 10 hours where guidebooks say 6-8, so catalogue figures read 15+ hours and a user will not trust them",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Replace the tags.get('highway', 'path') default with None plus an explicit skip, so an untagged way fails loudly instead of becoming routable",
      "est": 0.25,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Investigate the off-road drop from 87% to 74%: comfort.json layered on hike.json is not biting as hard as the standalone model did (67% in the gate test)",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Name the trailheads. 37 of 266 have one; route names now cover 81% so this is no longer blocking, but 'starts at' is still often blank",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Upgrade next 14 to 16, clearing the deferred postcss and sharp advisories",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Caddy TLS, VPS deploy script, Neo4j and Postgres backup cron, uptime check",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Judge the 131 routes that come out in more than one piece in qa.v_route: coverage clipping at the bbox edge, or a real gap along a named route that the topology rules cannot see",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Decide the matched_fraction floor a generated route must clear, so a 27-route tail like BI-12 (2 of 646 ways matched) cannot become a route under a famous name",
      "est": 0.25,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Fetch GLO-30 tile N46 E009 and re-run curate.elevation, closing the 75 edges (56.8 km) north of 46.0001 that have no profile",
      "est": 0.25,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Use profile_m rather than per-edge ascent_m when assembling a route, and swap ascent/descent on any piece the assembly reverses",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Judge the 86 start vertices that are not on the main component - a trailhead on an island is a place you can begin and get nowhere",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Look at the 33 car parks over 100 m from the network in qa.v_place_link: a missing access road in OSM, or a polygon somewhere odd",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Name the trailheads from a nearby named feature - qa.v_start.names is empty for most car parks and 'start from vertex 43128' is not an answer",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Review the refreshed bundle in QGIS: colour network by steepness_class, route by continuity_class, place_link by distance_band, start by reachability_class",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Calibrate duration before adding it to the route document: DIN 33466 gives 10 h for the Grigna ascent against a guidebook 6-8, and the schema deliberately refuses the field until then",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Make Neo4j, the API and the frontend read the route document rather than each deriving route fields - the inversion docs/route-document.md ratifies",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Review the 72 generated routes in QGIS (qa.v_draw by offroad_class) and judge a few by eye: does the loop look like something you would walk?",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Generate the second catalogue mixing high-anchor car parks with the car-free starts, and scale --starts once the shape is judged good",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    }
  ],
  "sessions": [
    {
      "date": "2026-08-15",
      "model": "fable-5",
      "credits": 69,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-16",
      "model": "opus-5",
      "credits": 175,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-16",
      "model": "fable-5",
      "credits": 61,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-17",
      "model": "opus-5",
      "credits": 7,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-17",
      "model": "opus-5",
      "credits": 41,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-18",
      "model": "opus-5",
      "credits": 31,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-18",
      "model": "opus-5",
      "credits": 79,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-18",
      "model": "opus-5",
      "credits": 98,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-19",
      "model": "opus-5",
      "credits": null,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-20",
      "model": "opus-5",
      "credits": null,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-20",
      "model": "opus-5",
      "credits": null,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-20",
      "model": "opus-5",
      "credits": null,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-20",
      "model": "opus-5",
      "credits": null,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-20",
      "model": "opus-5",
      "credits": null,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-20",
      "model": "opus-5",
      "credits": null,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-20",
      "model": "opus-5",
      "credits": null,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-21",
      "model": "opus-5",
      "credits": null,
      "person": "oscar",
      "hours": null
    },
    {
      "date": "2026-08-21",
      "model": "opus-5",
      "credits": null,
      "person": "oscar",
      "hours": null
    }
  ]
}
```
