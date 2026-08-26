# Handoff — VaiVia

State only. History is `docs/pm-log.jsonl` (append-only, never read back); rules
and local setup are `CLAUDE.md`; architecture is `docs/architecture.md`.

## What VaiVia is

A trail-query product over OSM for Lecco and Bergamo. Four tiers — Next.js
frontend, Fastify gateway (the only public service), FastAPI backend, Neo4j —
plus `pipeline/`, a PostGIS working store where the value lives. **The route
document is the product**: the pipeline emits one JSON per route and Neo4j, the
API and the frontend are all readers of it. The data is OSM throughout, with
open-licensed enrichment; no Trailforks data has ever entered the system.

## Where the data stands

- **Network** — 101,951 edges, 9,238.0 km, height on every edge, 98%+ of
  vertices in one component. 752 OSM route relations joined as the naming layer.
- **Catalogue** — 627 generated routes in six families (foot/mtb ×
  loop/destination/out_and_back), all drawn over the current network, all
  published to Neo4j and served as documents from `review/routes/`.
- The mapped OSM relations were withdrawn from the published catalogue on
  2026-08-26 and are a naming and QA layer now. That takes the 302 named CAI
  *sentieri* out of the answerable set until routes are generated over them.
- **Open QA queue** — 164 overlaps, judgement only; every automatable topology
  defect is at zero. 370 islands remain deliberately, as a coverage fact. The
  review surface is `review/vaivia-qa.gpkg` + a generated `review/README.md`.

## Verification

252 pipeline, 331 backend, 96 frontend, 40 gateway unit tests, plus 4 Playwright
e2e against the live stack. CI runs the four unit suites and stays offline.

## In flight

- **PR #40** (`feat/withdraw-mapped-routes` → `develop`) is open and unreviewed.
- Supabase auth is parked: `GATEWAY_DEV_NO_AUTH=true` runs everything as
  `dev-local-user`, and the gateway refuses to boot with that flag in production.
- Status is amber for one reason: three credentials were shared in plaintext and
  must be rotated before anything deploys.

<!-- pmctl:handoff v1 -->
```json
{
  "project": "VaiVia",
  "org": "ai safe earth",
  "status": "amber",
  "updated": "2026-08-26",
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
      "start": "2026-08-16",
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
      "text": "Supabase database password was shared in plaintext and must be rotated before any deployment",
      "severity": "high",
      "owner": "oscar",
      "since": "2026-08-16"
    },
    {
      "text": "The Supabase account password is 12345678 and was shared in plaintext; it must be changed before any deployment",
      "severity": "high",
      "owner": "oscar",
      "since": "2026-08-16"
    },
    {
      "text": "Trailforks is unavailable and this is settled: API-only with a granted key, and the Outside terms need prior written consent for commercial, in-software and AI use, which VaiVia is all three of. Nothing was ever taken (fetch_live is a stub, the fixture is synthetic), so the position is clean and the product moved to OSM. Blocks nothing unless someone tries to use their data. See docs/licensing.md",
      "severity": "medium",
      "owner": "oscar",
      "since": "2026-08-15"
    }
  ],
  "nextSteps": [
    {
      "title": "Review and merge PR #40 (the mapped relations withdrawn from the published catalogue)",
      "est": 0.25,
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
      "title": "Review the 627 generated routes in QGIS (qa.v_draw, colour by route_shape_class then offroad_class) and judge a few by eye: does the loop look like something you would walk, and does an out-and-back go somewhere worth returning from",
      "est": 0.5,
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
      "title": "Rotate the exposed OpenAI API key and the Supabase database and account passwords before any deployment",
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
      "title": "Decide whether a lane exit out of a settlement is a start after all: 2,990 are recorded with 'the town continuing' and reviewable in qa.v_urban_exit, so it is a one-word change either way",
      "est": 0.25,
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
      "date": "2026-08-22",
      "model": "opus-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    },
    {
      "date": "2026-08-23",
      "model": "opus-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    },
    {
      "date": "2026-08-26",
      "model": "opus-5",
      "person": "oscar",
      "credits": null,
      "hours": null
    }
  ]
}
```
