#!/usr/bin/env bash
# Nightly backup of what cannot be regenerated (docs/plan.md, "Backups"):
#   - PostGIS: the network, places, curated routes — everything the pipeline
#     and the agent improve. The value of the product.
#   - Supabase `public`: conversations, ledger, quotas, favorites, feedback.
#   - Saved routes' documents (users kept them).
# Neo4j and the packs are rebuilt from PostGIS, so they are not here.
#
#   backup.sh                     cron on the server, nightly (D5)
#
# Writes to $VAIVIA_HOME/backups/<UTC stamp>/, keeps the newest $KEEP, and —
# when RCLONE_REMOTE is set (an rclone remote pointing at R2, e.g.
# r2:vaivia-backups) — copies the new set off the box.

set -euo pipefail

HOME_DIR="${VAIVIA_HOME:-/srv/vaivia}"
ENV_FILE="${VAIVIA_ENV_FILE:-$HOME_DIR/.env}"
COMPOSE_DIR="${COMPOSE_DIR:-$HOME_DIR}"
KEEP="${KEEP:-14}"

value() {
  grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | sed -e 's/[[:space:]]#.*$//' -e 's/^"//' -e 's/"$//' || true
}

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$HOME_DIR/backups/$STAMP"
mkdir -p "$OUT"

compose() {
  docker compose -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

# PostGIS, from inside its own container: no client on the host, no port.
user="$(value POSTGIS_USER)"; user="${user:-vaivia}"
db="$(value POSTGIS_DB)"; db="${db:-vaivia_geo}"
compose exec -T postgis pg_dump -U "$user" -d "$db" -Fc > "$OUT/postgis.dump"

# Supabase public, through the pooler, with a client matching its server (17).
# Schema `public` only: auth.users is Supabase's (docs/plan.md, Backups).
docker run --rm -e PGURL="$(value DATABASE_URL)" postgres:17-alpine \
  sh -c 'pg_dump "$PGURL" --schema=public --no-owner -Fc' > "$OUT/supabase-public.dump"

docs="$(value DOCUMENTS_HOST_DIR)"; docs="${docs:-$HOME_DIR/data/documents}"
if [ -d "$docs" ]; then
  # To stdout: GNU tar reads "host:path" as remote, which a Windows path looks like.
  tar -czf - -C "$docs" . > "$OUT/documents.tar.gz"
fi

# A dump that wrote nothing is a failed backup, not an empty database.
for f in "$OUT"/*.dump; do
  [ -s "$f" ] || { echo "backup: $f is empty" >&2; exit 1; }
done
ls -l "$OUT"

# Keep the newest $KEEP sets.
ls -1d "$HOME_DIR"/backups/*/ 2>/dev/null | sort | head -n -"$KEEP" | xargs -r rm -rf

if [ -n "${RCLONE_REMOTE:-$(value RCLONE_REMOTE)}" ]; then
  remote="${RCLONE_REMOTE:-$(value RCLONE_REMOTE)}"
  rclone copy "$OUT" "$remote/$STAMP"
  echo "copied off the box to $remote/$STAMP"
fi
