# Deploy runbook

How VaiVia runs outside the dev stack. The decisions and their reasons are in
`docs/plan.md` ("Deploy strategy", Phase 9); this file is the commands.

```
browser ──▶ Cloudflare Pages            static frontend (frontend/out)
        ──▶ Supabase (hosted)           sign-in, chat history, quotas
        ──▶ front door ──▶ gateway ──▶ backend ──▶ neo4j
                                          │          postgis ◀── jobs (pipeline)
            caddy | tunnel | tunnel-quick   └── pack + documents (host dirs)
```

One compose project, two files: `infra/docker-compose.yml` (neo4j, postgis —
shared with dev) + `infra/compose.prod.yml` (backend, gateway, jobs, the front
doors). The **front door** is a profile, chosen by `FRONT_DOOR` in the env file:

| `FRONT_DOOR` | What | When |
|---|---|---|
| `caddy` | Caddy on 80/443 for `SITE_ADDRESS`, automatic TLS | a server with a domain; or `localhost` for a laptop check |
| `tunnel-quick` | Cloudflare quick tunnel: random `https://*.trycloudflare.com`, no account, no open ports | **the laptop beta**, before there is a domain |
| `tunnel` | a named Cloudflare Tunnel (`CLOUDFLARE_TUNNEL_TOKEN`) at `api.<domain>` | once the domain is on Cloudflare — laptop or server |

Everything secret lives in **one env file** (template: `infra/prod.env.example`),
never in the repo, an image or CI.

---

## 1. Laptop check (nothing public)

Proves the production images and wiring on this machine. Uses the dev
Neo4j/PostGIS volumes, so the passwords are the dev ones.

```bash
# an env file OUTSIDE the repo, from the template
cp infra/prod.env.example ~/vaivia-local.env      # then fill it in:
#   FRONT_DOOR=caddy  SITE_ADDRESS=localhost  HTTP_PORT=8080  HTTPS_PORT=8443
#   PACK_HOST_DIR=<a pack dir, e.g. pipeline/packs/pack-parity>
#   DOCUMENTS_HOST_DIR=<an empty dir>   VAIVIA_ENV_FILE=<this file's path>
#   NEO4J_PASSWORD / POSTGIS_PASSWORD = the dev ones (the volumes keep them)
#   NEO4J_HTTP_PORT / NEO4J_BOLT_PORT / POSTGIS_PORT = the dev ones too: the
#     neo4j/postgis containers are SHARED with dev, and an env file without
#     them recreates neo4j on the default ports, cutting off the dev backend
#   ALLOWED_ORIGINS=http://localhost:3000

VAIVIA_HOME=. COMPOSE_DIR=infra VAIVIA_ENV_FILE=~/vaivia-local.env \
  infra/deploy.sh local --build

curl -k https://localhost:8443/healthz             # {"status":"ok","database":"up","pack":"…"}
```

`DATABASE_URL` must be the **hosted** Supabase: the backend encrypts every
non-loopback Postgres connection (`backend/core/pg.py`), and the local Supabase
speaks no TLS. For a check without chat history, leave it empty and run the
compose command directly (deploy.sh requires it).

Stop: `docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml --env-file ~/vaivia-local.env --profile caddy down`
(the dev neo4j/postgis volumes are untouched).

## 2. Laptop beta (public, no server)

The whole app on the internet from this laptop: Pages for the frontend, a quick
tunnel for the gateway, hosted Supabase for accounts. **Up only while the
laptop is on and awake.** Every request still goes through the gateway's
sign-in, rate limits and daily cost caps — they are what protect the OpenAI bill.

### Once

1. **Supabase (hosted project)** — the D0 steps that were waiting for this:
   - reset the database password (Project Settings → Database), letters and
     digits only; turn on two-step login on the account;
   - Authentication → URL Configuration: **Site URL** and **Redirect URLs** =
     `https://<project>.pages.dev` (the confirm-email and reset links land there);
   - note the project URL and the **anon** key (Project Settings → API).
2. **Cloudflare**: a free account; then on this laptop
   `npx wrangler login` and `npx wrangler pages project create <project> --production-branch main`.
