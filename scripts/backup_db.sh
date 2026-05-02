#!/usr/bin/env bash
# Phase 10 §8.3：每日数据库备份
# 用法：scripts/backup_db.sh
# Cron（每日 02:00）： 0 2 * * * cd /path/to/QuantPilot && scripts/backup_db.sh >> logs/backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"

DATE="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$BACKUP_DIR/qp_${DATE}.sql.gz"

# shellcheck disable=SC1090
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
fi
POSTGRES_DB="${POSTGRES_DB:-quantpilot}"
POSTGRES_USER="${POSTGRES_USER:-quantpilot}"

echo "==> 备份 ${POSTGRES_DB} → ${OUT_FILE}"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
    | gzip > "$OUT_FILE"

echo "==> 清理 ${RETENTION_DAYS} 天前的旧备份"
find "$BACKUP_DIR" -name "qp_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete

echo "✅ 备份完成：$(du -h "$OUT_FILE" | cut -f1)"
