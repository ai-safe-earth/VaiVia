# Handoff — VaiVia

Last updated 2026-08-28. State only; history is in `docs/pm-log.jsonl` and git
log, doctrine in `docs/{plan,architecture,fragilities}.md` and `CLAUDE.md`.

## What VaiVia is

A trail-query product over OSM for Lecco and Bergamo. Four tiers — Next.js
frontend, Fastify gateway (the only public service), FastAPI backend, Neo4j —
plus `pipeline/`, a PostGIS working store where the value lives. **The route
document is the product**: the pipeline emits one JSON per route and Neo4j, the
API and the frontend are all readers of it. The data is OSM throughout; no
Trailforks data has ever entered the system.

## Where the data stands

- **Network** — 101,951 edges, 9,238.0 km, height on every edge, 98%+ of
  vertices in one component. 752 OSM route relations joined as the naming layer.
- **Catalogue** — 627 generated routes in six families (foot/mtb ×
  loop/destination/out_and_back), drawn over the current network, published to
  Neo4j, served as documents from `review/routes/`.
- Mapped OSM relations were withdrawn from the published catalogue on
  2026-08-26 and are a naming and QA layer now, which takes the 302 named CAI
  *sentieri* out of the answerable set until routes are generated over them.
- **Open QA** — 164 overlaps, judgement only; every automatable topology defect
  is at zero, 370 islands stay deliberately. Surface: `review/vaivia-qa.gpkg`.

## Where the code stands

All four tiers green: 267 pipeline, 378 backend, 112 frontend, 41 gateway unit
tests, plus 4 Playwright e2e against the live stack. CI runs the four unit
suites and stays offline. `develop` carries the whole PR stack (#46, #48).

Two things look like bugs and are not: Supabase auth is **parked**, so
`GATEWAY_DEV_NO_AUTH=true` runs all as `dev-local-user` (refused in production);
and routing is comfort-weighted GDS Dijkstra where `docs/routing-engine.md`
chose GraphHopper, not yet migrated.

`feat/chat-feedback-ui-pass` carries the chat/feedback/basemap pass as three
commits, plus uncommitted fixes from `/code-review max --fix` (11 findings
fixed, 4 skipped as owner decisions). `ANSWER_SYSTEM_PROMPT` changed, so the
paid `--answers` golden re-run is due before the branch merges.

<!-- pmctl:handoff v1 -->
```json
{
  "project": "VaiVia",
  "org": "ai safe earth",
  "status": "amber",
  "updated": "2026-08-28",
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
      "title": "Land feat/chat-feedback-ui-pass: commit the review fixes, decide the 3 skipped findings (near-black mtb lines on the new dark basemaps, safety caveats the count-only prompt dropped, OpenTopoMap/Esri tile terms entry in docs/licensing.md), re-run the paid --answers golden (ANSWER_SYSTEM_PROMPT changed), open the PR against develop",
      "est": 0.5,
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
      "title": "Decide how the named CAI sentieri come back into the answerable corpus, now that withdrawing the relations took 302 of them out: generate routes ALONG named relations so the names ride on ground we drew, or re-admit mapped routes through a measured quality gate (warnings 0, single piece, >= 500 m, a matched_fraction floor). The first is the honest one; the second is cheaper",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Generate short hike loops near Lecco (smaller target_m band): the catalogue has none under 18 km, so 'a short loop hike near Lecco' returns an empty block and golden g27/g49 cannot be pinned",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Decide whether SINGLE-LINE mapped routes need a matched_fraction floor too - the 0.9 multi-piece floor is ratified (2026-08-27) and holds the clipped fragments, but a single-line route with a low matched share still emits unfiltered",
      "est": 0.25,
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
      "title": "Calibrate duration: DIN 33466 rates the classic Grigna ascent at 10 hours where guidebooks say 6-8, and the route-document schema deliberately refuses the field until the figure is one a walker would trust",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Caddy TLS, VPS deploy script, Neo4j and Postgres backup cron, uptime check against /healthz",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    }
  ],
  "sessions": [
    {
      "date": "2026-08-27",
      "model": "fable-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    },
    {
      "date": "2026-08-28",
      "model": "opus-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    },
    {
      "date": "2026-08-28",
      "model": "fable-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    }
  ]
}
```