3. **Env file** `~/vaivia-beta.env` from `infra/prod.env.example`:
   `FRONT_DOOR=tunnel-quick`, the hosted `DATABASE_URL` / `SUPABASE_URL` /
   `SUPABASE_JWT_JWKS_URL`, a fresh `GATEWAY_SHARED_SECRET` (`openssl rand -hex 32`),
   `ALLOWED_ORIGINS=https://<project>.pages.dev`, the dev Neo4j/PostGIS
   passwords **and ports** (as in section 1), `PACK_HOST_DIR`, `DOCUMENTS_HOST_DIR`, `VAIVIA_ENV_FILE`, and the
   three public values the publish needs:
   `PAGES_PROJECT=<project>`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.

### Each time

```bash
export VAIVIA_ENV_FILE=~/vaivia-beta.env
VAIVIA_HOME=. COMPOSE_DIR=infra infra/deploy.sh beta --build   # migrates hosted Supabase, starts everything
infra/laptop-publish.sh                                         # Pages → the tunnel's current address
```

The quick tunnel's address changes **whenever its container restarts** (a
reboot, Docker restarting). After any restart, run `infra/laptop-publish.sh`
again — it reads the new address, rebuilds the frontend for it and publishes.
The domain ends this: see section 5.

Stop being public: `docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml --env-file "$VAIVIA_ENV_FILE" --profile tunnel-quick stop tunnel-quick`.

## 3. Server (later — Phase 9 D3/D4)

Bootstrap once, on a fresh Ubuntu VPS:

```bash
adduser --disabled-password deploy && usermod -aG docker deploy   # docker installed first
ufw allow 22,80,443/tcp && ufw enable                              # 80/443 only for FRONT_DOOR=caddy
install -d -o deploy -m 750 /srv/vaivia /srv/vaivia/{data/packs,data/documents,backups,supabase}
# as deploy: /srv/vaivia/.env from infra/prod.env.example, chmod 600
# CI copies docker-compose.yml, compose.prod.yml, Caddyfile, *.sh and
# supabase/migrations into /srv/vaivia on every deploy (D3)
```

The data (D4): restore PostGIS from the workstation's dump
(`pg_dump -Fc` → `scp` → `docker compose … exec -T postgis pg_restore …`),
copy the GDS jar into the `neo4j_plugins` volume (CONTRIBUTING.md, "Start
Neo4j"), then the first `infra/publish-pack.sh`. Then `deploy.sh <sha>`.

## 4. Operate

| Task | Command |
|---|---|
| Deploy a version | `deploy.sh <sha>` (CI does it on merge to `main`, D3) |
| Preflight only | `deploy.sh <sha> --check` |
| Roll back | `deploy.sh <previous sha>` — migrations are additive, the old image runs on the newer schema |
| Logs | `docker compose … logs <service> --since 1h \| grep <request-id>` |
| Backup | `infra/backup.sh` — PostGIS + Supabase `public` + saved documents → `backups/<stamp>/`, keeps 14, copies to R2 when `RCLONE_REMOTE` is set |
| Restore (rehearse in D5) | `pg_restore` the dump into the local PostGIS / `supabase start` stack, then the pipeline tests / `scripts.smoke_supabase` |
| One-shot pipeline job | `docker compose … --profile jobs run --rm jobs python -m <module>` |

## 5. When the domain exists

1. Put the domain's DNS on Cloudflare.
2. Zero Trust → Networks → Tunnels: create a tunnel, add the public hostname
   `api.<domain>` → `http://gateway:3001`, copy its token into
   `CLOUDFLARE_TUNNEL_TOKEN`; set `FRONT_DOOR=tunnel` (or `caddy` +
   `SITE_ADDRESS=api.<domain>` on a server with ports 80/443).
3. Pages → the project → Custom domains: `<domain>`; connect the project to the
   git repo so `main` builds it, with build variables
   `NEXT_PUBLIC_GATEWAY_URL=https://api.<domain>` and the two Supabase values.
4. `ALLOWED_ORIGINS=https://<domain>`; Supabase Site URL / Redirect URLs → `https://<domain>`.
5. `deploy.sh <sha>` — and `laptop-publish.sh` is no longer needed: the address never changes.
