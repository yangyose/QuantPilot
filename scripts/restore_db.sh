#!/usr/bin/env bash
# Phase 10 §8.3：从备份恢复数据库
# 用法：scripts/restore_db.sh backups/qp_YYYYMMDD_HHMMSS.sql.gz
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
    echo "用法: $0 <备份文件.sql.gz>"
    exit 1
fi

BACKUP_FILE="$1"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ 备份文件不存在: $BACKUP_FILE"
    exit 1
fi

# shellcheck disable=SC1090
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
fi
POSTGRES_DB="${POSTGRES_DB:-quantpilot}"
POSTGRES_USER="${POSTGRES_USER:-quantpilot}"

echo "⚠️  此操作将覆盖数据库 ${POSTGRES_DB} 中的所有数据"
read -p "继续？(yes/no) " -r
[ "$REPLY" = "yes" ] || { echo "已取消"; exit 0; }

echo "==> 从 ${BACKUP_FILE} 恢复"
gunzip -c "$BACKUP_FILE" | docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    exec -T db psql -U "$POSTGRES_USER" "$POSTGRES_DB"

echo "✅ 恢复完成"
