#!/usr/bin/env bash
# Local backtest compute DB sync (2026-06-15).
#
# Restores the latest remote backup (pulled by the SessionStart hook into
# backups/remote/) into the dedicated local backtest DB (port 5434, volume
# quantpilot_backtest_data). Idempotent: skips if the latest backup was already
# restored. After this, run backend/scripts/run_backtest_local.py against 5434.
#
# This DB is a throwaway compute cache; the authoritative data lives on the server.
# Isolation: 5434 only (NOT prod-fallback 5432, NOT test 5433).
#
# Usage (Git Bash, repo root):
#   bash scripts/sync_local_backtest_db.sh
#   bash scripts/sync_local_backtest_db.sh --force   # re-restore even if unchanged
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="docker-compose.backtest-local.yml"
CONTAINER="qp-backtest-db-5434"
REMOTE_DIR="backups/remote"
MARKER="${REMOTE_DIR}/.last_restore"
PG_USER="${POSTGRES_USER:-quantpilot}"
PG_DB="${POSTGRES_DB:-quantpilot}"
FORCE="${1:-}"

# 1. Find the latest pulled backup
LATEST="$(ls -t ${REMOTE_DIR}/qp_*.sql.gz 2>/dev/null | head -1 || true)"
if [ -z "$LATEST" ]; then
    echo "No backup found in ${REMOTE_DIR}/ (SessionStart hook pulls one per day). Abort."
    exit 1
fi
BASE="$(basename "$LATEST")"
echo "==> Latest backup: ${BASE}"

# 2. Skip if already restored (unless --force)
if [ "$FORCE" != "--force" ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$BASE" ]; then
    echo "Already restored ${BASE}; nothing to do (use --force to re-restore)."
    exit 0
fi

# 3. Bring up the compute DB + wait healthy
echo "==> Starting ${CONTAINER}"
docker compose -f "$COMPOSE_FILE" up -d
for i in $(seq 1 30); do
    if docker exec "$CONTAINER" pg_isready -U "$PG_USER" >/dev/null 2>&1; then
        break
    fi
    sleep 2
    if [ "$i" = "30" ]; then echo "DB not ready after 60s. Abort."; exit 1; fi
done

# 4. Drop & recreate the DB (plain pg_dump has no --clean; re-restore needs a fresh DB)
echo "==> Recreating database ${PG_DB}"
docker exec "$CONTAINER" psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS ${PG_DB} WITH (FORCE);" \
    -c "CREATE DATABASE ${PG_DB} OWNER ${PG_USER};"

# 5. Restore
echo "==> Restoring ${BASE} (this can take a couple minutes)"
gunzip -c "$LATEST" | docker exec -i "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -q

# 6. Mark done + report baseline
echo "$BASE" > "$MARKER"
BASELINE="$(docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "SELECT max(trade_date) FROM daily_quote;" 2>/dev/null || echo "?")"
echo "Done. Local backtest DB (5434) restored from ${BASE}; data baseline = ${BASELINE}"
echo "Run: DATABASE_URL=postgresql+asyncpg://${PG_USER}:PWD@localhost:5434/${PG_DB} \\"
echo "     uv run python backend/scripts/run_backtest_local.py --start ... --end ... [--push]"
