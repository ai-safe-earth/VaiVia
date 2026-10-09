---
status: done
step: bergamo-refactor
next: none; P8 (direction documents) is a quick fix to confirm obsolete and drop
---

# Phase 7 — The Bergamo refactor (plan approved 2026-08-25)

A trustworthy end-to-end route system on the configured Bergamo bbox, per the
approved implementation plan (owner-ratified this session; PR #32's start/end
contract ratified with one amendment — the `:fwd` sense comes from canonical
geometry, never `edge_id`, which rebuilds reassign). Locked decisions:
geometry-derived ids for every route kind with favorites reset at cutover;
full physical schema rename via a v2 baseline; custom routes publish into the
shared catalogue after automated checks; chat scope is the query-loop plan's
Phase 2 only.

Superseded handoff next-steps (2026-08-28): "rotate the exposed key and
passwords" → Phase 9 D0; "Caddy TLS, VPS deploy script, backup cron, uptime"
→ Phase 9; "generate short hike loops near Lecco" → Phase 10 F3; "decide how
the named CAI sentieri come back" → Phase 10 F5 (decided: generate along the
relations). `/handoff` drops them from `handoff.md` on its next run.

- [x] P0 `fix/card-map-races` — a card click draws its own answer or says its
  line failed; per-card line status; payload identity verified; component
  tests + the e2e asserts drawn id == clicked id (PR #34).
- [x] P1 `fix/document-contract-checks` — the API verifies a document against
  its catalogue row (id / schema_version / build run) with typed 503s;
  `scripts.audit_catalogue_documents` swept 980/980 clean live; the loader
  stamps `doc_run_id`; the catalogue wipe stays under the transaction
  timeout (PR #35).
- [x] P2 `feat/pipeline-v2-schema` — schemas renamed to their jobs
  (source_map / catalogue / provenance) via conversion + v2 baseline;
  regions on the degree matview with the Bergamo tolerance gate measured
  (2 m holds: 0.6% at 2 m); the weld level-guard; `start_class`; `urban_m`
  (bimodal, class cuts in the valleys); oneway-honest pgRouting views; GTFS
  calendar read (trenord is NOT year-round: runs to 2026-12-12); DEM N46
  tile loaded. Ticked 2026-08-28: `sql/v2/0001_baseline.sql` (the schemas),
  `0003` (`start_class`), `0004` (`urban_m`), `0005` (oneway-honest views),
  `0006` (GTFS span) are applied — owner to confirm the weld level-guard and
  the DEM N46 tile; if either is missing, split it into its own `- [ ]` line.
- [x] P3 `feat/route-id-v2` — the id cutover (`vv2-<digest>[-fwd|-rev]` —
  hyphen, not colon: NTFS reads a colon as an Alternate Data Stream
  separator and 877 documents once vanished proving it), schema 2.0
  documents (terminals with network reachability and seasons, categories,
  divergence, continuity.reason), same-ground relations folded out loud
  (one pair: 14910465/14910466), Neo4j reloaded, favorites reset, the
  cross-layer contract fixture pinned in both suites; audit 979/979 clean.
- [x] P4 `feat/draw-out-and-back` — strict out-and-back (same edges home
  by construction, one-way 2–20 km enforced with rejects reported); legs
  run directed over the oneway-honest views; the parameter surface
  (start-class, region, exigent ceiling, ascent, retrace, urban cap);
  urban_share measured (p95 0.66, tail to 0.92) with the cap at 0.8;
  catalogue regenerated over both provinces — 1,371 routes (751 mapped +
  620 generated across six families), audit clean. The 751 mapped were
  withdrawn from the published catalogue on 2026-08-26 (see P5a). The enum CHECK
  constraints landed after a parameter-order slip corrupted a run's shapes
  and the emit drift-guard caught it downstream — the constraint now fails
  the first row at the moment of the mistake.
- [x] P5a `feat/withdraw-mapped-routes` — the catalogue publishes
  `generated` only (`export/document.py::PUBLISHED_KINDS`, read by both the
  emitter and the loader). The 751 mapped relations were the pre-factory
  corpus and measured badly — 187 with warnings, 56 under 500 m, 131 in
  pieces, 27 under 20% matched fraction — because a relation is a mapping
  of ground that our bboxes clip. `export.route_documents` withdraws by
  default and publishes only under `--publish`; the loader gates on kind
  regardless. `source_map.edge_route` untouched: the relations still name
  the network and feed `qa.v_route*`. Corpus 1,378 → 627, and the 302 named
  CAI sentieri leave the answerable set with them.
- [x] P5 `feat/compact-card` — the two-line card; exigent warning; three-
  valued bike state; surface distribution, places, real provenance and QA
  warnings on expand. Landed 2026-08-27 on PR #49
  (`frontend/components/LoopCard.tsx:157-203`, `:214-372`; ticked 2026-08-28).
- [x] P6 `feat/chat-standing-plan` — persisted standing plan (query-loop
  Phase 2); resume rehydrates cards from result_refs. Landed 2026-08-26: the
  executed plan persists as `messages.intent.standing` (same jsonb, no
  migration); `PlanEnvelope.refine` marks a delta turn and
  `composer.apply_delta` merges it latest-wins in Python; a clarify carries
  the standing forward. Resume is single-conversation (the tabs are gone):
  `GET /routes/by-ids` hydrates stored loop_ids back into cards.
  `scripts.dump_conversation` renders any conversation as markdown with
  BUG/FIX/NOTE user turns flagged — the feedback-log surface.
- ~~P7 `feat/custom-route-jobs`~~ — superseded by Phase 12 (2026-09-02): a
  custom route is drawn in-process at ask time, so there is no job to queue,
  no worker and no publication step. Quota stays the per-user LLM cost cap.
- [ ] P8 `feat/direction-documents` — the `-rev` siblings and `reverse_of`
  (additions, never renames — the id rule landed in P3).
