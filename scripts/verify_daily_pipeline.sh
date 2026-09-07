#!/usr/bin/env bash
# verify_daily_pipeline.sh —— 每日 17:30 管线跑完后的一键核查（只读）
#
# 为什么要有这个脚本：每次管线验证都在临场拼 SQL，容易漏项、也无法跨天对比。
# 尤其 2026-09-04 那次，我用**前一日**的持仓快照预判"今天不会有信号"，
# 而实际当日收盘已破线 —— 判断有没有破线必须用**当日收盘价**，不能凭记忆。
#
# 只读：全部是 SELECT，不写任何表。
#
# 用法：scripts/verify_daily_pipeline.sh [YYYY-MM-DD]   # 缺省=今天
set -euo pipefail

SSH_HOST="${QP_SSH_HOST:-qp-tencent}"
DAY="${1:-$(date +%F)}"
PSQL="docker exec quantpilot-db-1 psql -U quantpilot -d quantpilot -v ON_ERROR_STOP=1"

echo "==> 生产版本"
curl -s --max-time 15 "${QP_HEALTH_URL:-https://quant.portableagi.com/health}"; echo

echo
echo "==> [$DAY] 管线运行状态"
ssh "$SSH_HOST" "$PSQL -c \"
SELECT trade_date, status, signal_count,
       cp1_data_ready, cp2_scoring_done, cp3_signals_done,
       to_char(started_at,'HH24:MI:SS') AS started,
       to_char(finished_at,'HH24:MI:SS') AS finished,
       EXTRACT(EPOCH FROM (finished_at - started_at))::int AS secs,
       coalesce(left(error_msg, 120), '-') AS err
FROM pipeline_run WHERE trade_date = '$DAY';\""

echo "==> [$DAY] 信号明细（按类型）"
ssh "$SSH_HOST" "$PSQL -c \"
SELECT signal_type, coalesce(trigger_reason,'-') AS reason, count(*)
FROM "signal" WHERE trade_date = '$DAY'
GROUP BY 1,2 ORDER BY 1,2;\""

echo "==> [$DAY] 候选池与持仓标记（is_holding 必须等于实际持仓数）"
ssh "$SSH_HOST" "$PSQL -c \"
SELECT (SELECT count(*) FROM candidate_pool WHERE trade_date='$DAY') AS pool_rows,
       (SELECT count(*) FROM candidate_pool WHERE trade_date='$DAY' AND is_holding) AS marked_holding,
       (SELECT count(*) FROM position WHERE shares > 0) AS actual_positions;\""

echo "==> 当前持仓与盈亏（pnl_pct 已按当日收盘盯市；破线判定看这里，不要用记忆）"
ssh "$SSH_HOST" "$PSQL -c \"
SELECT ts_code, shares, cost_price, current_price,
       round(pnl_pct * 100, 2) AS pnl_pct, phase
FROM position WHERE shares > 0 ORDER BY pnl_pct;\""

echo "==> [$DAY] 通知落库（站内信 / 微信推送）"
ssh "$SSH_HOST" "$PSQL -c \"
SELECT notify_type, count(*) AS n, count(*) FILTER (WHERE wx_pushed) AS wx_ok,
       count(*) FILTER (WHERE wx_error IS NOT NULL) AS wx_err
FROM in_app_notification WHERE created_at::date = '$DAY'
GROUP BY 1 ORDER BY 1;\""

echo "==> [$DAY] universe 规模（日志，容器重启即丢——见 CLAUDE.md §6 可观测性缺口）"
ssh "$SSH_HOST" "docker logs quantpilot-backend-1 --since 24h 2>&1 \
  | grep -E 'scoring_universe_phase11|universe_filter_low_coverage' | tail -5" || echo "  （日志中未找到）"

echo
echo "⚠️ 判读提醒：'有没有破止损线' 必须用**当日收盘价**核对，不要用昨天的持仓快照推断。"
