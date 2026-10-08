# Redesign & Delivery Plan

Design doctrine for VaiVia. Ratified 2026-08-15 after an architecture review. The roadmap is `docs/ROADMAP.md`; each phase's work lives in `docs/plans/` (finished ones in `docs/plans/done/`).

---

## Context

The original skeleton had a sound core (then framed as a two-source OSM + Trailforks knowledge graph) but incomplete or wrong surroundings:

- The LLM layer — the product's point — was undesigned and implied raw LLM-generated Cypher (injection + hallucination risk).

(The data story changed on 2026-08-18: Trailforks is legally unavailable — API-only key grants, prior written consent required for commercial/in-software/AI use, see `docs/licensing.md` — so the graph is OSM throughout, with open-licensed Wikipedia/Wikidata enrichment over marquee places. Trailforks references below this point are historical record of what was built and verified at the time.)
- The routing model was internally inconsistent: `CONNECTS_TO` was Segment→Intersection, but GDS routing requires Intersection–Intersection edges.
- `COMPOSED_OF`/`MAPS_TO` were redundant and unordered, silently breaking distance-along-trail queries ("hut at the halfway point").
- No security, auth, observability, or deployment story.

## Product decisions

| Decision | Choice |
|---|---|
| Ambition | Public beta product (real users, abuse protection, LLM cost caps) |
| UX | Chat-first. One UI at every width: the conversation is the base layer, the composer never leaves the bottom, the map is an overlay a route tap brings up (Phase 11) |
| Frontend | Next.js (independent app) |
| Gateway | Fastify (Node/TS) — auth, rate limits, origin control, SSE proxy; **no business logic** |
| Backend | FastAPI (Python) — chat orchestration + graph query service |
| Graph DB | Neo4j 5 **Community** + APOC + GDS |
| Auth + relational store | Supabase (JWT auth; Postgres for chat history, quotas, cost ledger) |
| LLM | OpenAI (structured-output intent extraction; text-embedding-3-small) |
| Hosting | Single VPS + docker-compose now; 12-factor so pieces can move to PaaS later — see Deploy strategy |
| Environments | **One: production**, a single VPS deployed from `main`. `develop` has no server: the local stack (`infra/docker-compose.yml` + `cd infra && supabase start`) is the development environment and CI is its gate. No staging until a second person deploys or a release breaks production twice. |
| Branches | `main` = production (protected; release PRs and `hotfix/*` only). `develop` = repository default; every PR targets it. `feat/ fix/ docs/ chore/` off `develop`; `hotfix/*` off `main`, merged back into `develop` in the same sitting (`CONTRIBUTING.md:303-344`). |
| Release | PR `develop → main`, CI green, merge. The merge deploys: Actions builds the three images (`:<sha>`), pushes to GHCR, SSHes to the VPS, runs `deploy.sh <sha>` (migrate → pull → up → healthz). Tag the merge commit `vX.Y.Z` by hand once healthz passes. Rollback = `deploy.sh <previous sha>`. |
| Repo | This monorepo: `frontend/`, `gateway/`, `backend/`, `infra/`, `docs/` |
| Beta data scope | Lecco + Bergamo bboxes (`REGIONS`); catalogue generated over both |
| Streaming | SSE end-to-end from day one |

## Target architecture

```
Browser (Next.js app)
   │  HTTPS + Supabase JWT
   ▼
Caddy (TLS) ── Fastify GATEWAY  (public: the ONLY exposed service)
   │   • Supabase JWT validation (JWKS), origin/CORS control
   │   • per-user + per-IP rate limits, daily LLM quota check (Postgres)
   │   • request IDs, structured logs, SSE passthrough
   ▼  internal docker network only
FastAPI BACKEND (chat orchestration + graph query service)
   │   • /chat: OpenAI structured-output intent extraction (NEVER raw Cypher)
   │   • intent JSON validated (pydantic) → parameterized query template
   │   • conversation history + cost ledger in Supabase Postgres
   ▼
Neo4j Community + APOC + GDS (internal only)
   ▲
Ingestion jobs (Overpass w/ backoff+cache, Trailforks --mock, spatial matcher)
```

