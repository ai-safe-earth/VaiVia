# Data sources for the route pipeline

Surveyed 2026-08-19 over the two configured provinces (Lecco bbox 45.8,9.3–46.0,9.6;
Bergamo bbox 45.68,9.55–45.92,9.85). Each source is judged on three questions, measured
where a measurement was possible today and named as a check where it was not:

- **Coverage** — what fraction of the features we need does it actually carry, *here*?
- **Licence** — usable commercially, in a software product, and with AI? These are the
  three tests Trailforks failed (`docs/licensing.md`); no source is adopted without
  passing them.
- **Fitness** — what question does it answer that nothing else in the list does?

Verdicts: **adopt** · **adopt-with-caveat** · **reject**. A caveat names the check that
must pass at adoption time; a rejection names the reason so it is not re-litigated.

| # | source | verdict | licence | role |
|---|---|---|---|---|
| 1 | OSM ways (Geofabrik) | **adopt** | ODbL | the network itself, tag-complete |
| 2 | OSM route relations | **adopt** | ODbL | named trails, waymarks, endpoints |
| 3 | Copernicus GLO-30 DEM | **adopt** | free/full/open, attribution | elevation, profiles |
| 3b | EU-DEM v1.1 | **reject** | — | discontinued Jan 2024 |
| 3c | SRTM 90 m (CGIAR) | **reject as primary** | — | GLO-30 is 3× finer; keep for GraphHopper only |
| 4 | GTFS (regional + basin) | **adopt-with-caveat** | IODL/CC-BY (confirm per feed) | transit-reachable starts |
| 5 | Lombardia REL (catasto sentieri) | **adopt-with-caveat** | CC-BY 4.0 | authoritative cross-check on OSM |
| 6 | Copernicus CLC+ Backbone | **adopt** | free/full/open, attribution | land cover along routes |
| 7 | CAI Infomont | **adopt-with-caveat** | ODbL | official itineraries, rifugi |
| 8 | GPS traces / heatmaps | **reject for now** | — | Strava/Komoot terms fail all three tests; OSM planet.gpx is stale (2013) |
| 9 | Wikipedia / Wikidata | **adopt** (already wired) | CC-BY-SA / CC0 | POI prose |
| 10 | ARPA Lombardia (weather/snow) | **defer** | open data portal | seasonality, later phase |

---

## 1. OSM ways, via Geofabrik extract — ADOPT

