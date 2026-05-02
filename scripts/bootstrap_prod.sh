#!/usr/bin/env bash
# QuantPilot 生产环境一键引导（首次部署用）
# 用法：sudo scripts/bootstrap_prod.sh
#
# 流程：
#   1) 系统体检（Docker 版本、内存、磁盘、端口冲突）
#   2) 交互生成 .env.prod（自动 bcrypt 哈希 / JWT 密钥 / 强随机数据库密码）
#   3) 校验 .env.prod 不再含占位值
#   4) 调用 scripts/deploy.sh 起服务
#   5) 引导设置 cron 备份 + 冒烟测试
#
# 已存在 .env.prod 时，跳过 [2]，直接走部署流程。

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

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
ask()   { read -rp "$1" REPLY; echo "$REPLY"; }
ask_secret() { read -rsp "$1" REPLY; echo; echo "$REPLY"; }

ENV_FILE="$ROOT/.env.prod"
EXAMPLE_FILE="$ROOT/.env.prod.example"

# ============== [1/5] 系统体检 ==============
info "[1/5] 系统体检"

# Docker
command -v docker >/dev/null 2>&1 || fatal "docker 未安装。Ubuntu 安装：curl -fsSL https://get.docker.com | sh"
docker compose version >/dev/null 2>&1 || fatal "docker compose v2 缺失。请升级 Docker Engine 到 20.10+"
docker info >/dev/null 2>&1 || fatal "Docker daemon 未运行。systemctl start docker"
ok "Docker $(docker version --format '{{.Server.Version}}') / Compose v2"

# 内存（建议 ≥ 2 GB 可用）
if [ -f /proc/meminfo ]; then
    MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
    if [ "$MEM_MB" -lt 1800 ]; then
        warn "总内存 ${MEM_MB} MB，低于推荐 2 GB；后端构建可能 OOM"
    else
        ok "内存 ${MEM_MB} MB"
    fi
fi

# 磁盘（部署目录 ≥ 10 GB 可用）
DISK_AVAIL=$(df -BG --output=avail "$ROOT" 2>/dev/null | tail -1 | tr -d 'G ' || echo 0)
if [ "${DISK_AVAIL:-0}" -lt 10 ]; then
    warn "可用磁盘 ${DISK_AVAIL} GB，低于推荐 10 GB"
else
    ok "可用磁盘 ${DISK_AVAIL} GB"
fi

# 端口冲突（80 / 443）
HTTP_PORT_DEFAULT=80
PORT_IN_USE=""
if command -v ss >/dev/null 2>&1; then
    if ss -ltn | awk '{print $4}' | grep -qE "[:.]${HTTP_PORT_DEFAULT}\$"; then
        PORT_IN_USE="80"
    fi
elif command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${HTTP_PORT_DEFAULT}\$" && PORT_IN_USE="80"
fi
if [ -n "$PORT_IN_USE" ]; then
    warn "端口 ${PORT_IN_USE} 已被占用。可在 .env.prod 中改 HTTP_PORT=8080"
else
    ok "端口 80 可用"
fi

# 时间同步检查（Asia/Shanghai 偏移 ≥ 60s 时告警）
if command -v timedatectl >/dev/null 2>&1; then
    SYNC=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo "no")
    if [ "$SYNC" != "yes" ]; then
        warn "系统时间未与 NTP 同步，可能影响 APScheduler 触发时间"
    else
        ok "系统时间已与 NTP 同步"
    fi
fi

# ============== [2/5] 生成 .env.prod ==============
info "[2/5] 准备 .env.prod"

if [ -f "$ENV_FILE" ]; then
    ok ".env.prod 已存在，跳过生成（如需重建：先 mv 到备份位置）"
