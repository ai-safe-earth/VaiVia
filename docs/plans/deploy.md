---
status: active
step: deploy
next: D0 rotate the OpenAI key and Supabase passwords; D1+D2 are built on feat/prod-compose (f487814, eb561b3), PRs to develop pending
---

# Phase 9 — Deploy (ratified 2026-08-28)

- [ ] D0 (no branch) — rotate the OpenAI key and the Supabase database and account passwords (the two high-severity handoff blockers; this supersedes the handoff next-step); mint production-only `NEO4J_PASSWORD` and `GATEWAY_SHARED_SECRET`; register the domain; clear the two blockers in this plan. Nothing in D1–D5 lands on a server before this.
- [ ] D1 `feat/dockerfiles` — `backend/Dockerfile` (root context, `uv sync --frozen --no-dev`, commit `backend/uv.lock`), `gateway/Dockerfile`, `frontend/Dockerfile` + `output: 'standalone'` in `next.config.mjs`, one root `.dockerignore`; `docker-compose.yml:152-153` comment updated. *(recommended)* `Settings.vaivia_env` + boot refusal when it is `production` and `gateway_shared_secret` is empty (`core/config.py:57-60`, `api/middleware.py:67-68`), mirroring `gateway/src/config.ts:29-37`, tested like `gateway/test/config.test.ts:6-14`. Done when each image boots locally and its healthz answers.
- [ ] D2 `feat/prod-compose` — `infra/compose.prod.yml` (caddy + three app services; the `environment:` block; neo4j image pinned; body-checking healthchecks; logging caps; `/srv/vaivia/{documents,migrations}` mounts), `profiles: ["dev"]` on graphhopper/postgis and the `:1-3` header in the dev file, `infra/Caddyfile` (`handle_path /api/*`, `flush_interval -1`), `infra/deploy.sh` (preflight → migrate → pull → up → healthz + `gds.version()`), `infra/backup.sh`, `docs/deploy.md` with the once-only bootstrap (`deploy` user, ufw 22/80/443, `/srv/vaivia/{.env,documents,migrations,backups}`, first migrations from the workstation per Phase 0, GDS jar per `CONTRIBUTING.md:94`). Done when the prod compose runs on the laptop with `DOMAIN=localhost` and a streamed `/chat` answer arrives token by token through Caddy.
- [ ] D3 `feat/ci-deploy` — `images` (GHCR push, `permissions: packages: write`, packages public) and `deploy` jobs in `ci.yml`, both `if: github.ref == 'refs/heads/main'`, `deploy` `needs:` all five; secrets/vars as listed; `main`'s required status checks on GitHub updated to the four suites + `images` (`deploy` is **not** required — it never runs on a PR); VPS bootstrapped per `docs/deploy.md`. First real deploy = `v0.1.0`; rollback rehearsed once with `deploy.sh <previous sha>` on the live box. Fix `CONTRIBUTING.md:256`, `:319`, `:379` ("three" jobs — four, five with `images`) in the same PR.
- [ ] D4 `feat/publish-pack` (was `publish-catalogue`; renamed 2026-09-02 by Phase 12) — `infra/publish-pack.sh` (rsync `packs/<run_id>/` + places/starts load through the tunnel → backend reload → audit, `--dry-run`); first fill over the tunnel (schema, both regions' places and starts, embeddings, the pack); the backend's `/healthz` reports the pack `run_id`; an on-demand route's geometry served over `https://<domain>/api`. Until slice 7 of Phase 12 lands, the catalogue documents ship alongside the pack. Go-live checks in this plan: `GATEWAY_DEV_NO_AUTH` absent, hosted JWKS, `ALLOWED_ORIGINS`, Supabase Site URL / redirect URLs pointed at `<domain>` (the confirm-email flow, Phase 5's sign-in bullet), post-deploy e2e green. E5 decided before this lands.
- [ ] D5 `chore/backups-and-uptime` — `backup.sh` in cron (keep 14); heartbeat cron → healthchecks.io, e-mail verified by stopping the backend for six minutes; **one restore rehearsed**: last night's `pg_dump` into the local Supabase, `scripts.smoke_supabase` green; date recorded here. Closes Phase 6's deploy line.

## Blockers (carried over from handoff.md, 2026-10-08)

- **high** — the OpenAI API key was shared in plaintext; rotate it before any deployment (D0).
- **high** — the Supabase account password was shared in plaintext and is also in the git history of the deleted `handoff.md`; change it before any deployment (D0).
- v0.1.0 is tagged on `a8ddd98` and pushed, so production has a release to roll back to.

## Not bugs

- Supabase auth is parked locally: `GATEWAY_DEV_NO_AUTH=true` runs everything as `dev-local-user`; the gateway refuses it in production.
