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
#   bash scripts/sync_local_backtest_db.sh --force        # 忽略"已恢复过"标记，重灌
#   bash scripts/sync_local_backtest_db.sh --force-wipe   # 连算力产出一起毁掉（危险）
#
# ⚠️ 算力产出保护（2026-08-26 加）：本脚本 DROP DATABASE 重建 5434。若库里已有
#    ic_baseline_pre_c1 / factor_ic_window_state 数据（本地独有、重造需数十小时），
#    默认**拒绝执行**，须显式 --force-wipe 才继续。
#    原有的 .last_restore 标记**不足以充当保护**：SessionStart 钩子每天拉一个新备份，
#    标记随即失配，第二天再跑这个脚本就会直接重灌——而面板任务要跑 31 小时，跨天必然。
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

# 2. Skip if already restored (unless --force / --force-wipe)
if [ "$FORCE" != "--force" ] && [ "$FORCE" != "--force-wipe" ] \
   && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$BASE" ]; then
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

# 3.5 算力产出保护：库里已有本地独有产出时拒绝重灌（除非 --force-wipe）
# 逐表探测（不能把两表写进一条 SQL——引用不存在的表在解析期就报错）
_rows() {
    local t="$1"
    if [ "$(docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
            "SELECT to_regclass('${t}') IS NOT NULL;" 2>/dev/null | tr -d '[:space:]')" = "t" ]; then
        docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
            "SELECT count(*) FROM ${t};" 2>/dev/null | tr -d '[:space:]'
    else
        echo 0   # 表不存在（或库还没建）→ 视为无产出，全新机器正常放行
    fi
}
GUARD_TOTAL=0
GUARD_DETAIL=""
for t in ic_baseline_pre_c1 factor_ic_window_state; do
    n="$(_rows "$t")"
    n="${n:-0}"
    GUARD_TOTAL=$(( GUARD_TOTAL + n ))
    GUARD_DETAIL="${GUARD_DETAIL}    ${t}: ${n} 行\n"
done

if [ "$GUARD_TOTAL" -gt 0 ] && [ "$FORCE" != "--force-wipe" ]; then
    echo "" >&2
    echo "拒绝执行：5434 上已有本地独有的算力产出，重灌会永久毁掉它们。" >&2
    printf "%b" "$GUARD_DETAIL" >&2
    echo "" >&2
    echo "  ic_baseline_pre_c1 不在生产库里，重造约需 57 小时；面板 IC 行同理。" >&2
    echo "  先备份：docker exec ${CONTAINER} pg_dump -U ${PG_USER} -d ${PG_DB} \\" >&2
    echo "            --data-only -t ic_baseline_pre_c1 -t factor_ic_window_state > backups/local-5434/<名字>.sql" >&2
    echo "  确认可毁再跑：bash scripts/sync_local_backtest_db.sh --force-wipe" >&2
    exit 1
fi
[ "$GUARD_TOTAL" -gt 0 ] && echo "==> --force-wipe：将毁掉 ${GUARD_TOTAL} 行算力产出后重灌"

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
