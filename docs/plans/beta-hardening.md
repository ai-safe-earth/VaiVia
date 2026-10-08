---
status: todo
step: beta-hardening
next: pick the first block to wire ("How I read it") or calibrate duration first
---

# Phase 6 — Beta hardening
- [x] Embeddings job + semantic search behind the 503-until-populated rule. `scripts/embed_trails.py` embeds the owner-ratified input (description + landscape_description + difficulty_notes) via text-embedding-3-small and stores a sha of the input on the node, so re-runs embed only changed trails (verified live: 3 embedded, then 0 on re-run). `POST /trails/semantic-search` embeds the user's text and queries the `trail_embeddings` vector index through the named template `semantic_search_trails` — the text never becomes Cypher. Before the index is populated the endpoint returns 503, verified live against the real unpopulated index before the first embedding run. Live ranking discriminates correctly: three distinct queries each ranked their intended trail first.
- [x] Gateway pins `iss`/`aud` on Supabase tokens when `SUPABASE_URL` is set; negative tests (right key, wrong claims → 401) and a live pass with a real token. The gateway has always served `/healthz` — an earlier "missing health endpoint" finding was checked against the wrong path and is retracted.
- Wire the five UI blocks that ship inactive because the frontend has no data for them (each is a backend/API change, not a styling one):
  - **"How I read it"** — `/chat` streams results but not the plan the composer merged; return the resolved constraints so a user can correct one instead of rewriting the question.
  - **Elevation profile** — the payload carries total ascent, not a height series along the route.
  - **Places layer** — POIs do not travel with map geometry.
  - **Hazards layer** — hazards are per trail, not per segment, so there is no geometry to draw them on.
  - **Coverage layer** — nothing exposes where coverage stops, which is what the copy rules require us to name.
  - The layer tab strip that hosted three of them was removed 2026-08-27 (`frontend/components/MapChrome.tsx:5-9`); in the one-UI shell (Phase 11) layers return on the map layer's top edge when the API feeds them, and the elevation profile lives in the route panel.
- Sources disclosure shows what the payload has (graph id, OSM/ODbL, the never-merged wording). OSM way ids and the match distance render as soon as the API returns them — the component already takes them; the brand mockups' sample ids are deliberately not shipped.
- Deploy plumbing → Phase 9 (D5 closes this line; dashboards are deliberately not built — see Deploy strategy).

## Carried over (2026-10-08)

- **Calibrate duration.** DIN 33466 rates the classic Grigna ascent at 10 hours where guidebooks say 6–8. Cards show *our estimate*; the schema still refuses the field until the figure is one a walker would trust.
- **Next 16 upgrade.** `next` is on ^15.1.3; the postcss and sharp advisories stay deferred until the 16 upgrade.
