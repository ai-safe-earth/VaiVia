#!/usr/bin/env bash
# The laptop beta's last step: point the frontend on Cloudflare Pages at the
# current quick-tunnel address and publish it (docs/deploy.md, "Laptop beta").
#
#   infra/laptop-publish.sh            from the repo root, after deploy.sh
#
# Why a script: NEXT_PUBLIC_GATEWAY_URL is inlined into the static build, and
# a quick tunnel's address changes every time its container restarts. So the
# frontend is rebuilt for the address the tunnel has NOW. With a domain and a
# named tunnel the address never changes, and Pages builds from git instead.
#
# Needs, in the env file: PAGES_PROJECT, NEXT_PUBLIC_SUPABASE_URL,
# NEXT_PUBLIC_SUPABASE_ANON_KEY (all public values). Needs once:
# `npx wrangler login` (opens the browser; the owner's Cloudflare account).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${VAIVIA_ENV_FILE:?set VAIVIA_ENV_FILE to the laptop prod env file}"

value() {
  grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | sed -e 's/[[:space:]]#.*$//' -e 's/^"//' -e 's/"$//' || true
}

PROJECT="$(value PAGES_PROJECT)"
SB_URL="$(value NEXT_PUBLIC_SUPABASE_URL)"
SB_ANON="$(value NEXT_PUBLIC_SUPABASE_ANON_KEY)"
for pair in "PAGES_PROJECT:$PROJECT" "NEXT_PUBLIC_SUPABASE_URL:$SB_URL" "NEXT_PUBLIC_SUPABASE_ANON_KEY:$SB_ANON"; do
  [ -n "${pair#*:}" ] || { echo "laptop-publish: ${pair%%:*} is empty in $ENV_FILE" >&2; exit 1; }
done

# The gateway refuses every origin it was not told about.
case "$(value ALLOWED_ORIGINS)" in
  *"https://$PROJECT.pages.dev"*) ;;
  *) echo "laptop-publish: ALLOWED_ORIGINS must include https://$PROJECT.pages.dev" >&2; exit 1 ;;
esac

URL="$(docker compose -f "$ROOT/infra/docker-compose.yml" -f "$ROOT/infra/compose.prod.yml" \
  --env-file "$ENV_FILE" --profile tunnel-quick logs tunnel-quick 2>&1 \
  | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n1 || true)"
[ -n "$URL" ] || { echo "laptop-publish: no quick-tunnel address — is FRONT_DOOR=tunnel-quick deployed?" >&2; exit 1; }

# A fresh address takes a moment to reach DNS; publishing before it answers
# ships a frontend that cannot reach its own gateway.
for _ in $(seq 1 30); do
  curl -fsS -o /dev/null --max-time 5 "$URL/healthz" && break
  sleep 3
done
curl -fsS -o /dev/null --max-time 5 "$URL/healthz" \
  || { echo "laptop-publish: $URL/healthz does not answer" >&2; exit 1; }
echo "gateway: $URL"

cd "$ROOT/frontend"
NEXT_PUBLIC_GATEWAY_URL="$URL" \
NEXT_PUBLIC_SUPABASE_URL="$SB_URL" \
NEXT_PUBLIC_SUPABASE_ANON_KEY="$SB_ANON" \
  npm run build

# --branch main: the production deployment at https://<project>.pages.dev,
# not a per-upload preview address the gateway would not allow.
npx wrangler pages deploy out --project-name "$PROJECT" --branch main --commit-dirty=true

echo
echo "published: https://$PROJECT.pages.dev  →  $URL"