Security model: browsers never reach FastAPI, Neo4j, or OpenAI. The gateway is the single ingress; the backend trusts only the gateway (shared-secret header + network isolation); LLM calls happen server-side with per-user cost caps enforced before each call.

## Graph model (corrected)

1. **Routing graph:** `(:Intersection)-[:CONNECTS_TO {distance, elevation_change, osm_way_id, surface, highway_type}]->(:Intersection)`. Segments are edge data on the routing graph; `(:Segment)` nodes remain for trail composition and POI proximity. GDS Dijkstra projects Intersection/CONNECTS_TO consistently.
2. **`[:MAPS_TO]` is dropped.** Single ordered relationship `(:Trail)-[:COMPOSED_OF {seq, match_confidence}]->(:Segment)`; `seq` makes distance-along-trail queries correct.
3. **Region:** numeric bbox properties; `(:Trail)-[:LOCATED_IN]->(:Region)` and `(:POI)-[:LOCATED_IN]->(:Region)` created at ingestion time.
4. Routing queries never traverse semantic edges (`PASSES_BY`); all traversals bounded.

## The LLM boundary (intent contract)

`backend/chat/intents.py` defines pydantic models — `TrailSearchIntent`, `OutingIntent`, `RouteIntent` (names only; drawn as an outing), `SemanticThemeIntent`, and `ClarifyIntent` (`LoopSearchIntent` went with R7), wrapped in a `PlanEnvelope` of atomic subqueries. OpenAI structured outputs (strict json_schema, via `to_strict_schema` because strict mode rejects `oneOf`/`discriminator`) produce validated intents only; out-of-scope input → `ClarifyIntent`, which poisons the whole plan. `backend/chat/composer.py` — Python, not the model — merges subqueries (tightest-wins) and maps the result onto named parameterized templates in `backend/graph/queries.cypher`. **The model never sees or writes Cypher.**

## Deploy strategy

Ratified 2026-08-28. One VPS, one compose project at `/srv/vaivia/`, Caddy in front, images from GHCR, one script for deploy, one for backup, one for catalogue publish. Nothing below exists yet: the repo has no Dockerfile, Caddyfile, `*.sh`, `.dockerignore`, `uv.lock`, prod compose or deploy job (`infra/docker-compose.yml:152-153` still says "added when their Dockerfiles land"). Decisions live here; commands live in `docs/deploy.md` (D2).

### Topology

```
internet ──443──▶ caddy  (auto-TLS; the only published ports on the box: 80/443)
                   ├─ /api/*  → gateway:3001   handle_path strips /api; flush_interval -1 so /chat SSE is never buffered
                   └─ /*      → frontend:3000
                   compose network `internal`:  gateway → backend:8000 → neo4j:7687
neo4j bolt stays on 127.0.0.1:7687 of the VPS (the dev binding, docker-compose.yml:18): the owner's
ssh -L for catalogue loads and ingestion; never a public port
```

