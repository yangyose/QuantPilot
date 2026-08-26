#!/usr/bin/env bash
# V1.5-C C1 面板对比驱动（2026-08-26 从 scratchpad 移入仓库）。
#
# 按月分块跑 backfill_daily_ic，中断最多丢一个月（每交易日独立 commit，可续跑）。
# 跑完自动 dump 一份原始 IC 行——两组配置写的是同一批行（--force 原地覆盖），
# 且 IC 行本身不带配置标记，不 dump 就永久无法分辨/复查哪行出自哪个配置。
#
# 用法（仓库根目录）：
#   bash scripts/run_ic_panel.sh off  var/panel/off.log
#   bash scripts/run_ic_panel.sh on   var/panel/on.log
#
# 可选环境变量：
#   PANEL_DB_URL    默认 postgresql+asyncpg://quantpilot:quantpilot@localhost:5434/quantpilot
#   PANEL_START     起始月（YYYY-MM），默认 2024-07
#   PANEL_END       结束月（YYYY-MM），默认 2026-07
#   PANEL_LAST_DAY  结束月截止日，默认 2026-07-17（前向窗口上限：daily_quote
#                   最新 2026-08-14 → 因子日最多到 2026-07-17；换数据后须同步调整）
#
# ⚠️ 只对本地算力库 5434 跑。生产 2GB 机禁止任何全 universe 评分作业（CLAUDE.md §6）。
# ⚠️ 长任务务必 detached 起，否则随终端/会话退出而死：
#       nohup bash scripts/run_ic_panel.sh off var/panel/off.log >/dev/null 2>&1 &
#   判断是否还活着看落盘 log 的 chunk 行，不认通知、不认退出码。
set -u

usage() { echo "用法: bash scripts/run_ic_panel.sh <off|on> <logfile>" >&2; exit 2; }
[ $# -ge 2 ] || usage
CFG="$1"
LOG="$2"
case "$CFG" in off|on) ;; *) echo "第一个参数必须是 off 或 on（收到：$CFG）" >&2; usage ;; esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend" || { echo "找不到 backend/：$ROOT" >&2; exit 2; }

mkdir -p "$(dirname "$LOG")"
export PYTHONIOENCODING=utf-8
export DATABASE_URL="${PANEL_DB_URL:-postgresql+asyncpg://quantpilot:quantpilot@localhost:5434/quantpilot}"

START="${PANEL_START:-2024-07}"
END="${PANEL_END:-2026-07}"
LAST_DAY="${PANEL_LAST_DAY:-2026-07-17}"

# 生成 START..END 的月份序列（date -d 在 Git Bash / GNU date 下可用）
MONTHS=""
m="$START"
while :; do
    MONTHS="$MONTHS $m"
    [ "$m" = "$END" ] && break
    m=$(date -d "${m}-01 +1 month" +%Y-%m) || exit 2
done

{
    echo "=== PANEL RUN cfg=$CFG started $(date -Iseconds) ==="
    echo "    DATABASE_URL=${DATABASE_URL%%://*}://…@${DATABASE_URL##*@}"
    echo "    window=$START..$END (末月截至 $LAST_DAY)"
} >> "$LOG"

RC=0
for m in $MONTHS; do
    s="${m}-01"
    if [ "$m" = "$END" ]; then
        e="$LAST_DAY"
    else
        e=$(date -d "${s} +1 month -1 day" +%Y-%m-%d)
    fi
    t0=$(date +%s)
    echo "--- chunk $m ($s → $e) start $(date -Iseconds)" >> "$LOG"
    uv run python scripts/backfill_daily_ic.py \
        --start "$s" --end "$e" --force --skip-confirm \
        --momentum-risk-adjusted "$CFG" >> "$LOG" 2>&1
    rc=$?
    echo "--- chunk $m done rc=$rc elapsed=$(( $(date +%s) - t0 ))s" >> "$LOG"
    [ $rc -ne 0 ] && RC=1
done

# ---- 收尾：dump 原始 IC 行，防被下一组覆盖后无从复查 ----
DUMP_DIR="$ROOT/backups/local-5434"
DUMP="$DUMP_DIR/panel_ic_${CFG}_$(date +%Y%m%d_%H%M%S).sql"
mkdir -p "$DUMP_DIR"
if docker exec qp-backtest-db-5434 pg_dump -U quantpilot -d quantpilot \
        --data-only -t factor_ic_window_state > "$DUMP" 2>>"$LOG"; then
    echo "=== dump OK: $DUMP ($(wc -c < "$DUMP") bytes) ===" >> "$LOG"
else
    echo "!!! dump FAILED —— 原始 IC 行未备份，跑下一组前务必手工 dump，否则本组结果会被覆盖且不可复查" >> "$LOG"
    RC=1
fi

echo "=== PANEL RUN cfg=$CFG finished $(date -Iseconds) panel_exit=$RC ===" >> "$LOG"
exit $RC
