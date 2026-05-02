#!/usr/bin/env bash
# QuantPilot 开发环境一键引导
# 用法：scripts/bootstrap_dev.sh [--seed | --no-seed]
#
# 流程：
#   1) 检查 docker / docker compose
#   2) 若 .env 不存在，从 .env.example 生成；自动填充 bcrypt 哈希、JWT key、随机 DB/Redis 密码
#   3) docker compose up -d db redis backend
#   4) 等待 PostgreSQL 就绪
#   5) 跑 alembic upgrade head
#   6) 默认植入演示数据（可用 --no-seed 跳过）
#   7) 输出登录信息

set -euo pipefail

# ============== 参数解析 ==============
SEED=1
for arg in "$@"; do
    case "$arg" in
        --seed)    SEED=1 ;;
        --no-seed) SEED=0 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "未知参数: $arg"; exit 1 ;;
    esac
done

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
COMPOSE="docker compose -f docker-compose.dev.yml"

# ============== 颜色与日志 ==============
if [ -t 1 ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; BLUE=""; NC=""
fi
info()  { echo "${BLUE}==>${NC} $*"; }
ok()    { echo "${GREEN}✅${NC} $*"; }
warn()  { echo "${YELLOW}⚠️ ${NC} $*"; }
fatal() { echo "${RED}❌${NC} $*" >&2; exit 1; }

# ============== [1/6] 前置检查 ==============
info "[1/6] 检查前置依赖"

command -v docker >/dev/null 2>&1 || fatal "docker 未安装。请安装 Docker Desktop (https://www.docker.com/products/docker-desktop)"
docker compose version >/dev/null 2>&1 || fatal "docker compose v2 未启用。请升级 Docker Desktop"
docker info >/dev/null 2>&1 || fatal "Docker daemon 未运行。请启动 Docker Desktop"
ok "Docker $(docker version --format '{{.Server.Version}}') / Compose v2"

# ============== [2/6] .env 生成 ==============
info "[2/6] 准备 .env"

ENV_FILE="$ROOT/.env"
EXAMPLE_FILE="$ROOT/.env.example"

if [ -f "$ENV_FILE" ]; then
    ok ".env 已存在，跳过生成（如需重建请删除后重跑）"
else
    [ -f "$EXAMPLE_FILE" ] || fatal "$EXAMPLE_FILE 不存在"
    cp "$EXAMPLE_FILE" "$ENV_FILE"

    # ---- 询问管理员密码 ----
    if [ -n "${SKIP_PROMPT:-}" ]; then
        ADMIN_PASSWORD="${ADMIN_PASSWORD:-Quantpilot123!}"
        warn "SKIP_PROMPT=1，使用默认密码：$ADMIN_PASSWORD"
    else
        echo ""
        echo "请设置管理员密码（仅本机开发使用，至少 8 位）："
        while true; do
            read -rsp "密码: " p1; echo
            read -rsp "确认: " p2; echo
            [ "$p1" != "$p2" ] && { warn "两次输入不一致"; continue; }
            [ ${#p1} -lt 8 ] && { warn "至少 8 位"; continue; }
            ADMIN_PASSWORD="$p1"
            break
        done
    fi

    # ---- 生成 bcrypt 哈希（用 python:3.12-slim 一次性容器，无需本地 Python）----
    info "生成 bcrypt 哈希..."
    HASH=$(docker run --rm python:3.12-slim sh -c \
        "pip install -q bcrypt >&2 && python -c \"import bcrypt,os; print(bcrypt.hashpw(os.environ['P'].encode(), bcrypt.gensalt()).decode())\"" \
        -e P="$ADMIN_PASSWORD" 2>/dev/null) || \
    HASH=$(P="$ADMIN_PASSWORD" docker run --rm -e P python:3.12-slim sh -c \
        "pip install -q bcrypt >/dev/null && python -c \"import bcrypt,os; print(bcrypt.hashpw(os.environ['P'].encode(), bcrypt.gensalt()).decode())\"")
    [ -n "$HASH" ] || fatal "bcrypt 哈希生成失败"
    ok "bcrypt 哈希已生成"

    # ---- 生成 JWT 密钥 ----
    if command -v openssl >/dev/null 2>&1; then
        JWT_KEY="$(openssl rand -hex 64)"
    else
        JWT_KEY="$(docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_hex(64))')"
    fi
    ok "JWT 密钥已生成 (${#JWT_KEY} hex)"

    # ---- 随机 DB / Redis 密码 ----
    if command -v openssl >/dev/null 2>&1; then
        DB_PASS="$(openssl rand -base64 18 | tr -d '/=+\n' | head -c 24)"
        REDIS_PASS="$(openssl rand -base64 18 | tr -d '/=+\n' | head -c 24)"
    else
        DB_PASS="$(docker run --rm python:3.12-slim python -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(24)))')"
        REDIS_PASS="$(docker run --rm python:3.12-slim python -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(24)))')"
    fi

    # ---- 写入 .env ----
    # macOS 与 GNU sed 行为差异 + Windows 路径差异：统一用容器内 python 替换
    PY_SCRIPT="$(mktemp)"
    cat > "$PY_SCRIPT" <<'PYEOF'
import re, sys
path, h, jwt, dbp, rp = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
with open(path, encoding="utf-8") as f:
    text = f.read()
def sub(key, val, quote_single=False):
    global text
    v = f"'{val}'" if quote_single else val
    pattern = rf"^{re.escape(key)}=.*$"
    if re.search(pattern, text, flags=re.M):
        text = re.sub(pattern, f"{key}={v}", text, flags=re.M)
    else:
        text += f"\n{key}={v}\n"
sub("ADMIN_PASSWORD_HASH", h, quote_single=True)
sub("JWT_SECRET_KEY", jwt)
sub("DB_PASSWORD", dbp)
sub("REDIS_PASSWORD", rp)
with open(path, "w", encoding="utf-8") as f:
    f.write(text)
PYEOF

    if command -v python3 >/dev/null 2>&1; then
        python3 "$PY_SCRIPT" "$ENV_FILE" "$HASH" "$JWT_KEY" "$DB_PASS" "$REDIS_PASS"
    else
        docker run --rm -v "$ROOT:/work" -v "$PY_SCRIPT:/tmp/sub.py:ro" \
            -w /work python:3.12-slim python /tmp/sub.py \
            ".env" "$HASH" "$JWT_KEY" "$DB_PASS" "$REDIS_PASS"
    fi
    rm -f "$PY_SCRIPT"

    ok ".env 已生成 ($ENV_FILE)"
fi

# ============== [3/6] 启动容器 ==============
info "[3/6] 启动 db / redis / backend"
$COMPOSE up -d --build

# ============== [4/6] 等待 PostgreSQL 就绪 ==============
info "[4/6] 等待 PostgreSQL 就绪"
for i in $(seq 1 30); do
    if $COMPOSE exec -T db pg_isready -U quantpilot >/dev/null 2>&1; then
        ok "PostgreSQL 就绪 (${i}s)"
        break
    fi
    sleep 1
    [ "$i" = 30 ] && fatal "PostgreSQL 30 秒内未就绪，查看日志：$COMPOSE logs db"
done

# 等 backend 容器跑起来（依赖 db healthy + 镜像首次构建）
info "等待 backend 容器启动"
for i in $(seq 1 60); do
    if $COMPOSE exec -T backend true >/dev/null 2>&1; then
        ok "backend 容器就绪"
        break
    fi
    sleep 1
    [ "$i" = 60 ] && fatal "backend 60 秒内未启动，查看日志：$COMPOSE logs backend"
done

# ============== [5/6] 跑迁移 ==============
info "[5/6] 跑数据库迁移 (alembic upgrade head)"
$COMPOSE exec -T backend uv run alembic upgrade head

# ============== [6/6] 演示数据 ==============
if [ "$SEED" = 1 ]; then
    info "[6/6] 植入演示数据"
    $COMPOSE exec -T backend uv run python scripts/seed_demo_data.py
    ok "演示数据已就绪"
else
    info "[6/6] 跳过演示数据 (--no-seed)"
fi

# ============== 验证 + 提示 ==============
echo ""
sleep 2
if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    ok "QuantPilot 后端已启动：http://localhost:8000"
else
    warn "健康检查失败，查看日志：$COMPOSE logs backend"
fi

echo ""
echo "${GREEN}===================== 启动成功 =====================${NC}"
echo " 后端 API   : http://localhost:8000"
echo " API 文档   : http://localhost:8000/docs"
echo " 管理员账号 : admin"
echo " 密码       : ${ADMIN_PASSWORD:-（已存在 .env，未重新生成）}"
echo ""
echo " 启动前端（另开一个终端）："
echo "   cd frontend && npm install && npm run dev"
echo ""
echo " 停止服务： $COMPOSE down"
echo "${GREEN}====================================================${NC}"
