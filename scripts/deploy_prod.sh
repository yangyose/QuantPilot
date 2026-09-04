#!/usr/bin/env bash
# deploy_prod.sh —— QuantPilot 生产部署（腾讯 43.134.63.13）
#
# 为什么不是 scripts/deploy.sh：那个是 Phase 10 的一键脚本，对**当前**生产是错的——
# 它带 `--pull`（会引入无关基础镜像变化）、用 compose 起 nginx（服务器上 compose 被
# 就地改过）、不做 `nginx -s reload`（backend 重建后容器 IP 变，不 reload 必 502）、
# 且完全不同步代码。它从未被用于这套生产，属 CLAUDE.md §4.11「接了但没生效」一族。
#
# 本脚本固化 2026-08-31 C1+P0 部署实测走通的序列，并补上当时缺的三件事：
#   ① 版本戳——服务器不是 git 仓库，此前无法回答「现在跑的是哪个版本」
#   ② 部署记录进仓库——此前只存在于个人 memory，换机器/换人即丢失
#   ③ 前置闸门——工作区脏 / 未推送就部署，会产出一个 GitHub 上找不到的 sha
#
# 用法：
#   scripts/deploy_prod.sh --dry-run           # 只做预检，不改动生产
#   scripts/deploy_prod.sh                     # 完整部署
#   scripts/deploy_prod.sh --baseline <sha>    # 服务器无版本戳时（首次）显式给基线
#
# ⚠️ 这是生产写操作。CLAUDE.md C-1 要求**每次**取得用户单独确认，"上次批准过"不算。
#    本脚本不代替那个确认——它只保证确认之后的步骤不出错、不遗漏。

set -euo pipefail

SSH_HOST="${QP_SSH_HOST:-qp-tencent}"
REMOTE_ROOT="/home/ubuntu/QuantPilot"
REMOTE_BACKUPS="/home/ubuntu/backups"
HEALTH_URL="${QP_HEALTH_URL:-https://quant.portableagi.com/health}"
DEPLOY_LOG="docs/ops/deploy_log.md"

# 基线核验采样的文件（覆盖 engine / service / pipeline / data 四层）
BASELINE_FILES=(
    "src/quantpilot/engine/signal.py"
    "src/quantpilot/services/strategy_service.py"
    "src/quantpilot/pipeline/daily_pipeline.py"
    "src/quantpilot/data/repository.py"
)

DRY_RUN=0
BASELINE_OVERRIDE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --baseline) BASELINE_OVERRIDE="$2"; shift 2 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

