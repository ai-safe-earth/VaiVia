# VaiVia — Product Status

## Description
VaiVia is a voice- and text-driven route-planning app for hikers,
trail runners and mountain bikers: you describe the outing you want in
plain language and it finds or builds the route. Behind the frontend
sits a knowledge graph (Neo4j + FastAPI) merging OpenStreetMap
infrastructure with Trailforks curated trail metadata. It is an
end-user product — the graph and API are internal infrastructure, not
the thing being sold.

## Strategy
Current bet: conversation replaces the filter panel. Multi-hop graph
traversal answers compound queries (4+ hops — trail → hut → station →
trail) that flat trail databases like Komoot, Wikiloc and AllTrails
can't handle, and natural language is the only interface that makes
those queries askable. Competitive frame is consumer route-planning
apps, not developer tooling.

## Log
- 2026-08-15 — Project set up for branding/artwork and strategy/GTM
  work. `product-status.md` established as shared state between chats,
  committed to the repo root and mirrored in project knowledge.
- 2026-08-15 — Repo (Phase 1–2) fixed as ground truth for what is
  shipped; roadmap phases 3–5 must not be presented as features.
- 2026-08-15 — Brand voice principle noted: engineering honesty (e.g.
  documented fragilities, no merged geometries) should carry into the
  brand; no safety guarantees about routes or trail conditions.
- 2026-08-16 — **Decision: end-user product, not an API product.** A
  frontend exists. This supersedes the earlier API-first framing. The
  graph/API remains internal infrastructure. Reopens what "shipped"
  means for messaging — see open questions.
- 2026-08-16 — Decision (superseded): brand name "GetOutdoor".
- 2026-08-16 — Branding brief written for Claude Design: three
  divergent directions (instrument / culture / companion) to choose
  between, then a full system build-out from the winner.
- 2026-08-17 — **Decision: brand name is "VaiVia"**, superseding
  "GetOutdoor". Flag: "vai via" is Italian for "go away" — decide
  whether the double meaning is owned playfully or is a liability in
  the Italian launch market. Trademark/domain/app-store checks now
  apply to VaiVia; GetOutdoor's search-ownability trade-off no longer
  applies. Design brief needs re-pointing at the new name before any
  wordmark work.

## Open questions
- Which ICP within end users: MTB-first, hiking-first, or
  multi-activity from day one? Drives brand tone and icon priorities.
- Trailforks API commercial terms and OSM ODbL attribution: what do
  they permit **for a consumer app**? Terms that allowed backend use
  may not allow end-user redistribution. Unresolved and blocking on
  any GTM commitment.
- Trademark, app-store and domain availability for "VaiVia" — not yet
  checked. Verify before any asset production. Includes the Italian
  "go away" double-meaning call.
- Geographic launch scope: Lake Como/Lecco as a beachhead, or
  Alps-wide? Data coverage likely decides this.
- What is honestly claimable today vs. roadmap, now that "shipped" is
  judged by the frontend rather than the repo phases.

<!-- pmctl:product v1 -->
```json
{
  "project": "VaiVia",
  "org": "ai safe earth",
  "updated": "2026-08-17",
  "description": "Voice- and text-driven route-planning app for hikers, trail runners and mountain bikers; describe the outing in plain language and it finds or builds the route. End-user consumer product with an existing frontend, backed by a Neo4j knowledge graph merging OSM and Trailforks data.",
  "strategy": "Conversation replaces the filter panel. Bet: multi-hop graph traversal answers compound queries (trail to hut to station to trail) that Komoot, Wikiloc and AllTrails can't, and natural language is the only interface that makes them askable.",
  "productStatus": [
    { "date": "2026-08-15", "track": "docs", "title": "Project setup", "note": "Non-code project created; product-status.md established as shared state" },
    { "date": "2026-08-15", "track": "strategy", "title": "Ground truth fixed", "note": "Repo Phase 1-2 is what exists; phases 3-5 are roadmap, not features" },
    { "date": "2026-08-15", "track": "branding", "title": "Voice principle", "note": "Engineering honesty carries into brand; no route-safety guarantees" },
    { "date": "2026-08-16", "track": "strategy", "title": "Consumer pivot", "note": "End-user app with existing frontend, not an API product; supersedes API-first framing" },
    { "date": "2026-08-16", "track": "branding", "title": "Name: GetOutdoor (superseded)", "note": "Superseded 2026-08-17 by VaiVia" },
    { "date": "2026-08-16", "track": "branding", "title": "Design brief written", "note": "Three divergent directions for Claude Design; brief needs re-pointing at VaiVia" },
    { "date": "2026-08-17", "track": "branding", "title": "Name settled: VaiVia", "note": "Supersedes GetOutdoor. Flag: Italian 'vai via' = 'go away'; trademark/domain unchecked" }
  ]
}
```
