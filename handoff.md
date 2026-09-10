# Handoff — VaiVia

Last updated 2026-09-02. State only; history is in `docs/pm-log.jsonl` and git
log, doctrine in `docs/{plan,architecture,fragilities,route-design}.md` and `CLAUDE.md`.

## What VaiVia is

A trail-query product over OSM for Lecco and Bergamo. Four tiers — Next.js
frontend, Fastify gateway (the only public service), FastAPI backend, Neo4j —
plus `pipeline/`, a PostGIS working store where the value lives, and now
`shared/routes/` (`vaivia_routes`), the package both Python tiers import. **The
route document is the product** and, since 2026-09-02, **the network is the
catalogue**: Phase 12 draws a route per ask over an exported pack instead of
searching 627 pre-generated ones (`docs/route-design.md`). The data is OSM
throughout; no Trailforks data has ever entered the system.

## Where the data stands

- **Network** — 101,951 edges, 9,238.0 km, height on every edge, 98%+ of
  vertices in one component. 752 OSM route relations joined as the naming layer.
- **Catalogue** — 627 generated routes, published to Neo4j, served from
  `review/routes/`. Still what answers `/chat` until Phase 12 R4; then it is the
  parity oracle only.
- **Pack** — `export.pack` writes the whole store in 10 s: 80,113 vertices,
  101,951 edges, 761,048 points, 17,697 places (10,489 starts), ~35 MB. Format 1
  is `shared/routes/vaivia_routes/pack.py`. No fixture pack cut yet.
- **Open QA** — 164 overlaps, judgement only; 370 islands stay deliberately.

## Where the code stands

Two checkouts. `A02_VaiVia-route-design` (worktree, branch `feat/pack-export`,
two commits over develop `52c879a`, **not pushed**, `.env` copied in) carries
Phase 12 R1: the pack export, the shared package, its CI job and docs; 291
pipeline+shared tests green. `A02_VaiVia` (this one) sits on
`feat/chat-feedback-ui-pass` at `b2f4a66` — PR #49 is **merged** — with 34
files uncommitted that are not from the pack session: deploy-strategy and
release-process sections in `docs/plan.md`, frontend and `CLAUDE.md` edits.
Sort that tree before branching anything else from here.

Other tiers: 378 backend, 112 frontend, 41 gateway unit tests, 4 Playwright e2e.
Golden eval at `ece21a3`: decomposition 50/50, answers 44/44, retrieval 18/20.

Two things look like bugs and are not: Supabase auth is **parked**, so
`GATEWAY_DEV_NO_AUTH=true` runs all as `dev-local-user` (refused in production);
and A→B routing is still GDS Dijkstra — GDS is retired by decision, not yet code.

<!-- pmctl:handoff v1 -->
```json
{
  "project": "VaiVia",
  "org": "ai safe earth",
  "status": "amber",
  "updated": "2026-09-02",
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
      "title": "Push feat/pack-export from the A02_VaiVia-route-design worktree and open the PR against develop. Before that, if wanted: cut the fixture pack (cd pipeline && uv run python -m export.pack --out ../shared/routes/tests/fixtures --bbox 9.38,45.84,9.42,45.87 --name pack-lecco-3km; needs live PostGIS, writes a build_run row) and add a load-the-fixture test in shared/routes/tests",
      "est": 0.25,
      "owner": "oscar",
      "phase": "Phase 12 - On-demand routes",
      "plan": "redesign"
    },
    {
      "title": "R2 feat/pack-engine: move assemble/loops/destinations/document/ids into vaivia_routes (re-exports left in pipeline until R7), network.py with CSR per activity and scipy dijkstra(limit=), planner invariants as pure functions, the 627-id parity test over the full pack, latency measured and written into docs/route-design.md",
      "est": 3,
      "owner": "oscar",
      "phase": "Phase 12 - On-demand routes",
      "plan": "redesign"
    },
    {
      "title": "R3 feat/outing-intent: OutingIntent/Waypoint/StartSpec in chat/intents.py (no query, template, id, coordinate or weight fields), chat/compile.py owns every number, golden dataset extended, check_intents_live adversarial half stays 7/7 clarify",
      "est": 2,
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
      "title": "Decide how the named CAI sentieri come back into the answerable corpus (302 went out with the mapped relations). Phase 12 reframes it: a named relation can be a waypoint/theme the planner routes along, which is the honest option; re-admitting mapped routes through a quality gate is the cheap one",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 12 - On-demand routes",
      "plan": "redesign"
    },
    {
      "title": "Judge the 86 start vertices that are not on the main component; in the pack they are starts the planner cannot leave, so decide whether the export drops them or the planner refuses them",
      "est": 0.5,
      "owner": "oscar",
      "phase": "Phase 12 - On-demand routes",
      "plan": "redesign"
    },
    {
      "title": "Calibrate duration: DIN 33466 rates the classic Grigna ascent at 10 hours where guidebooks say 6-8. Cards will show it labelled our estimate from R4; the schema still refuses the field until the figure is one a walker would trust",
      "est": 1,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    },
    {
      "title": "Caddy TLS, VPS deploy script, Neo4j and Postgres backup cron, uptime check against /healthz; migration 0005 must be applied before the backend deploys",
      "est": 2,
      "owner": "oscar",
      "phase": "Phase 6 - Beta hardening",
      "plan": "redesign"
    }
  ],
  "sessions": [
    {
      "date": "2026-09-02",
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
