#!/usr/bin/env bash
# deploy_frontend.sh —— QuantPilot 生产前端部署（腾讯 43.134.63.13）
#
# 为什么需要它：`deploy_prod.sh` **只同步 backend/**，从不碰 frontend/。
# 2026-09-14 实测发现生产 `frontend_dist` 卷里的产物停在 **2026-09-03**，
# 而此后有 5 个 commit 改过 frontend/（含 af94e57 的 low_volatility 溯源展示、
# liquidity_note 渲染、funding_note 横幅、信号详情「判断依据」）——
# **后端部署成功 ≠ 功能上线**，这套生产此前根本没有前端部署路径。
#
# 为什么不用 `scripts/deploy.sh`：那个脚本对当前生产是错的（带 --pull / 用 compose
# 起 nginx 会覆盖服务器上就地改过的配置 / 不做 nginx reload / 完全不同步代码），
# 详见 deploy_prod.sh 文件头。
#
# 为什么不在服务器上构建：`deployment.md` 记着小机 vite/npm 构建**有 OOM 风险**，
# 而运维红线明确「生产机 2026-09-03 升配至 2C4G **不解除本条**」。
# 故走「**本地构建 → 传产物 → 换卷 → nginx reload**」——不在生产机跑 node。
#
# 机制（2026-09-14 实地查明）：
#   - 卷 `quantpilot_frontend_dist`，nginx 以**只读**挂在 /usr/share/nginx/html
#   - `frontend-builder` 容器的全部作用就是 `rm -rf /output/* && cp -r /dist/. /output/`
#     ——它只是个搬运工，产物完全可以从别处来，故本脚本与既有机制不冲突
#   - nginx 只读挂载 ⇒ 不能从 nginx 侧写入，须用临时容器以读写方式挂同一个卷
#
# ⚠️⚠️ 一个必须知道的回退陷阱：`frontend-builder` 镜像里**烤着一份构建期的 dist**。
# 谁若在本脚本之后执行 `docker compose up -d frontend-builder`，它会
# `rm -rf /output/*` 再把**镜像里那份旧产物**拷回去，**静默回滚本次部署且不报错**。
# 判据同下面第 7 步：拿公网 index.html 引用的 asset hash 与本地产物比对。
#
# 用法：
#   scripts/deploy_frontend.sh --dry-run    # 只构建 + 预检，不动生产
#   scripts/deploy_frontend.sh              # 完整部署
#
# ⚠️ 这是生产写操作。CLAUDE.md C-1 要求**每次**取得用户单独确认。

set -euo pipefail

SSH_HOST="${QP_SSH_HOST:-qp-tencent}"
VOLUME="quantpilot_frontend_dist"
NGINX_CT="quantpilot-nginx-1"
REMOTE_BACKUPS="/home/ubuntu/backups"
REMOTE_TMP="/tmp/qp_frontend_dist"
SITE_URL="${QP_SITE_URL:-https://quant.portableagi.com}"
DEPLOY_LOG="docs/ops/deploy_log.md"
MIN_ASSET_FILES=20      # 低于此数几乎肯定是构建残缺，宁可不部署

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

cd "$(dirname "$0")/.."
say() { printf '\n==> %s\n' "$*"; }
die() { printf '\n❌ %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. 本地闸门

say "[1/7] 本地闸门"
[ -n "$(git status --porcelain -- frontend/)" ] && die "frontend/ 有未提交改动。
   部署出去的产物必须能从 git 复现：
$(git status --short -- frontend/)"

SHA="$(git rev-parse --short HEAD)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git fetch -q origin "$BRANCH" 2>/dev/null || true
if ! git merge-base --is-ancestor HEAD "origin/$BRANCH" 2>/dev/null; then
    die "HEAD ($SHA) 未推送到 origin/$BRANCH。先 push，否则线上产物对应的源码在远端找不到。"
fi
printf '    分支 %s / HEAD %s / 已与 origin 一致\n' "$BRANCH" "$SHA"

# ---------------------------------------------------------------- 2. 本地构建

say "[2/7] 本地构建（不在生产机跑 node——OOM 红线）"
( cd frontend && npm run build >/dev/null 2>&1 ) || die "npm run build 失败。先在本地修好再部署。
   单独跑 'cd frontend && npm run build' 看完整报错。"
DIST="frontend/dist"
[ -f "$DIST/index.html" ] || die "构建产物缺 index.html"

# ---------------------------------------------------------------- 3. 产物自检
# 「构建成功」不等于「产物可用」：断链的 index.html、被清空的 assets/ 都能 exit 0。

say "[3/7] 产物自检"
N_ASSETS="$(find "$DIST/assets" -type f | wc -l | tr -d ' ')"
[ "$N_ASSETS" -ge "$MIN_ASSET_FILES" ] \
    || die "assets/ 只有 $N_ASSETS 个文件（< $MIN_ASSET_FILES），疑似构建残缺，拒绝部署。"

# index.html 引用的每个 asset 都必须真的存在——断链会让站点白屏而 nginx 仍 200
MISSING=""
while read -r ref; do
    [ -z "$ref" ] && continue
    [ -f "$DIST/$ref" ] || MISSING="$MISSING $ref"
done <<EOF
$(grep -o 'assets/[^"]*' "$DIST/index.html" | sort -u)
EOF
[ -n "$MISSING" ] && die "index.html 引用了不存在的产物：$MISSING"

ENTRY_JS="$(grep -o 'assets/index-[^"]*\.js' "$DIST/index.html" | head -1)"
[ -n "$ENTRY_JS" ] || die "index.html 里找不到入口 JS 引用，无法建立生效判据"
printf '    assets 文件 %s 个 / 入口 %s\n' "$N_ASSETS" "$ENTRY_JS"