else
    [ -f "$EXAMPLE_FILE" ] || fatal "$EXAMPLE_FILE 不存在"
    cp "$EXAMPLE_FILE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    echo ""
    echo "${BLUE}请输入生产配置（回车使用括号中的默认值）：${NC}"
    echo ""

    DOMAIN=$(ask "对外域名或 IP（如 quant.example.com，仅 IP 部署直接回车跳过）: ")
    HTTP_PORT=$(ask "HTTP 端口 [80]: "); HTTP_PORT="${HTTP_PORT:-80}"

    while true; do
        ADMIN_USERNAME=$(ask "管理员用户名 [admin]: "); ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
        echo "请设置管理员密码（≥ 12 位，包含字母与数字；公网部署务必使用强密码）："
        ADMIN_PASSWORD=$(ask_secret "密码: ")
        CONFIRM=$(ask_secret "确认: ")
        [ "$ADMIN_PASSWORD" != "$CONFIRM" ] && { warn "两次输入不一致"; continue; }
        [ ${#ADMIN_PASSWORD} -lt 12 ] && { warn "至少 12 位"; continue; }
        break
    done

    TUSHARE_TOKEN=$(ask "TUSHARE_TOKEN（积分 ≥ 2000；留空则数据 API 返回 503）: ")
    WXPUSHER_APP_TOKEN=$(ask "WXPUSHER_APP_TOKEN（留空则推送降级为站内信）: ")
    WXPUSHER_UID=$(ask "WXPUSHER_UID（同上）: ")

    info "生成 bcrypt 哈希..."
    HASH=$(P="$ADMIN_PASSWORD" docker run --rm -e P python:3.12-slim sh -c \
        "pip install -q bcrypt >/dev/null && python -c \"import bcrypt,os; print(bcrypt.hashpw(os.environ['P'].encode(), bcrypt.gensalt()).decode())\"")
    [ -n "$HASH" ] || fatal "bcrypt 哈希生成失败"

    info "生成 JWT 密钥与数据库密码..."
    if command -v openssl >/dev/null 2>&1; then
        JWT_KEY="$(openssl rand -hex 64)"
        DB_PASS="$(openssl rand -base64 24 | tr -d '/=+\n' | head -c 32)"
        REDIS_PASS="$(openssl rand -base64 24 | tr -d '/=+\n' | head -c 32)"
    else
        JWT_KEY="$(docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_hex(64))')"
        DB_PASS="$(docker run --rm python:3.12-slim python -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))')"
        REDIS_PASS="$(docker run --rm python:3.12-slim python -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))')"
    fi

    # CORS_ORIGINS：有域名时同时含域名 + http://localhost；纯 IP 时仅 http://localhost
    if [ -n "$DOMAIN" ]; then
        CORS_ORIGINS="[\"https://${DOMAIN}\",\"http://${DOMAIN}\",\"http://localhost\"]"
    else
        CORS_ORIGINS="[\"http://localhost\"]"
    fi

    # 写入 .env.prod
    docker run --rm -v "$ROOT:/work" -w /work python:3.12-slim python - "$ENV_FILE" "$HASH" "$JWT_KEY" "$DB_PASS" "$REDIS_PASS" "$ADMIN_USERNAME" "$TUSHARE_TOKEN" "$WXPUSHER_APP_TOKEN" "$WXPUSHER_UID" "$CORS_ORIGINS" "$HTTP_PORT" <<'PYEOF'
import re, sys
path, h, jwt, dbp, rp, uname, tu, wxa, wxu, cors, hp = sys.argv[1:12]
with open(path, encoding="utf-8") as f:
    text = f.read()

def upd(key, val, quote_single=False):
    global text
    v = f"'{val}'" if quote_single else val
    pat = rf"^{re.escape(key)}=.*$"
    if re.search(pat, text, flags=re.M):
        text = re.sub(pat, f"{key}={v}", text, flags=re.M)
    else:
        text += f"\n{key}={v}\n"

upd("POSTGRES_USER", "quantpilot")
upd("POSTGRES_DB", "quantpilot")
upd("POSTGRES_PASSWORD", dbp)
upd("DATABASE_URL", f"postgresql+asyncpg://quantpilot:{dbp}@db:5432/quantpilot")
upd("REDIS_PASSWORD", rp)
upd("REDIS_URL", f"redis://:{rp}@redis:6379/0")
upd("ADMIN_USERNAME", uname)
upd("ADMIN_PASSWORD_HASH", h, quote_single=True)
upd("JWT_SECRET_KEY", jwt)
upd("TUSHARE_TOKEN", tu)
upd("WXPUSHER_APP_TOKEN", wxa)
upd("WXPUSHER_UID", wxu)
upd("CORS_ORIGINS", cors)
upd("HTTP_PORT", hp)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)
print("OK")
PYEOF

    chmod 600 "$ENV_FILE"
    ok ".env.prod 已生成（权限 600）"
fi

# ============== [3/5] 占位值校验 ==============
info "[3/5] 校验 .env.prod 不再含占位值"
if grep -E '^(POSTGRES_PASSWORD|JWT_SECRET_KEY|ADMIN_PASSWORD_HASH)=.*(CHANGE_ME|REPLACE_WITH|changeme)' "$ENV_FILE" >/dev/null; then
    fatal ".env.prod 仍含占位值，请编辑后重试。所有 CHANGE_ME / REPLACE_WITH / changeme 都要替换"
fi
ok ".env.prod 已通过占位值检测"

# ============== [4/5] 调用 deploy.sh ==============
info "[4/5] 部署服务"
"$ROOT/scripts/deploy.sh"

# ============== [5/5] 后续提示 ==============
echo ""
ok "部署完成！"
echo ""
HTTP_PORT_VAL="$(grep -E '^HTTP_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d ' \r' || echo 80)"
HTTP_PORT_VAL="${HTTP_PORT_VAL:-80}"

echo "${GREEN}===================== 后续步骤 =====================${NC}"
echo ""
echo " 1) 浏览器访问 ${BLUE}http://<服务器IP>:${HTTP_PORT_VAL}${NC}（或绑定的域名）"
echo "    使用刚才设置的管理员账号登录，会自动跳转「首次启动向导」。"
echo ""
echo " 2) 公网部署务必启用 HTTPS（详见 docs/guides/deployment.md §2）"
echo "    未启用 HTTPS 时禁止开放公网访问，否则 JWT token 在传输中被窃取 = 账户被劫持。"
echo ""
echo " 3) 设置每日自动备份（强烈推荐）："
echo "    crontab -e"
echo "    0 2 * * * cd $ROOT && scripts/backup_db.sh >> logs/backup.log 2>&1"
echo ""
echo " 4) 冒烟自测（验证主链路）："
echo "    BASE_URL=http://localhost:${HTTP_PORT_VAL} API_PASSWORD=<刚设置的密码> scripts/prod_smoke.sh"
echo ""
echo " 5) 升级流程：git pull && scripts/deploy.sh"
echo " 6) 回滚流程：git checkout <prev-commit> && scripts/deploy.sh"
echo " 7) 数据恢复：scripts/restore_db.sh backups/qp_<timestamp>.sql.gz"
echo "${GREEN}====================================================${NC}"