| Decision | Choice | Why |
|---|---|---|
| Services on the VPS | caddy, frontend, gateway, backend, neo4j | `graphhopper`: no code calls it (routing is comfort-weighted GDS Dijkstra; the `docs/routing-engine.md` migration never started). `postgis`: the pipeline's working store; `export.neo4j_load` writes its provenance row into it (`pipeline/export/neo4j_load.py:393-409`), so loads run from the workstation, never the server |
| Ingress *(recommended)* | Caddy, one domain, gateway under `/api` | same origin: no CORS preflight on the SSE POST, one cert. `NEXT_PUBLIC_GATEWAY_URL=https://<domain>/api` works because `frontend/lib/api.ts:40` concatenates paths and `frontend/app/api/` does not exist. `ALLOWED_ORIGINS=https://<domain>` (`gateway/src/config.ts:59`) |
| Gateway reachability | compose network only | `trustProxy: true` (`gateway/src/app.ts:46`) is safe only while nothing but Caddy can set `X-Forwarded-For`; one replica, since `@fastify/rate-limit` is in-process (`app.ts:77-92`) |
| Compose files | `infra/docker-compose.yml` (dev) + `infra/compose.prod.yml` overlay | overlay adds caddy + the three app services, pins `neo4j.image`, and sets every compose-network address in each service's `environment:` — `NEO4J_URI=bolt://neo4j:7687`, `BACKEND_URL=http://backend:8000`, `GATEWAY_HOST=0.0.0.0`, `ROUTE_DOCUMENTS_DIR=/documents/current`, `NODE_ENV=production` — because compose `environment` beats `env_file`, so a `.env` copied from `.env.example` cannot undo them and `config.ts:40-41` cannot be flipped off production. `graphhopper` and `postgis` get `profiles: ["dev"]` in the dev file: `up -d postgis` still starts it locally and prod `up -d` skips both; the header at `docker-compose.yml:1-3` changes with it. One neo4j definition, so the allowlist (`:37`, `:45`), transaction timeout (`:51`), memory (`:54-56`) and plugins volume (`:84`) cannot drift |
| Neo4j image *(recommended)* | pin `neo4j:5.x.y-community` in prod (dev floats on `5-community`, `:11`) | the GDS jar on `neo4j_plugins` is seeded by hand (`CONTRIBUTING.md:72-96`, 2.13.12 at `:89`) and losing it is silent — routing falls back to hop-count `shortestPath` (`CONTRIBUTING.md:68-70`); a minor bump would strand it |
| Images *(recommended: public)* | `ghcr.io/ai-safe-earth/vaivia-{backend,gateway,frontend}:<sha>` (+ `:main`) | built by Actions on push to `main`; the repo is public, so public packages and no pull token on the VPS |
| Process manager | compose `restart: unless-stopped` + healthchecks that match the **body** | backend `/healthz` answers 200 with `"database":"down"` when Neo4j is unreachable (`api/main.py:116-124`, `tests/test_api_middleware.py:67-70`), so probes match `"database":"up"`, never the status |
| VPS *(recommended)* | 4 vCPU / 8 GB / 80 GB, EU, provider daily snapshot on | neo4j alone is heap 2g + pagecache 1g (`docker-compose.yml:54-56`); the Supabase pooler is `eu-west-1` (`.env.example:73`) |

### Artefacts

All three images build from the **repo root** context with one root `.dockerignore` (`.env*`, `**/.venv`, `**/node_modules`, `**/.next`, `review/`, `infra/neo4j/plugins/`) — `.gitignore` guards git, not a build context, and a laptop `.env` baked into a public image is a leak.