# 结构对账：与服务器现有产物的**去哈希文件名集合**比对。
# 差异不一定是错（真删/真加视图就会变），但必须被看见而不是悄悄发生。
if REMOTE_LS="$(ssh "$SSH_HOST" "docker exec $NGINX_CT ls /usr/share/nginx/html/assets" 2>/dev/null)"; then
    # ⚠️ 用 | 作分隔符：替换内容本身是 `.`，若沿用 / 作分隔很容易多写一个
    # 而被 sed 当成标志位报 "unknown option to `s'"（本脚本首跑就栽在这）。
    DEHASH='s|-[A-Za-z0-9_-]\{8,\}\.|.|'
    L="$(find "$DIST/assets" -type f -printf '%f\n' | sed "$DEHASH" | sort -u)"
    R="$(printf '%s\n' "$REMOTE_LS" | sed "$DEHASH" | sort -u)"
    DIFF="$(diff <(printf '%s\n' "$L") <(printf '%s\n' "$R") || true)"
    if [ -n "$DIFF" ]; then
        printf '    ⚠️ 与线上产物结构有差异（自行确认是否预期）：\n%s\n' "$DIFF"
    else
        printf '    结构与线上一致（仅内容/哈希不同）\n'
    fi
fi

if [ "$DRY_RUN" = 1 ]; then
    say "--dry-run：构建与自检通过，未改动生产。"
    exit 0
fi

# ---------------------------------------------------------------- 4. 回滚点

say "[4/7] 回滚点（打包线上现有产物）"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="$REMOTE_BACKUPS/frontend_dist_pre_${SHA}_${TS}.tar.gz"
ssh "$SSH_HOST" "mkdir -p $REMOTE_BACKUPS && docker run --rm \
    -v $VOLUME:/src:ro -v $REMOTE_BACKUPS:/bk alpine:3.20 \
    sh -c 'cd /src && tar czf /bk/$(basename "$BACKUP") .' && ls -la $BACKUP"

# ---------------------------------------------------------------- 5. 传产物

say "[5/7] 传产物"
TARBALL="$(mktemp -u)/dist.tar.gz"
mkdir -p "$(dirname "$TARBALL")"
( cd "$DIST" && tar czf "$TARBALL" . )
ssh "$SSH_HOST" "rm -rf $REMOTE_TMP && mkdir -p $REMOTE_TMP"
scp -q "$TARBALL" "$SSH_HOST:$REMOTE_TMP/dist.tar.gz"
ssh "$SSH_HOST" "cd $REMOTE_TMP && tar xzf dist.tar.gz && rm dist.tar.gz && ls | head -3"
rm -rf "$(dirname "$TARBALL")"

# ---------------------------------------------------------------- 6. 换卷
# nginx 以只读挂载该卷，故用一次性容器以读写方式挂同一个卷来替换内容。

say "[6/7] 换卷 + nginx reload"
ssh "$SSH_HOST" "docker run --rm -v $VOLUME:/output -v $REMOTE_TMP:/src:ro alpine:3.20 \
    sh -c 'rm -rf /output/* && cp -r /src/. /output/ && ls /output/index.html'"
ssh "$SSH_HOST" "docker exec $NGINX_CT nginx -s reload && rm -rf $REMOTE_TMP"

# ---------------------------------------------------------------- 7. 生效判据
# ⚠️ 判据不是「文件复制成功」——那在 nginx 缓存旧 fd / 换错卷 / CDN 缓存住旧页面时
# 都会假阳性。vite 的 asset 文件名带内容哈希，故判据是：
# **公网取回的 index.html 必须引用本次构建的那个入口 JS，且该文件可 200 取回。**

say "[7/7] 生效判据：公网 index.html 必须引用本次构建的入口"
SERVED="$(curl -fsS --max-time 20 "$SITE_URL/" | grep -o 'assets/index-[^"]*\.js' | head -1 || true)"
printf '    线上引用 %s\n    本次构建 %s\n' "${SERVED:-<空>}" "$ENTRY_JS"
[ "$SERVED" = "$ENTRY_JS" ] || die "公网 index.html 仍引用 ${SERVED:-<空>}，与本次产物不符。
   可能原因：换卷未生效 / nginx 未 reload / Cloudflare 缓存了旧页面（试 curl -H 'Cache-Control: no-cache'）。
   回滚：ssh $SSH_HOST \"docker run --rm -v $VOLUME:/output -v $REMOTE_BACKUPS:/bk:ro alpine:3.20 \\
        sh -c 'rm -rf /output/* && tar xzf /bk/$(basename "$BACKUP") -C /output'\" && \\
        ssh $SSH_HOST 'docker exec $NGINX_CT nginx -s reload'"

curl -fsS --max-time 20 -o /dev/null "$SITE_URL/$ENTRY_JS" \
    || die "入口 JS $ENTRY_JS 取不回来（index.html 引用对了但文件缺失 = 白屏）"
printf '    入口 JS 可 200 取回 ✅\n'

say "完成。生产前端现在 = $SHA"
cat <<EOF

    回滚：ssh $SSH_HOST "docker run --rm -v $VOLUME:/output -v $REMOTE_BACKUPS:/bk:ro alpine:3.20 \\
            sh -c 'rm -rf /output/* && tar xzf /bk/$(basename "$BACKUP") -C /output'" \\
          && ssh $SSH_HOST "docker exec $NGINX_CT nginx -s reload"

    ⚠️ 别在此之后跑 \`docker compose up -d frontend-builder\`：它会 rm -rf 卷内容
       并拷回**镜像里烤的那份旧 dist**，静默回滚本次部署。真要用它，先重建镜像。

    记得把本次部署追加到 $DEPLOY_LOG（含线上 asset 哈希，便于下次对账）。
EOF
