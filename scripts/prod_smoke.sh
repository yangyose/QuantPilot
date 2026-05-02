#!/usr/bin/env bash
# Phase 10 §8.6：生产环境端到端冒烟
# 用法：BASE_URL=http://localhost API_PASSWORD=xxx scripts/prod_smoke.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost}"
API_USERNAME="${API_USERNAME:-admin}"
API_PASSWORD="${API_PASSWORD:-}"

if [ -z "$API_PASSWORD" ]; then
    echo "❌ 请设置 API_PASSWORD 环境变量"
    exit 1
fi

PASS=0
FAIL=0

run_check() {
    local label="$1"
    local cmd="$2"
    if eval "$cmd" > /dev/null 2>&1; then
        echo "  ✅ $label"
        PASS=$((PASS + 1))
    else
        echo "  ❌ $label"
        FAIL=$((FAIL + 1))
    fi
}

echo "==> [1] 健康检查"
run_check "GET /health → 200" "curl -fsS '$BASE_URL/health'"

echo "==> [2] 登录"
TOKEN=$(curl -fsS -X POST "$BASE_URL/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$API_USERNAME\",\"password\":\"$API_PASSWORD\"}" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['data']['access_token'])" 2>/dev/null)

if [ -z "$TOKEN" ]; then
    echo "  ❌ 登录失败"
    exit 1
fi
echo "  ✅ 登录成功"

echo "==> [3] 关键 API"
HDR="Authorization: Bearer $TOKEN"
for path in "/api/v1/setup/status" "/api/v1/notifications/unread-count" \
            "/api/v1/settings" "/api/v1/account?account_id=1"; do
    run_check "GET $path → 200" "curl -fsS -H '$HDR' '$BASE_URL$path'"
done

echo "==> [4] YAML 导出"
run_check "GET /api/v1/settings/export → text/yaml" \
    "curl -fsS -H '$HDR' -o /dev/null -w '%{content_type}' '$BASE_URL/api/v1/settings/export' | grep -q 'yaml'"

echo ""
echo "==> 结果：$PASS 通过，$FAIL 失败"
exit $FAIL
