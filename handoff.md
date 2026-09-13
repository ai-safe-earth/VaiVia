# Handoff — VaiVia

Last updated 2026-09-12. State only; history is in `docs/pm-log.jsonl` and git
log, doctrine in `docs/{plan,architecture,fragilities,route-design}.md` and `CLAUDE.md`.

## What VaiVia is

A trail-query product over OSM for Lecco and Bergamo. Four tiers — Next.js
frontend, Fastify gateway (the only public service), FastAPI backend, Neo4j —
plus `pipeline/` (PostGIS working store) and `shared/routes/` (`vaivia_routes`),
the package both Python tiers import. **The route document is the product and
the network is the catalogue**: with a pack mounted (`PACK_DIR`), `/chat` draws
outings at ask time — Phase 12 R1–R5 are live — and the 627-route catalogue
still answers plain loop/trail asks until R7 retires it. Data is OSM
throughout; no Trailforks data has ever entered the system.

## Where the data stands

- **Network** — 101,951 edges, 9,238 km, height everywhere, 98%+ one component.
- **Pack** — format 2, 39.5 MB: costs, geometry, profiles, places, drive matrix
  (170 town/village origins × 8,106 starts), rail matrix (17 stations, track
  time), gazetteer, `trail_share_5km`, per-kind potential fields. Fixture
  `pack-lecco-3km` committed; parity pack + asks under `pipeline/packs/`.
- **Catalogue** — 627 routes in Neo4j; also the parity oracle (627/627 holds).
- **Store additions** — `staging.car_road/car_edge/rail_min`,
  `source_map.drive_min` (1.3 M pairs), layer `qa.v_drive_start` (bundle not
  yet refreshed with it).

## Where the code stands

One checkout on `develop` (the `A02_VaiVia-route-design` worktree is merged and
prunable). **Release v0.1.0 is prepared and waiting**: PR #57 (gate fix — merge
first), then release PR #58 (develop → main, the first since PR #5), then tag.
Signed-in e2e is the owner's step (credentials). Tests: backend 424,
pipeline 274, shared 32, frontend 126, gateway 41. Release-gate eval lines in
`backend/eval_runs.jsonl` (decomposition 62–63/63, retrieval 16–17/20 on the
5-trail stub corpus, facts 3/3, answers 52–53/53; containment 7/7 twice).

Three things look like bugs and are not: Supabase auth is parked
(`GATEWAY_DEV_NO_AUTH=true` runs all as `dev-local-user`, refused in
production); A→B routing is still GDS until R7 removes it; a backend restart
loses unpersisted drawn routes by design (favourite persists them).

<!-- pmctl:handoff v1 -->
```json
{
  "project": "VaiVia",
  "org": "ai safe earth",
  "status": "amber",
  "updated": "2026-09-12",
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
      "name": "Phase 6 - Beta hardening",
      "status": "active",
      "start": "2026-08-17",
      "end": null,
      "plan": "redesign"
    },
    {
      "name": "Phase 12 - On-demand routes",
      "status": "active",
      "start": "2026-09-02",
      "end": null,
      "plan": "redesign"
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
      "text": "The Supabase account password is 12345678 and was shared in plaintext; it must be changed before any deployment",
      "severity": "high",
      "owner": "oscar",
      "since": "2026-08-16"
    },
    {
      "text": "next is on ^15.1.3 and the postcss and sharp advisories stay deferred until the 16 upgrade",
      "severity": "low",
      "owner": "oscar",
      "since": "2026-08-16"
    },
    {
      "text": "Trailforks is unavailable and this is settled: API-only with a granted key, and the Outside terms need prior written consent for commercial, in-software and AI use, which VaiVia is all three of. Nothing was ever taken. Blocks nothing unless someone tries to use their data. See docs/licensing.md",
      "severity": "medium",
      "owner": "oscar",
      "since": "2026-08-15"
    }
  ],
  "nextSteps": [
    {
      "title": "Ship v0.1.0: run the signed-in e2e (stack up, E2E_EMAIL/E2E_PASSWORD, cd frontend && npm run test:e2e), merge PR #57 then release PR #58, tag v0.1.0 on the merge sha",
      "est": 0.25,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "R6 feat/multi-day: day-leg chaining over sleep-kind places, fitness -> per-day band, trek.json envelope, day cards, demo D; E stays a coverage Clarify",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 12 - On-demand routes",
      "plan": "redesign"
    },
    {
      "title": "R7 chore/retire-catalogue: drop the catalogue templates/loads/LoopSearchIntent, GDS off, publish-pack.sh, docs trimmed; the 627 ids stay as the parity fixture",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 12 - On-demand routes",
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
      "title": "D3 ci-deploy + D4 publish-pack: images and deploy jobs, VPS bootstrap, PACK_DIR/REQUIRE_PACK/ROUTE_DOCUMENTS_DIR in /srv/vaivia/.env, first fill over the tunnel, confirm pack-engine latency on the VPS, Caddy TLS, backups cron, healthchecks",
      "est": 3,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Refresh the review bundle (qa.v_drive_start and the drive/rail tables landed without a refresh) and eyeball the drive bands in QGIS",
      "est": 0.25,
      "owner": "oscar",
      "phase": "Phase 12 - On-demand routes",
      "plan": "redesign"
    },
    {
      "title": "Calibrate duration: DIN 33466 rates the classic Grigna ascent at 10 hours where guidebooks say 6-8; cards show our estimate, the schema still refuses the field until the figure is one a walker would trust",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "The named CAI sentieri return as the F5 sentiero planner theme (spine discount on the pack's cost columns) after Phase 12 slice 4 wiring settles - the honest option over re-admitting mapped routes",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 12 - On-demand routes",
      "plan": "redesign"
    }
  ],
  "sessions": [
    {
      "date": "2026-09-12",
      "model": "fable-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    },
    {
      "date": "2026-09-11",
      "model": "fable-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    },
    {
      "date": "2026-09-02",
      "model": "fable-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    }
  ]
}
```
