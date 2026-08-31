#!/usr/bin/env bash
# Phase 10 §8.1：QuantPilot 一键部署脚本
# 用法：scripts/deploy.sh [--env-file .env.prod]
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${1:-.env.prod}"

cd "$(dirname "$0")/.."

# ---------------------------------------------------------------------------
# ⛔ 本脚本**不适用于腾讯生产实例**（43.134.63.13）。它是 Phase 10 的一键脚本，
#    用于在一台干净机器上从零起一套栈；对**现有**生产它有三处会造成事故：
#      ① `build --pull` 会引入无关基础镜像变化（生产明令禁止）
#      ② 用 compose 起 nginx——服务器上的 compose 被就地改过（HTTPS + env 白名单），
#         且它不会做 `nginx -s reload`，backend 重建后容器 IP 变 → 必 502
#      ③ 完全不同步代码，也不备份、不核验基线、不留版本戳
#    现有生产的部署走 `scripts/deploy_prod.sh`。
#
#    用闸门而非注释：注释拦不住手快的人（也拦不住手快的 agent）。
# ---------------------------------------------------------------------------
if [ "$COMPOSE_FILE" = "docker-compose.prod.yml" ] && [ "${ALLOW_LEGACY_DEPLOY:-0}" != "1" ]; then
    cat >&2 <<'EOF'
❌ 拒绝执行：本脚本不适用于现有生产实例。

   现有生产（腾讯 43.134.63.13）请用：
       scripts/deploy_prod.sh --dry-run     # 先预检
       scripts/deploy_prod.sh               # 需 CLAUDE.md C-1 单独确认

   若你确实是在**一台干净机器上从零起栈**（新机 bootstrap / 灾备重建），
   显式声明后再跑：
       ALLOW_LEGACY_DEPLOY=1 scripts/deploy.sh
EOF
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ 环境文件不存在: $ENV_FILE"
    echo "   请先执行: cp .env.prod.example $ENV_FILE && 编辑后重试"
    exit 1
fi

echo "==> [1/4] 拉取最新镜像并构建"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build --pull

echo "==> [2/4] 启动数据库与 Redis"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d db redis

echo "==> 等待 PostgreSQL 就绪..."
until docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    exec -T db pg_isready -U "${POSTGRES_USER:-quantpilot}" > /dev/null 2>&1; do
    sleep 2
done

echo "==> [3/4] 启动前端构建容器 + 后端（后端自动跑 alembic upgrade）"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d frontend-builder backend

echo "==> [4/4] 启动 Nginx"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d nginx

echo "==> 部署完成。执行冒烟检查..."
sleep 5
HTTP_PORT="$(grep -E '^HTTP_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d ' \r' || echo 80)"
HTTP_PORT="${HTTP_PORT:-80}"
if curl -fsS "http://localhost:${HTTP_PORT}/health" > /dev/null; then
    echo "✅ QuantPilot 已启动：http://localhost:${HTTP_PORT}"
else
    echo "⚠️  健康检查失败，请查看日志："
    echo "   docker compose -f $COMPOSE_FILE logs backend"
    exit 1
fi
