# Handoff — VaiVia

Last updated 2026-09-16. State only; history is in `docs/pm-log.jsonl` and git
log, doctrine in `docs/{plan,architecture,fragilities,route-design}.md` and `CLAUDE.md`.

## What VaiVia is

A trail-query product over OSM for Lecco and Bergamo; tiers and layout are in
`CLAUDE.md`. **The route document is the product and the network is the
catalogue** — with a pack mounted (`PACK_DIR`) `/chat` draws EVERY outing at ask
time. R7 retired the 627-route catalogue as a product, so a plain loop ask is
drawn like any other. No Trailforks data has ever entered the system.

## Where the data stands

- **Network** — 101,951 edges, 9,238 km, height everywhere, 98%+ one component.
- **Pack** — format 2, 39.5 MB: costs, geometry, profiles, places, drive matrix
  (170 origins × 8,106 starts), rail matrix (17 stations), gazetteer,
  `trail_share_5km`, per-kind potential fields. `pack-parity` is mounted.
- **Catalogue** — retired as product. `catalogue.route` stays in PostGIS as the
  parity oracle, its 627 asks are now a committed fixture
  (`shared/routes/tests/fixtures/parity_asks.json`), parity holds 627/627. The
  Neo4j loader is deleted; the `:Route` rows it wrote are still in the graph,
  unreachable by search but still hydrated by a saved favourite.
- **Store additions** — `staging.car_road/car_edge/rail_min`,
  `source_map.drive_min` (1.3 M pairs), `qa.v_drive_start` (bundle not
  refreshed with it yet).

## Where the code stands

v0.1.0 is merged to `main` (`a8ddd98`) and **not tagged**. R7
`chore/retire-catalogue` is complete and green but **not pushed**: 8 commits,
~3,900 lines net deleted. Two of its decisions go against `docs/plan.md:532` as
written, owner-confirmed and recorded there — `Intersection`/`CONNECTS_TO` stay
because `/chat`'s `RouteIntent` walks them, and the GDS plugin stays for
`scripts.check_graph_connectivity`. GDS is off every serving path.

Tests: backend 336, pipeline 229, shared 76, frontend 128, gateway 41. Evals:
decomposition 62/63 (the baseline median), `check_intents_live` 7/7; the one
failure, g47, is a known defect described in `441b401`.

Not bugs: Supabase auth is parked (`GATEWAY_DEV_NO_AUTH=true` runs all as
`dev-local-user`, refused in production); A→B is bounded `shortestPath` via
`/chat`, never GDS; a backend restart loses unpersisted drawn routes by design.

<!-- pmctl:handoff v1 -->
```json
{
  "project": "VaiVia",
  "org": "ai safe earth",
  "status": "amber",
  "updated": "2026-09-23",
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
      "text": "The OpenAI API key was shared in plaintext and must be rotated before any deployment",
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
      "text": "v0.1.0 is merged to main (a8ddd98) but carries no tag, so production has no marked release to roll back to",
      "severity": "medium",
      "owner": "oscar",
      "since": "2026-09-16"
    },
    {
      "text": "Trailforks is unavailable and this is settled: API-only with a granted key, and the Outside terms need prior written consent for commercial, in-software and AI use, which VaiVia is all three of. Nothing was ever taken. Blocks nothing unless someone tries to use their data. See docs/licensing.md",
      "severity": "medium",
      "owner": "oscar",
      "since": "2026-08-15"
    },
    {
      "text": "next is on ^15.1.3 and the postcss and sharp advisories stay deferred until the 16 upgrade",
      "severity": "low",
      "owner": "oscar",
      "since": "2026-08-16"
    }
  ],
  "nextSteps": [
    {
      "title": "Tag v0.1.0 on a8ddd98 and push the tag",
      "est": 0.1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Push chore/retire-catalogue and open it against develop; the PR body needs the two plan.md:532 departures (Intersection/CONNECTS_TO and the GDS plugin both stay) so a reviewer is not ambushed by them",
      "est": 0.25,
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
      "title": "Decide the fate of the 627 catalogue :Route rows still in Neo4j: drop them, or keep them so an old favourite still hydrates. Nothing reads them by search since R7",
      "est": 0.25,
      "owner": "oscar",
      "phase": "Phase 12 - On-demand routes",
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
      "title": "D3 ci-deploy + D4 publish-pack: images and deploy jobs, VPS bootstrap, PACK_DIR/REQUIRE_PACK/ROUTE_DOCUMENTS_DIR in /srv/vaivia/.env, first fill over the tunnel, confirm pack-engine latency on the VPS, Caddy TLS, backups cron, healthchecks",
      "est": 3,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
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
      "title": "The named CAI sentieri return as the F5 sentiero planner theme (spine discount on the pack's cost columns) - the honest option over re-admitting mapped routes",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 12 - On-demand routes",
      "plan": "redesign"
    }
  ],
  "sessions": [
    {
      "date": "2026-09-23",
      "model": "opus-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    },
    {
      "date": "2026-09-16",
      "model": "opus-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    },
    {
      "date": "2026-09-15",
      "model": "opus-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    }
  ]
}
```