cd "$(dirname "$0")/.."
say() { printf '\n==> %s\n' "$*"; }
die() { printf '\n❌ %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. 本地闸门

say "[1/8] 本地闸门"

[ -n "$(git status --porcelain)" ] && die "工作区不干净。部署出去的 sha 必须能在 git 里
   完整复现——先提交或 stash：
$(git status --short)"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
HEAD_SHA="$(git rev-parse HEAD)"
HEAD_SHORT="$(git rev-parse --short HEAD)"

git fetch --quiet origin "$BRANCH" 2>/dev/null || true
REMOTE_SHA="$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo "")"
[ "$REMOTE_SHA" != "$HEAD_SHA" ] && die "HEAD ($HEAD_SHORT) 未推送到 origin/$BRANCH。
   部署一个远端没有的 commit，等于生产上跑着一份只存在于本机的代码——
   本机一坏就无从复现。先 git push。"

echo "    分支 $BRANCH / HEAD $HEAD_SHORT / 已与 origin 一致"

# ---------------------------------------------------------------- 2. 服务器基线

say "[2/8] 服务器基线核验（不假设它在某个 commit 上）"

DEPLOYED_STAMP="$(ssh "$SSH_HOST" "cat $REMOTE_ROOT/backend/VERSION 2>/dev/null" || true)"
DEPLOYED_STAMP="$(printf '%s' "$DEPLOYED_STAMP" | tr -d '\r' | head -1)"

if [ -n "$BASELINE_OVERRIDE" ]; then
    BASELINE="$BASELINE_OVERRIDE"
    echo "    基线由 --baseline 指定：$BASELINE"
elif [ -n "$DEPLOYED_STAMP" ] && [ "$DEPLOYED_STAMP" != "unknown" ]; then
    BASELINE="$(printf '%s' "$DEPLOYED_STAMP" | awk '{print $1}')"
    echo "    服务器版本戳：$DEPLOYED_STAMP"
else
    die "服务器上没有版本戳（首次使用本脚本时正常）。
   请用 --baseline <sha> 显式给出当前生产对应的 commit，
   并先按 $DEPLOY_LOG 的方法核验它确实成立。"
fi

# ⚠️ 必须 --strip-trailing-cr：服务器上的 .py 是 CRLF（git archive 在 Windows 端按
#    core.autocrlf 转换过），而 `git show <sha>:path` 出来的 blob 是 LF
#    → 裸 md5/diff 必然全部对不上，看起来像「服务器不在任何已知 commit 上」。
#    2026-08-31 首次部署时踩过，排查了十几分钟。CRLF 对 .py 无害，不要去"统一"它。
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
MISMATCH=0
for f in "${BASELINE_FILES[@]}"; do
    ssh "$SSH_HOST" "cat $REMOTE_ROOT/backend/$f" > "$TMPD/remote" 2>/dev/null || { MISMATCH=1; echo "    缺失 $f"; continue; }
    git show "$BASELINE:backend/$f" > "$TMPD/local" 2>/dev/null || { MISMATCH=1; echo "    本地无 $BASELINE:$f"; continue; }
    if diff -q --strip-trailing-cr "$TMPD/remote" "$TMPD/local" >/dev/null 2>&1; then
        echo "    MATCH  $f"
    else
        echo "    DIFFER $f"
        MISMATCH=1
    fi
done
[ "$MISMATCH" -eq 1 ] && die "服务器内容与基线 $BASELINE 不符。
   可能是有人在服务器上就地改过代码，或基线记错了。**先查清楚再部署**——
   直接覆盖会静默丢掉服务器上那份改动（BACKTEST_ENABLED 就是这么分叉的）。"

# ---------------------------------------------------------------- 3. 变更范围

say "[3/8] 变更范围（整树同步，不是 cherry-pick）"

DELTA="$(git log --oneline "$BASELINE..HEAD" -- backend/)"
[ -z "$DELTA" ] && die "$BASELINE..HEAD 在 backend/ 下无改动，无需部署。"
echo "$DELTA" | sed 's/^/    /'

if git diff --name-status "$BASELINE..HEAD" -- backend/ | grep -q '^D'; then
    die "delta 中存在**被删除**的文件，但同步用的是 tar -x（只覆盖、不删除）。
   需手工在服务器上删除它们，否则会留下应当消失的旧文件。"
fi

if ! git diff --quiet "$BASELINE..HEAD" -- backend/alembic/; then
    echo "    ⚠️  本批含 alembic 迁移——backend 启动时会自动 upgrade head。"
    echo "        务必确认迁移是**前向且非破坏**的（CLAUDE.md C-1）。"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    say "--dry-run：预检通过，未改动生产。"
    exit 0
fi

# ---------------------------------------------------------------- 4. 回滚点

say "[4/8] 回滚点"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="$REMOTE_BACKUPS/backend_pre_${HEAD_SHORT}_${TS}.tar.gz"
ssh "$SSH_HOST" "mkdir -p $REMOTE_BACKUPS && cd /home/ubuntu && tar czf $BACKUP QuantPilot/backend && ls -lh $BACKUP"

# ---------------------------------------------------------------- 5. 同步 + 版本戳

say "[5/8] 同步 backend/（不碰 compose / nginx / .env.prod——它们在服务器上被就地改过）"
MSYS_NO_PATHCONV=1 git archive --format=tar HEAD backend \
    | MSYS_NO_PATHCONV=1 ssh "$SSH_HOST" "cd $REMOTE_ROOT && tar -x"

STAMP="$HEAD_SHORT $(date -u +%Y-%m-%dT%H:%M:%SZ) $BRANCH"
ssh "$SSH_HOST" "printf '%s\n' '$STAMP' > $REMOTE_ROOT/backend/VERSION && cat $REMOTE_ROOT/backend/VERSION"

# 同步后逐文件回验：tar 静默失败过的话，这里会当场发现
for f in "${BASELINE_FILES[@]}"; do
    ssh "$SSH_HOST" "cat $REMOTE_ROOT/backend/$f" > "$TMPD/remote" 2>/dev/null
    git show "HEAD:backend/$f" > "$TMPD/local"
    diff -q --strip-trailing-cr "$TMPD/remote" "$TMPD/local" >/dev/null 2>&1 \
        || die "同步后 $f 仍与 HEAD 不符——tar 未生效，停止部署。回滚：$BACKUP"
done
echo "    同步校验通过"

# ---------------------------------------------------------------- 6. 构建 + 重启

say "[6/8] 构建（不带 --pull）+ 重启 backend"
ssh "$SSH_HOST" "cd $REMOTE_ROOT && docker compose -f docker-compose.prod.yml --env-file .env.prod build backend"
ssh "$SSH_HOST" "cd $REMOTE_ROOT && docker compose -f docker-compose.prod.yml --env-file .env.prod up -d backend"

# ---------------------------------------------------------------- 7. nginx reload

say "[7/8] nginx reload（backend 重建后容器 IP 变，不 reload 必 502）"
ssh "$SSH_HOST" "docker exec quantpilot-nginx-1 nginx -s reload"

# ---------------------------------------------------------------- 8. 生效判据

say "[8/8] 生效判据：/health 自报的版本必须等于本次部署的 sha"
for i in $(seq 1 12); do
    sleep 5
    LIVE="$(curl -fsS "$HEALTH_URL" 2>/dev/null || true)"
    case "$LIVE" in
        *"$HEAD_SHORT"*)
            echo "    $LIVE"
            echo "    ✅ 版本戳一致"
            break
            ;;
    esac
    [ "$i" -eq 12 ] && die "60s 内 /health 未报出 $HEAD_SHORT（实得：${LIVE:-无响应}）。
   容器可能未起来或仍是旧镜像。查 docker logs quantpilot-backend-1；
   回滚：ssh $SSH_HOST 'cd /home/ubuntu && tar xzf $BACKUP' 后重建。"
done

# ---------------------------------------------------------------- 部署记录

cat >> "$DEPLOY_LOG" <<EOF

## $HEAD_SHORT — $(date -u +%Y-%m-%dT%H:%M:%SZ)

| 项 | 值 |
|---|---|
| 分支 | \`$BRANCH\` |
| 基线（部署前） | \`$BASELINE\` |
| 回滚点 | \`$BACKUP\` |
| delta | $(printf '%s' "$DELTA" | wc -l | tr -d ' ') 个 commit |

\`\`\`
$DELTA
\`\`\`
EOF

say "完成。生产现在 = $HEAD_SHORT"
cat <<EOF

    部署记录已追加到 $DEPLOY_LOG —— **请提交它**，否则下次算 delta 又要靠考古。
    回滚：ssh $SSH_HOST 'cd /home/ubuntu && tar xzf $BACKUP' 然后重跑 [6][7] 两步。

    观察期 ≥ 3 个交易日（设计文档 §8 DoD）：每日看 run SUCCESS / signal_count /
    candidate_pool 行数 / backend 内存峰值（生产 2026-09-03 起为 2C4G，红线①仍然有效）。
EOF