| File | What |
|---|---|
| `backend/Dockerfile` | `python:3.11-slim` + uv (`backend/pyproject.toml:7`); `uv sync --frozen --no-dev` (commit `backend/uv.lock`); CMD `uvicorn api.main:app --host 0.0.0.0 --port 8000` (`api/main.py:3`). Scripts ship in the image: `docker compose exec backend python -m scripts.<x>` is the server-side console |
| `gateway/Dockerfile` | `node:22-alpine` (CI pins 22, `ci.yml:48`); `npm ci && npm run build && npm ci --omit=dev`; CMD `node dist/server.js` (`gateway/package.json:12-13`). `NODE_ENV=production` comes from the overlay, so `config.ts:47-52` refuses `GATEWAY_DEV_NO_AUTH` and `:64-68` refuses a missing secret/JWKS URL |
| `frontend/Dockerfile` | `output: 'standalone'` added to `next.config.mjs`; build ARGs `NEXT_PUBLIC_GATEWAY_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` — inlined at build (`next.config.mjs:6-8`, `lib/supabaseClient.ts:23-24`), so they are Actions **vars**, not runtime env; CMD `node server.js` on 3000 |
| `infra/compose.prod.yml` | the overlay: `env_file: /srv/vaivia/.env`; the `environment:` block above; backend mounts `/srv/vaivia/documents:/documents:ro` and `/srv/vaivia/migrations` at the path `apply_migrations.py:30` resolves (`parents[2]/infra/supabase/migrations`); json-file logging `max-size 10m, max-file 5`; healthchecks |
| `infra/Caddyfile` | the two routes above, `{$DOMAIN}` from env |
| `infra/deploy.sh` | `deploy.sh <sha>`: **preflight** (every required key non-empty in `/srv/vaivia/.env`: `GATEWAY_SHARED_SECRET NEO4J_PASSWORD OPENAI_API_KEY DATABASE_URL SUPABASE_URL SUPABASE_JWT_JWKS_URL ALLOWED_ORIGINS DOMAIN`; `GATEWAY_DEV_NO_AUTH`, `NODE_ENV`, `NEO4J_URI`, `BACKEND_URL` must be absent) → **migrate** (`docker compose exec backend python -m scripts.apply_migrations` on the running container over the mounted SQL the job just copied; idempotent, one transaction per file, `DATABASE_URL` from the env_file — the image holds no `.env` — `apply_migrations.py:7`, `:33-44`, `:77-78`; "No .sql files" exits 1, `:59-61`, fatal) → write `IMAGE_TAG=<sha>` → **pull** → **up** `-d --remove-orphans`, wait healthy → **healthz** (`GET https://$DOMAIN/api/healthz` 200, `GET https://$DOMAIN/` 200, `cypher-shell 'RETURN gds.version()'` answers). Non-zero on any failure; nothing rolls back on its own. The first deploy has no running backend: migrations are applied from the workstation as in Phase 0 (its `apply_migrations` bullet), part of the bootstrap in `docs/deploy.md` *(recommended order)* |
| `infra/backup.sh` | nightly cron: `pg_dump` through the pooler into `/srv/vaivia/backups/`, keep 14 |
| `infra/publish-catalogue.sh` | workstation-side, documents → server (below) |
| `docs/deploy.md` | the runbook: VPS bootstrap (once), deploy, rollback, restore, catalogue publish, first fill |

### Secrets

Rule: **CI holds no application secret.**

| Where | What |
|---|---|
| `/srv/vaivia/.env`, `chmod 600`, owner `deploy` | every runtime secret: `OPENAI_API_KEY`, `DATABASE_URL` (Supavisor pooler, session mode — the direct host is IPv6-only, `.env.example:71-73`; TLS by host in code, `backend/core/pg.py:22-43`, `gateway/src/quotaStore.ts:26`), `GATEWAY_SHARED_SECRET` (`openssl rand -hex 32`), a production-only `NEO4J_PASSWORD`, `SUPABASE_URL` + `SUPABASE_JWT_JWKS_URL` (hosted project, `.env.example:46-48`; the gateway pins `iss`/`aud` from the URL, `app.ts:62-63`), `ALLOWED_ORIGINS`, `DOMAIN`, `IMAGE_TAG`. Not `SUPABASE_SERVICE_ROLE_KEY`: nothing in `backend/`, `gateway/` or `frontend/` reads it (`backend/core/config.py:8-75` has no such field), so it never goes on the box. Written by hand over SSH from the password manager; never in CI, never in an image |
| Actions secrets | `DEPLOY_HOST`, `DEPLOY_SSH_KEY` (user `deploy`: docker group, no sudo, key-only), `DEPLOY_KNOWN_HOSTS` |
| Actions vars (public) | the three `NEXT_PUBLIC_*` (`.env.example:79-84`), `DOMAIN` |
| GHCR | `GITHUB_TOKEN` with `permissions: packages: write` |

