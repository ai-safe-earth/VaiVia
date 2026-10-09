---
status: active
step: deploy
next: L0, the named tunnel and the gateway secret; then merge chore/cleanup and D1+D2 so Pages can build main
---

# Phase 9 — Deploy (ratified 2026-08-28)

## Stage 1 — the laptop on vaivia.dev (decided 2026-10-09)

Users reach `vaivia.dev`. Everything except the front end runs in Docker on the
owner's laptop and is public only while the laptop is on. The VPS (D3, and the
server halves of D4 and D5) becomes Stage 2, unchanged in design.

    vaivia.dev       → Cloudflare Pages (static frontend, D1's `output: 'export'`)
    api.vaivia.dev   → named Cloudflare Tunnel → gateway:3001 (auth check, CORS, rate limits, quota)
    auth.vaivia.dev  → same tunnel → local Supabase Auth, path `/auth/v1/*` only
    gateway → backend → Neo4j, local Supabase Postgres — Docker network only

Decided: no Caddy (Cloudflare holds the certificate; the tunnel routes); the
gateway stays Fastify in Docker, not a Worker (a Worker would need the backend
public); Supabase is the LOCAL stack, so accounts live on the laptop and
backups are not optional.

- [ ] L0 (no branch) — *Done 2026-10-09:* supabase.com login is GitHub with
  two-step login; the paused hosted projects are deleted; the Pages project
  `vaivia` exists, connected to GitHub. *Left:* the named tunnel
  (`CLOUDFLARE_TUNNEL_TOKEN`) in the Cloudflare account that holds vaivia.dev;
  a production `GATEWAY_SHARED_SECRET` (`openssl rand -hex 32`, kept in the env
  file outside the repo). Email: none for now — **closed beta**, accounts
  created by hand (L2); a real sender is L4.
- [ ] L0b (no branch) — Pages `vaivia` settings: production branch `main`,
  preview builds off, root `frontend`, build `npm run build`, output `out`,
  build variables `NEXT_PUBLIC_GATEWAY_URL=https://api.vaivia.dev`,
  `NEXT_PUBLIC_SUPABASE_URL=https://auth.vaivia.dev`,
  `NEXT_PUBLIC_SUPABASE_ANON_KEY` (the local stack's public key); custom
  domains `vaivia.dev` and `www.vaivia.dev` (redirected to the apex). The
  static build (`output: 'export'`) exists only on `feat/prod-compose` until
  D1 reaches `main`, so the first good build follows `chore/cleanup` →
  `develop`, D1+D2 → `develop`, release `develop` → `main`.
- [ ] L1 `feat/db-tls-opt-out` — `backend/core/pg.py:22` and
  `gateway/src/quotaStore.ts:16` accept an explicit `sslmode=disable` in the
  connection string; everything else still encrypts by default (fail secure
  stays the rule). Tests for both: loopback → plain, `sslmode=disable` → plain,
  any other host → TLS, unparseable → TLS.
- [ ] L2 `feat/laptop-on-domain` — local Supabase public config in
  `infra/supabase/config.toml`: public URL and token issuer
  `https://auth.vaivia.dev`, `site_url` and redirect URLs `https://vaivia.dev`,
  public signups OFF (`enable_signup = false` in `[auth]` and `[auth.email]`):
  the owner creates each tester in local Studio (`127.0.0.1:54323`,
  Authentication → Add user, auto-confirm) and sends the password by hand;
  resets are manual too. Tunnel ingress for the two hostnames, auth limited to
  `/auth/v1/*`, and a Cloudflare rule BLOCKING `/auth/v1/admin/*` (the admin
  API sits under the same path and answers anyone holding the service key);
  confirm the local keys are not the stack's shared defaults. Env:
  `ALLOWED_ORIGINS=https://vaivia.dev`, `SUPABASE_URL=https://auth.vaivia.dev`,
  the gateway fetching its key list (JWKS) over the Docker network. Front end
  built with `NEXT_PUBLIC_GATEWAY_URL=https://api.vaivia.dev`,
  `NEXT_PUBLIC_SUPABASE_URL=https://auth.vaivia.dev`. `docs/deploy.md` gains
  this as the laptop section (it replaces the quick-tunnel and hosted-Supabase
  "laptop beta"). Done when a tester account created in Studio signs in from
  another network, gets a streamed `/chat` answer token by token on
  vaivia.dev, and `https://auth.vaivia.dev/auth/v1/admin/users` is refused.
- [ ] L3 `chore/laptop-backups` — `backup.sh` nightly while the laptop is on
  (`pg_dump` of `public` AND `auth`: here the accounts are ours, unlike hosted),
  plus the Neo4j dump; a copy off the laptop; one restore rehearsed.
  Go-live checks: `GATEWAY_DEV_NO_AUTH` absent, `/rest/v1` and Studio NOT
  reachable from outside, a sign-in rate limit seen to trigger.
- [ ] L4 `feat/auth-email` (when signup opens) — an email-sending service
  (SMTP; Resend or Brevo free tier) with vaivia.dev verified as sender
  (its DNS records in Cloudflare); `[auth.email.smtp]` in `config.toml`, the
  password from the env file; signups on with email confirmation; the
  confirm and reset links land on `https://vaivia.dev`. Done when a stranger
  signs up with a real address, confirms it, and resets a password.

## The D-steps (ratified 2026-08-28; D1 and D2 serve both stages, D0 and D3–D5 are Stage 2, the VPS)

- [ ] D0 (no branch) — Stage 2; the secrets work for Stage 1 is L0. rotate the OpenAI key and the Supabase database and account passwords (the two high-severity handoff blockers; this supersedes the handoff next-step); mint production-only `NEO4J_PASSWORD` and `GATEWAY_SHARED_SECRET`; register the domain; clear the two blockers in this plan. Nothing in D1–D5 lands on a server before this.
- [ ] D1 `feat/dockerfiles` — `backend/Dockerfile` (root context, `uv sync --frozen --no-dev`, commit `backend/uv.lock`), `gateway/Dockerfile`, `frontend/Dockerfile` + `output: 'standalone'` in `next.config.mjs`, one root `.dockerignore`; `docker-compose.yml:152-153` comment updated. *(recommended)* `Settings.vaivia_env` + boot refusal when it is `production` and `gateway_shared_secret` is empty (`core/config.py:57-60`, `api/middleware.py:67-68`), mirroring `gateway/src/config.ts:29-37`, tested like `gateway/test/config.test.ts:6-14`. Done when each image boots locally and its healthz answers.
- [ ] D2 `feat/prod-compose` — `infra/compose.prod.yml` (caddy + three app services; the `environment:` block; neo4j image pinned; body-checking healthchecks; logging caps; `/srv/vaivia/{documents,migrations}` mounts), `profiles: ["dev"]` on graphhopper/postgis and the `:1-3` header in the dev file, `infra/Caddyfile` (`handle_path /api/*`, `flush_interval -1`), `infra/deploy.sh` (preflight → migrate → pull → up → healthz + `gds.version()`), `infra/backup.sh`, `docs/deploy.md` with the once-only bootstrap (`deploy` user, ufw 22/80/443, `/srv/vaivia/{.env,documents,migrations,backups}`, first migrations from the workstation per Phase 0, GDS jar per `CONTRIBUTING.md:94`). Done when the prod compose runs on the laptop with `DOMAIN=localhost` and a streamed `/chat` answer arrives token by token through Caddy. Its quick-tunnel and hosted-Supabase laptop beta is replaced by Stage 1's L2; the `tunnel` profile and the images stay.
- [ ] D3 `feat/ci-deploy` — Stage 2 (VPS). `images` (GHCR push, `permissions: packages: write`, packages public) and `deploy` jobs in `ci.yml`, both `if: github.ref == 'refs/heads/main'`, `deploy` `needs:` all five; secrets/vars as listed; `main`'s required status checks on GitHub updated to the four suites + `images` (`deploy` is **not** required — it never runs on a PR); VPS bootstrapped per `docs/deploy.md`. First real deploy = `v0.1.0`; rollback rehearsed once with `deploy.sh <previous sha>` on the live box. Fix `CONTRIBUTING.md:256`, `:319`, `:379` ("three" jobs — four, five with `images`) in the same PR.
- [ ] D4 Stage 2 (VPS): `feat/publish-pack` (was `publish-catalogue`; renamed 2026-09-02 by Phase 12) — `infra/publish-pack.sh` (rsync `packs/<run_id>/` + places/starts load through the tunnel → backend reload → audit, `--dry-run`); first fill over the tunnel (schema, both regions' places and starts, embeddings, the pack); the backend's `/healthz` reports the pack `run_id`; an on-demand route's geometry served over `https://<domain>/api`. Until slice 7 of Phase 12 lands, the catalogue documents ship alongside the pack. Go-live checks in this plan: `GATEWAY_DEV_NO_AUTH` absent, hosted JWKS, `ALLOWED_ORIGINS`, Supabase Site URL / redirect URLs pointed at `<domain>` (the confirm-email flow, Phase 5's sign-in bullet), post-deploy e2e green. E5 decided before this lands.
- [ ] D5 `chore/backups-and-uptime` — Stage 2 (VPS); Stage 1's restore rehearsal is L3. `backup.sh` in cron (keep 14); heartbeat cron → healthchecks.io, e-mail verified by stopping the backend for six minutes; **one restore rehearsed**: last night's `pg_dump` into the local Supabase, `scripts.smoke_supabase` green; date recorded here. Closes Phase 6's deploy line.

## Blockers (carried over from handoff.md, 2026-10-08)

- ~~**high** — the OpenAI API key was shared in plaintext.~~ Rotated 2026-10-09.
- ~~**high** — the Supabase account password was shared in plaintext.~~ Closed 2026-10-09: the hosted projects it belonged to are deleted, and the account signs in through GitHub with two-step login. The old text stays in the git history of `handoff.md`, harmless now.
- v0.1.0 is tagged on `a8ddd98` and pushed, so production has a release to roll back to.

## Not bugs

- Supabase auth is parked locally: `GATEWAY_DEV_NO_AUTH=true` runs everything as `dev-local-user`; the gateway refuses it in production.
