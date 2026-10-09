#!/usr/bin/env bash
# Deploy one image tag: preflight → migrate → pull → up → health.
#
#   deploy.sh <sha|tag>          on the server (CI calls it, D3), or by hand
#   deploy.sh <sha> --build      build the images here instead of pulling
#                                (the laptop beta, before images are on GHCR)
#   deploy.sh <sha> --check      preflight only: change nothing
#
# VAIVIA_HOME (default /srv/vaivia) holds .env, compose.prod.yml,
# docker-compose.yml, Caddyfile and supabase/migrations — CI copies them there.
# FRONT_DOOR in .env picks the profile: caddy | tunnel | tunnel-quick.
# Non-zero on any failure; nothing rolls back on its own: rollback is
# `deploy.sh <previous sha>` (docs/deploy.md).

set -euo pipefail

TAG="${1:?usage: deploy.sh <sha|tag> [--build|--check]}"
BUILD="${2:-}"
HOME_DIR="${VAIVIA_HOME:-/srv/vaivia}"
ENV_FILE="${VAIVIA_ENV_FILE:-$HOME_DIR/.env}"
COMPOSE_DIR="${COMPOSE_DIR:-$HOME_DIR}"

say() { printf '\n== %s\n' "$*"; }
die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }

value() { # the value of KEY in the env file, quotes and comments stripped
  grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | sed -e 's/[[:space:]]#.*$//' -e 's/^"//' -e 's/"$//' || true
}

# ── Preflight ────────────────────────────────────────────────────────────────
say "preflight ($ENV_FILE)"
[ -f "$ENV_FILE" ] || die "no env file at $ENV_FILE"

missing=()
for key in OPENAI_API_KEY GATEWAY_SHARED_SECRET NEO4J_PASSWORD POSTGIS_PASSWORD \
           DATABASE_URL SUPABASE_URL SUPABASE_JWT_JWKS_URL ALLOWED_ORIGINS FRONT_DOOR; do
  [ -n "$(value "$key")" ] || missing+=("$key")
done
[ ${#missing[@]} -eq 0 ] || die "empty or missing in $ENV_FILE: ${missing[*]}"

# Set by compose.prod.yml itself; present here, they would be ignored at best
# and, for the auth switch, the one setting that must never reach production.
for key in GATEWAY_DEV_NO_AUTH NODE_ENV VAIVIA_ENV NEO4J_URI BACKEND_URL; do
  grep -qE "^$key=" "$ENV_FILE" && die "$key must not be in $ENV_FILE (compose.prod.yml owns it)"
done

DOOR="$(value FRONT_DOOR)"
case "$DOOR" in
  caddy)        [ -n "$(value SITE_ADDRESS)" ] || die "FRONT_DOOR=caddy needs SITE_ADDRESS" ;;
  tunnel)       [ -n "$(value CLOUDFLARE_TUNNEL_TOKEN)" ] || die "FRONT_DOOR=tunnel needs CLOUDFLARE_TUNNEL_TOKEN" ;;
  tunnel-quick) ;;
  *)            die "FRONT_DOOR must be caddy, tunnel or tunnel-quick (got '$DOOR')" ;;
esac

PACK="$(value PACK_HOST_DIR)"; PACK="${PACK:-$HOME_DIR/data/packs/current}"
[ -e "$PACK/manifest.json" ] || die "no pack at $PACK (manifest.json missing) — the backend refuses to boot without one"

if [ "$BUILD" = "--check" ]; then
  say "preflight passed ($DOOR, pack $PACK) — nothing changed"
  exit 0
fi

# The tag this deploy runs, recorded where compose interpolates it from.
if grep -qE '^IMAGE_TAG=' "$ENV_FILE"; then
  sed -i.bak -E "s|^IMAGE_TAG=.*|IMAGE_TAG=$TAG|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
else
  printf 'IMAGE_TAG=%s\n' "$TAG" >> "$ENV_FILE"
fi

compose() {
  docker compose \
    -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/compose.prod.yml" \
    --env-file "$ENV_FILE" --profile "$DOOR" "$@"
}

# ── Images ───────────────────────────────────────────────────────────────────
if [ "$BUILD" = "--build" ]; then
  say "build $TAG locally"
  compose build backend gateway
  compose --profile jobs build jobs
else
  say "pull $TAG"
  compose pull --ignore-buildable backend gateway
  compose --profile jobs pull jobs
fi

# ── Migrate (before the new backend serves) ─────────────────────────────────
# Both are additive and idempotent, so the PREVIOUS image keeps running against
# the newer schema until `up` replaces it — which is also what makes a rollback
# safe. A one-off container, so it works on the very first deploy too.
compose up -d --wait neo4j postgis
say "migrate Supabase (public schema)"
compose run --rm --no-deps backend python -m scripts.apply_migrations
say "migrate PostGIS"
compose --profile jobs run --rm jobs python migrate.py

# ── Up ───────────────────────────────────────────────────────────────────────
say "up ($DOOR)"
compose up -d --remove-orphans --wait

# ── Health ───────────────────────────────────────────────────────────────────
say "health"
health="$(compose exec -T backend python -c \
  "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/healthz', timeout=5).read().decode())")"
printf '%s\n' "$health"
case "$health" in
  *'"database":"up"'*'"pack":"'*) ;;
  *) die "backend is not healthy with a pack: $health" ;;
esac

if [ "$DOOR" = caddy ]; then
  site="$(value SITE_ADDRESS)"
  curl -fsS -o /dev/null --max-time 15 "https://$site/healthz" \
    || die "https://$site/healthz does not answer through Caddy"
  echo "https://$site/healthz answers"
elif [ "$DOOR" = tunnel-quick ]; then
  url="$(compose logs tunnel-quick 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n1 || true)"
  echo "quick tunnel: ${url:-not up yet — compose logs tunnel-quick}"
  echo "(the address changes on every restart: infra/laptop-publish.sh rebuilds Pages for it)"
fi

say "deployed $TAG"