Known gap: an empty `gateway_shared_secret` silently disables the backend's gateway check (`core/config.py:57-60`, `api/middleware.py:67-68`) — the preflight is the only guard until the boot refusal lands (D1, *recommended*). Go-live state: Supabase auth is parked locally as `dev-local-user` (`GATEWAY_DEV_NO_AUTH=true`); on the VPS the variable is absent and the JWKS URL is the hosted project's, which already verified a real sign-in end to end (Phase 0's auth bullet). The exposed OpenAI key and Supabase passwords rotate before anything lands on a server (D0; `CONTRIBUTING.md:349-351`).

### How data reaches the server

PostGIS stays on the workstation; the VPS receives **documents and a Neo4j load**. Every Neo4j writer runs from the workstation over `ssh -N -L 7688:127.0.0.1:7687 deploy@host` with `NEO4J_URI=bolt://127.0.0.1:7688` — env beats `.env` (`pipeline/core.py:42-44`, `neo4j_load.py:295-297`, `backend/core/config.py:9-13`).

The catalogue is two artefacts that must agree — documents on disk and the `:Route` rows that name them — and the API refuses a disagreement with a 503 (`api/routes/routing.py:209-215`, `:242-246`), so the order is the rule. `infra/publish-catalogue.sh` (`--dry-run` passes through to the loader): **rsync** `review/routes/vv2-*.json` (`review/` is gitignored, `.gitignore:78`; the glob is the loader's, `neo4j_load.py:246`; the API opens `<dir>/<route_id>.json`, `routing.py:238`, so flat) to `/srv/vaivia/documents/<publish-ts>/` (named by publish timestamp, not by a document's `run_id`: after Phase 10 the catalogue is a union of per-recipe, per-region runs, each with its own `provenance.run_id`, the field the loader stamps as `doc_run_id`, `neo4j_load.py:135`; the script records the set of run ids it shipped in `<dir>/RUNS`) → **load** `cd pipeline && uv run python -m export.neo4j_load` through the tunnel (wipes and replaces `:Route/:Place/:Start`, `:309-330`; refuses a count mismatch, `:338-342`) → **flip** `ln -sfn <publish-ts> /srv/vaivia/documents/current` (atomic; the parse cache is keyed on path + mtime + size, `routing.py:259-260`, so no restart; between load and flip re-emitted routes answer `build_mismatch` 503 — seconds, never a wrong shape) → **audit** `docker compose exec backend python -m scripts.audit_catalogue_documents`, exit 0 or the publish is not done (`audit_catalogue_documents.py:25`). Keep the last two run dirs; prune older by hand. Unpublished mapped documents ship harmlessly: the loader gates on kind (`:264-266`) and the API 404s any id the graph does not name (`:227-229`).

First fill (once, over the same tunnel, in `docs/deploy.md`): GDS jar into `neo4j_plugins` (`CONTRIBUTING.md:94-95`); `scripts.init_schema`; `ingestion.osm_ingest --region Lecco` and `--region Bergamo` (`CONTRIBUTING.md:160-171`); `scripts.embed_trails` only if `MATCH (t:Trail) RETURN count(t)` > 0 (a no-op otherwise); then `publish-catalogue.sh`. **Never `trailforks_ingest --mock` against production** (owner decision 13; the `:Trail` question is Phase 8 E5): the fixture is synthetic and it is the only `:Trail` writer, so production has no `:Trail` nodes and `search_trails` answers nothing.

### Backups, restore, rollback

| Store | Irreplaceable | Backup | Restore / rollback |
|---|---|---|---|
| Supabase Postgres `public` — conversations, ledger, quotas, favorites, feedback (0001–0006) | **yes** | `backup.sh` nightly `pg_dump` of `public` as the `postgres` role; the provider's disk snapshot is the off-box copy | `pg_restore` into the local `supabase start` stack, `scripts.smoke_supabase` green — rehearsed once (D5) |
| `auth.users` | yes | **not in our dump** — Supabase's; `public` rows reference it (`0001:7`), so a restore into a fresh project needs auth first, and on the free tier a lost project means users re-register | *(recommended)* free + dump until users justify Pro |
| Neo4j | no — OSM re-ingest + catalogue reload | none; a publish touches only `:Route/:Place/:Start` (`neo4j_load.py:309-330`), the OSM graph is untouched | bad publish: `rsync` the previous run dir back to `review/routes/`, re-run the loader through the tunnel, flip `current` back |
| Documents | no — PostGIS regenerates them | the last two publish dirs under `/srv/vaivia/documents/` | flip `current` |
| `/srv/vaivia/.env`, GDS jar | small, hand-made | password manager; `infra/neo4j/plugins/` local pin (`.gitignore:87`) | rewrite / `docker cp` |
| PostGIS (the source) | **yes** | not on the VPS — the workstation's backup, outside this section | — |

App rollback: `deploy.sh <previous sha>` — images are immutable by sha and migrations are **additive and idempotent** (`apply_migrations.py:7`; `0001:5` `create table if not exists`, `0005:7` `add column if not exists`), so the previous image runs against the newer schema. A drop or rename ships one release after its last reader is gone. `apply_migrations` replays every file every run and keeps no ledger (`:73-79`); a ledger table is the upgrade if a file ever stops being idempotent. Secrets: rotation is forward-only.

### Monitoring

| Signal | Mechanism |
|---|---|
| Up? | one cron on the VPS every 5 min: `curl -fsS https://$DOMAIN/api/healthz` (DNS, Caddy, TLS, gateway) **and** `docker inspect` health of every service → ping a healthchecks.io check; no ping → e-mail. A dead VPS, Caddy, gateway or Neo4j all stop the heartbeat; the backend stays dark (the security model above) |
| Crashed process | compose healthchecks + `restart: unless-stopped` |
| What happened | `docker compose logs <svc> --since … \| grep <request-id>` — backend logs are JSON with request ids (`backend/core/logging.py:1-5`), gateway is pino, the id is echoed to the browser (`app.ts:100-103`) |
| Deploy failed | the `deploy` job is red; the previous containers still serve unless `up` itself failed — then `deploy.sh <previous sha>` |

### Deliberately not built

| Not built | Instead | Build it when |
|---|---|---|
| Staging | CI + the local stack + `npm run test:e2e` | a second person deploys, or a release breaks production twice |
| Swarm / k8s / blue-green | compose; seconds of restart | not at this scale |
| Log shipping, dashboards (the Phase 6 deploy bullet) | `docker compose logs` | a user problem the logs cannot answer by hand |
| Auto-rollback | red job → `deploy.sh <sha>` by hand | it has been needed three times |
| Secrets manager, Terraform | `.env` chmod 600; one VPS by hand, steps in `docs/deploy.md` | a second operator / a second VPS |
| Neo4j dumps | the loader re-run + the rebuild path | never on Community (dump needs the store stopped) |
| Image builds on every PR | build on push to `main` only; D2 proves the Dockerfiles locally | a broken Dockerfile reaches `main` twice |
| GraphHopper container | GDS Dijkstra | the `docs/routing-engine.md` migration lands (JVM, ~1 GB heap, `:102-103`) |

## Release process

### What runs when

| Event | Runs |
|---|---|
| any PR, push to `develop` | the four CI jobs (`ci.yml:9-71`). No server |
| push to `main` | CI → `images` pushes `:<sha>` and `:main` to GHCR → `deploy` (`needs:` all five; `concurrency: deploy-production`, never cancelled): `scp` `infra/{compose.prod.yml,Caddyfile,deploy.sh,backup.sh}` and `infra/supabase/migrations/` to `/srv/vaivia/`, then `ssh deploy@host /srv/vaivia/deploy.sh <sha>` |
| tag `v*` | nothing; a tag names what is already live |
| catalogue change | `publish-catalogue.sh` by hand from the workstation — not a release; note the publish timestamp and its `RUNS` in `docs/plans/deploy.md` |
| cron on the VPS | `backup.sh` nightly; heartbeat every 5 min |

### Release checklist (PR `develop → main`)

1. `develop` green; `docs/ROADMAP.md` says what ships; the migrations the release needs are in the PR body (0006 is current; `deploy.sh` migrates before `up`, so "before the backend" is automatic — and 0006 widens a primary key the running backend still writes against, so an old image left up during the window gets 42P10 on `/feedback` until it is replaced).
2. `cd frontend && npm run test:e2e` against the local stack (`playwright.config.ts:1-8`: pre-deploy, never CI). The Phase 8 regression policy (README): every release PR carries `eval_golden --graph --answers` (+ `--judge` after E4); `scripts.check_intents_live` 7/7 clarify when a prompt or intent changed.
3. Graph or documents changed → publish the catalogue first: old code reads new documents through the `schema_version` gate (`routing.py:196-203`); new code reading old documents may not.
4. New migration → additive + idempotent. New env var → in `/srv/vaivia/.env` **before** merging, and in the `deploy.sh` preflight in the same PR.
5. PR titled `release: vX.Y.Z`; merge — no rebase, no cherry-pick (`CONTRIBUTING.md:333-335`).
6. `deploy` green → `git tag -a vX.Y.Z <merge sha> && git push origin vX.Y.Z`. Red → read the job; `deploy.sh <previous sha>` if production is affected; fix forward on `develop`.
7. `E2E_BASE_URL=https://<domain> E2E_LIVE=1 npm run test:e2e` (`playwright.config.ts:20`, `e2e/smoke.spec.ts:30`; a dedicated production test account; one OpenAI turn). `docs/plans/deploy.md` gets the sha in production.

### Hotfix

`git switch -c hotfix/<x> main` → PR to `main` → merge (deploys) → `git switch develop && git merge main` in the same sitting (`CONTRIBUTING.md:336-338`). Tag with a patch bump.

## Delivery phases

Moved to `docs/ROADMAP.md` and `docs/plans/` on 2026-10-08. Phase numbers in code
comments map to plans: 0–5 `done/foundations`, 6 `beta-hardening`, 7 `done/bergamo-refactor`,
8 `evals`, 9 `deploy`, 10 `planner-themes`, 11 `done/one-ui`, 12 `done/on-demand-routes`.

## Verification per phase

- Every phase lands with CI green, fully offline (`--mock`).
- Phase 1: run ingestion twice, assert node/relationship counts unchanged; cookbook queries return expected fixture results.
- Phases 2–3: end-to-end curl through gateway → backend → Neo4j on compose; auth/limit contract tests.
- Phase 4: golden set of NL queries → expected intents (50 entries incl. Italian phrasings and multiturn conversations; `--graph` checks retrieval, `--answers` checks the raw answer against the code-checkable prompt rules); adversarial set never produces a write. Scores logged per run in `backend/eval_runs.jsonl`. Phase 8 carries the eval plan.
- Phase 5: Playwright smoke — sign in, ask "easy trail near a lake", see result cards + map polyline.
- Phase 8: every full `eval_golden` run appends its line; a PR touching prompts/intents/composer/dataset carries a fresh decomposition line.
- Phase 9: `GET https://<domain>/api/healthz` 200 through Caddy after a tagged merge; one rollback and one restore rehearsed.
- Phase 10: pipeline tests pure; one regeneration per recipe over both provinces; `scripts.audit_catalogue_documents` clean; `export.review_bundle` refreshed; golden `--graph` after F3 and F5.
- Phase 11: the 360×640 Playwright project green against the live stack.
