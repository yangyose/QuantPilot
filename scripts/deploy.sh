#!/usr/bin/env bash
# Phase 10 §8.1：QuantPilot 一键部署脚本
# 用法：scripts/deploy.sh [--env-file .env.prod]
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${1:-.env.prod}"

cd "$(dirname "$0")/.."

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
