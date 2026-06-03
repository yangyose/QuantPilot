#!/usr/bin/env bash
# QuantPilot — Oracle Cloud ARM（Ampere A1）迁移引导脚本（一次性）
#
# 把本地 pg_dump 整库迁到全新 Oracle ARM 服务器并起生产栈。
# 配套 deployment.md §14（ARM64 部署）。
#
# 用法：
#   scripts/bootstrap_oracle_arm.sh <path/to/qp_YYYYMMDD_HHMMSS.sql.gz>
#
# 前置（在服务器上）：
#   1) 已 git clone 本仓库，并在仓库根目录执行本脚本
#   2) 已准备好 .env.prod（从本地 scp 过来，或先跑 scripts/bootstrap_prod.sh 生成）
#   3) 已把本地 scripts/backup_db.sh 产出的 dump（qp_*.sql.gz）传到服务器
#   4) Docker 已装且当前用户在 docker 组（docker ps 不报权限错）
#
# 流程：架构/Docker 体检 → arm64 构建 → 起 db → 灌 dump（空库）→ 起 backend/前端/nginx → 冒烟
#
# 关键顺序：dump 必须灌在**全新空库**上（起 backend 前）。dump 已含 head schema，
# 灌完 backend 启动时 alembic upgrade 自动 no-op；若先起 backend 让 alembic 建表，
# 再灌 plain-SQL dump 会与既有表冲突。
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"

# ============== 颜色 ==============
if [ -t 1 ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; BLUE=""; NC=""
fi
info()  { echo "${BLUE}==>${NC} $*"; }
ok()    { echo "${GREEN}✅${NC} $*"; }
warn()  { echo "${YELLOW}⚠️ ${NC} $*"; }
fatal() { echo "${RED}❌${NC} $*" >&2; exit 1; }

dc() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

# ============== 0. 参数 ==============
DUMP="${1:-}"
[ -n "$DUMP" ] || fatal "用法: $0 <dump.sql.gz>（本地 backup_db.sh 产出的 qp_*.sql.gz）"
[ -f "$DUMP" ] || fatal "dump 文件不存在: $DUMP"

# ============== 1. 架构 + Docker 体检 ==============
info "[1/7] 体检：架构 + Docker"
ARCH="$(uname -m)"
if [ "$ARCH" = "aarch64" ]; then
    ok "架构 aarch64（ARM64）"
else
    warn "当前架构 $ARCH（本脚本面向 Oracle ARM=aarch64；x86 也能跑，仅提示）"
fi
command -v docker >/dev/null 2>&1 || fatal \
    "Docker 未安装。先执行：curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker \$USER（然后重新登录）"
dc version >/dev/null 2>&1 || fatal "docker compose 插件缺失（需 Docker Compose v2）"
docker ps >/dev/null 2>&1 || fatal \
    "当前用户无 docker 权限。执行：sudo usermod -aG docker \$USER，重新登录后再跑本脚本"
ok "Docker + compose 就绪"

# ============== 2. .env.prod ==============
info "[2/7] 校验 $ENV_FILE"
[ -f "$ENV_FILE" ] || fatal \
    "$ENV_FILE 不存在。从本地 scp（推荐，沿用同一套密钥）：scp .env.prod user@server:仓库路径/  或跑 scripts/bootstrap_prod.sh 生成"
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a
POSTGRES_DB="${POSTGRES_DB:-quantpilot}"
POSTGRES_USER="${POSTGRES_USER:-quantpilot}"
ok ".env.prod 就绪（DB=$POSTGRES_DB USER=$POSTGRES_USER）"

# ============== 3. arm64 构建 ==============
info "[3/7] 构建镜像（ARM：base 自动取 arm64，uv sync 取 aarch64 wheel；首次较慢）"
dc build --pull
ok "镜像构建完成"

# ============== 4. 起 db 等就绪 ==============
info "[4/7] 启动 db 并等待就绪"
dc up -d db
until dc exec -T db pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; do sleep 2; done
ok "db 就绪"

# ============== 5. 灌 dump（必须空库）==============
info "[5/7] 灌入整库 dump：$DUMP"
EXISTING=$(dc exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d '[:space:]' || echo 0)
if [ "${EXISTING:-0}" -gt 0 ]; then
    warn "目标库 public schema 已有 $EXISTING 张表——不是全新空库。继续灌库可能冲突/叠加。"
    read -rp "确认继续？(yes/no) " R
    [ "$R" = "yes" ] || fatal "已中止（建议在全新实例上跑，或先 DROP 重建库）"
fi
gunzip -c "$DUMP" | dc exec -T db psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$POSTGRES_DB"
ok "灌库完成"

# 快速核验关键表行数（迁移完整性初筛）
info "灌库后核验：关键表行数"
for tbl in daily_quote candidate_pool factor_ic_window_state strategy_weights_history; do
    cnt=$(dc exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
        "SELECT count(*) FROM $tbl;" 2>/dev/null | tr -d '[:space:]' || echo "ERR")
    echo "      $tbl: $cnt"
done

# ============== 6. 起 backend / 前端 / nginx ==============
info "[6/7] 启动 backend + 前端 + nginx（schema 已在 head，alembic 应 no-op）"
dc up -d frontend-builder backend nginx
ok "全栈启动"

# ============== 7. 冒烟 ==============
info "[7/7] 冒烟自测（等服务起来 ~10s）"
sleep 10
if [ -x scripts/prod_smoke.sh ]; then
    scripts/prod_smoke.sh || warn "冒烟有失败项 → docker logs quantpilot-backend-1 排查"
else
    code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost/health || echo 000)
    [ "$code" = "200" ] && ok "/health 200" || warn "/health 返回 $code，检查 docker logs quantpilot-backend-1"
fi

echo
ok "迁移引导完成。"
echo "   后续："
echo "   1) 数据完整性复核：四表行数对照本地 + 最新 trade_date 抽查"
echo "   2) 防回收：账号升级 Pay-As-You-Go（见 deployment.md §14.5）"
echo "   3) HTTPS：deployment.md §6（域名 A 记录 + certbot）"
echo "   4) 每日备份 cron：scripts/backup_db.sh（deployment.md §7）"