**What.** The full `europe/italy/nord-ovest` extract: [548 MB PBF, updated
daily](https://download.geofabrik.de/europe/italy/nord-ovest.html). Replaces Overpass as
the way source: the whole region arrives in one reproducible file with **every tag
intact**, instead of a bbox query that keeps only what it thought to ask for. It is also
what GraphHopper imports, so one download can serve both.

**Coverage — measured** on the production Lecco Overpass fetch (22,610 routing ways,
11,937 off-road), which is the same data by another door:

| tag | off-road ways | all routing ways |
|---|---|---|
| `surface` | 61.7% | 56.4% |
| `sac_scale` | **41.3%** | 21.9% |
| `trail_visibility` | 36.9% | 19.6% |
| `incline` | 30.7% | 17.2% |
| `foot` / `bicycle` | 24.6% / 23.9% | 14.8% / 14.1% |
| `mtb:scale` | 21.6% | 11.7% |
| `tracktype` | 16.6% | 9.6% |
| `name` | 10.7% | 29.5% |

Today's ingestion stores only `surface` and `highway`; every other row of that table is
currently discarded. Access tags (`access`, `foot`, `bicycle`) become **hard legality
rules** in the new pipeline, not metadata.

**Licence.** [ODbL](https://www.openstreetmap.org/copyright): commercial use, software
use and derived databases all permitted; attribution required (already rendered in the
frontend since `d014c09`); a derived *database* must be shared alike — our curated tables
are a derivative, which is compatible with this product and already the position the
existing graph is in.

**Fitness.** It is the network. Nothing else in the list carries geometry we can route on.

## 2. OSM route relations — ADOPT

**What.** `relation[route~hiking|foot|mtb|bicycle]` — the named-trail layer today's
ingestion never fetches. Comes free in the same Geofabrik extract (pyosmium resolves
member ways).

**Coverage — measured** on the spike's cached Overpass fetches:

| | Lecco | Bergamo |
|---|---|---|
| relations | 453 | 366 |
| `ref` (CAI sentiero number) | 87.9% | 83.3% |
| `from` / `to` (named endpoints) | 86.8% | 92.9% |
| `network` (lwn/rwn/ncn) | 97.4% | 98.9% |
| `osmc:symbol` (waymark) | 84.1% | 71.9% |
| `name` | 36.9% | 36.9% |
| `description` | 20.8% | 10.7% |

Real examples: `33 · Pasturo – Grignone (via estiva)`, `6 · Traversata Bassa delle
Grigne`, `24 · Cainallo – Rifugio Bietti`.

**Licence.** ODbL, as above.

**Fitness.** Three things nothing else supplies: the sentiero numbering a walker actually
uses ("segui il 33"); waymark symbols for "follow the red-white flashes" instructions; and
`from`/`to` **named endpoint pairs** — the naming source for starts (219 of 257 current
trailheads are nameless) and the seed list for out-and-back destinations.

## 3. Elevation — Copernicus GLO-30: ADOPT. EU-DEM: REJECT. SRTM 90 m: keep for GraphHopper only

**Copernicus GLO-30** — 30 m global DEM, [hosted free on AWS Open
Data](https://registry.opendata.aws/copernicus-dem/) as Cloud-Optimised GeoTIFFs
(`s3://copernicus-dem-30m/`, no account needed). Two 1°×1° tiles cover both provinces.
Licence: [free, full and open](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM),
commercial use permitted; required credit
`© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under
COPERNICUS by the European Union and ESA` plus a "produced using…" notice on derived
works — a fixed attribution string, no share-alike. **Adopt** as the profile source.

**EU-DEM v1.1** — [no longer disseminated as of January
2024](https://land.copernicus.eu/en/products/products-that-are-no-longer-disseminated-on-the-clms-website);
CLMS itself points users at Copernicus DEM. **Reject**: building on a discontinued
product is adopting a migration.

**SRTM 90 m (CGIAR)** — already cached on the GraphHopper volume
(`/data/srtm-cache/srtm_38_03`, 106 MB) and baked into its graph. 3× coarser than GLO-30.
**Keep only as GraphHopper's internal source**; profiles served to users come from GLO-30
so the dashboard and the engine do not silently disagree by provider. *Adoption check:*
sample both DEMs over the Grigna classic (12 km / ~1,600 m, the ascent
`backend/tests/test_durations.py` already pins) and record the delta.

## 4. GTFS — ADOPT-WITH-CAVEAT

**What.** Transit stops + service, for the ratified start rule ("reachable without a
car"). Regional rail: [Trenord GTFS on
dati.lombardia.it](https://www.dati.lombardia.it/Mobilit-e-trasporti/Orario-Ferroviario-Regionale-Gtfs/3z4k-mxz9),
refreshed and valid. Buses are per transport *basin*:
[Como-Lecco-Varese](https://www.tplcomoleccovarese.it/) and
[Bergamo](https://www.agenziatplbergamo.it/) agencies both publish GTFS.

**Coverage — measured today** on the live Trenord feed (566 stops total):
**11 rail stops inside the Lecco bbox** (Lierna, Olcio, Mandello, Abbadia Lariana, Sala
al Barro-Galbiate, Civate…) and **6 inside the Bergamo bbox**. Rail alone already makes
the lakeshore towns transit-reachable starts.

**Licence.** dati.lombardia.it publishes under IODL/CC-BY terms. **Caveat:** confirm the
licence statement on each *basin agency* feed at adoption — agency sites sometimes attach
their own terms.

**Fitness.** The only source that can say a start is reachable without a car — the third
leg of the start rule, and unanswerable from OSM alone (a `highway=bus_stop` with no
service is not reachability).

## 5. Lombardia REL — catasto sentieri — ADOPT-WITH-CAVEAT

**What.** The region's own trail registry ([Catasto della Rete Escursionistica
Lombarda](https://www.geoportale.regione.lombardia.it/news/-/asset_publisher/80SRILUddraK/content/catasto-regionale-della-rete-escursionistica-della-lombardia-rel-),
L.R. 5/2017), layers: percorsi, tratte, segnaletica (GPS positions of actual waymarks),
punti d'acqua, strutture ricettive, località, POIs.

**Licence.** **CC-BY 4.0** — passes all three tests with attribution.

**Coverage.** Not measured today (WFS/download, not a one-request probe). **Caveat — the
adoption check:** download the Lecco+Bergamo cut, count percorsi against OSM's 819
relations, and measure geometric agreement on a sample valley. Role either way is
**cross-check and gap-fill**, not replacement: where REL has a path OSM lacks (or vice
versa) that is a `qa_finding`, and the *segnaletica* layer (real waymark positions) exists
nowhere else in this list.

**Fitness.** The authoritative answer to "is OSM's picture of this valley complete?" —
the one question OSM cannot ask of itself.

## 6. Copernicus CLC+ Backbone — ADOPT

**What.** [10 m raster land cover, 11 classes,
Europe-wide](https://land.copernicus.eu/en/products/clc-backbone), 100 km tiles,
two-yearly updates (2018/2021/2023 releases).

**Licence.** Copernicus full/open/free (Regulation 1159/2013): commercial use permitted,
source credit required, modifications must be stated.

**Coverage.** Wall-to-wall by construction; no gap risk in-region. Adoption check is only
resolution fitness: 10 m against a 2–3 m wide path means the class *along* the line, not
of the path surface itself — fine for "through woodland vs open pasture", which is the
question asked.

**Fitness.** Scenery and shade: "mostly in forest", "open ridge above the treeline" —
signals no OSM tag carries at coverage, feeding both route scoring and composed prose.

## 7. CAI Infomont — ADOPT-WITH-CAVEAT

**What.** The [Catasto della Rete Sentieristica Italiana](https://www.cai.it/sentieri-e-rifugi/infomont/):
CAI's own digital registry of itineraries, rifugi and bivouacs; GPX per trail.

**Licence.** Verified on the CAI page itself: *"open data, distributed with Open Data
Commons Open Database License (ODbL) by the Club Alpino Italiano"* — same licence family
as OSM, attribution to both CAI and OSM. Passes all three tests. (This is the
happy inverse of the Trailforks finding: the club data VaiVia wanted all along, openly
licensed.)

**Coverage.** Not measured today; the portal is interactive and bulk access needs
checking. **Caveat — the adoption check:** confirm a bulk/regional export exists (not
just per-trail GPX), then count itineraries over the two provinces against OSM's 819
relations. Expected overlap is high (CAI sections maintain both), so the marginal value
is *official status and maintenance state*, not geometry.

**Fitness.** Officialness: "this is a maintained CAI itinerary" is a trust signal neither
OSM nor REL can grant.

## 8. GPS traces / heatmaps — REJECT for now

- **Strava / Komoot heatmaps:** terms restrict to personal, non-commercial use and forbid
  extraction — the same three-way failure as Trailforks (`docs/licensing.md`). Rejected on
  licence regardless of usefulness. Nothing has been fetched.
- **OSM public GPS traces:** licence-compatible, but the bulk artefact
  ([planet.gpx](https://wiki.openstreetmap.org/wiki/Planet.gpx)) was last built in
  **2013**; current access is a paged per-bbox API of mixed-age traces with no recency
  filter. Coverage-per-effort is poor.

Popularity therefore stays out of the scoring in v1 (the criteria list marks it
"if the source survives Phase 0" — it did not). Revisit only if a licensed popularity
signal appears.

## 9. Wikipedia / Wikidata — ADOPT (already wired)

`scripts/enrich_pois_wiki.py` already implements the resolution order (wikipedia tag →
wikidata sitelink → summary; wikidata one-liner stored, never embedded) and stores per-POI
attribution. CC-BY-SA (text) / CC0 (wikidata). **Measured today: 48 of 3,195 POIs (1.5%)
carry real prose** — the baseline the wider POI set and importance-ranked matching must
beat. Re-run over the pipeline's larger POI catalogue; no design change needed.

## 10. ARPA Lombardia weather / snow — DEFER

Station data (including nivometers above ~1,000 m) is on
[dati.lombardia.it](https://www.dati.lombardia.it/stories/s/DATI-METEO-in-OpenData/ax8t-ekyc/)
as open data. Licence workable; fitness real (season-scoped hazards are already modelled
in the graph). Deferred because seasonality enrichment is a later phase than the
map-of-routes deliverable, and adopting it now would widen Phase 2 for no Phase 5 gain.
Named here so the decision is dated, not forgotten.

---

## What Phase 2 loads, concretely

1. **Geofabrik `nord-ovest` PBF** → pyosmium → staging: ways (full tag set), route
   relations (with ordered members), POI nodes/areas, settlements (`place=*`,
   `landuse=residential`), drivable roads, parking, stations.
2. **Copernicus GLO-30**, two tiles from AWS → `raster2pgsql` → `staging_dem`.
3. **GTFS**: Trenord + the two basin agency feeds → stops with real service.
4. **CLC+ Backbone 2023**: the tile covering both provinces → `staging_landcover`.
5. **REL** (after its adoption check) → `staging_rel_*` for the QA cross-check.
6. **Infomont** (after its adoption check) → official-status flags on matched itineraries.

Attribution obligations accumulate: OSM (ODbL), Copernicus DEM (fixed credit string),
CLC+ (source credit), REL (CC-BY), CAI (ODbL), Wikipedia (CC-BY-SA, per-POI, already
stored). The frontend already renders OSM attribution; the others join it when their data
first reaches a user-facing surface.
