/**
 * Phase 12 §3.3.1：trigger_reason / market_state 中文翻译。
 *
 * trigger_reason 枚举值来源：Phase 11 §5 SignalGenerator + Phase 12 §4 设计。
 * 未登记的 reason 直接返回原值，避免遮蔽未知触发原因。
 */

/**
 * trigger_reason 翻译表。
 * 源：Phase 11 §5 SignalGenerator 枚举（pct_below_buy / pct_above_sell /
 * hard_stop_loss / short_term_z_drop / mid_term_icir_flip），以代码为准。
 * 后端未在此列表中的 reason 直接显示原值（translateTriggerReason fallback）。
 */
export const TRIGGER_REASON_MAP: Record<string, string> = {
  pct_below_buy: '分位顶部强烈买入',
  pct_above_sell: '分位底部减仓',
  hard_stop_loss: '硬止损触发',
  short_term_z_drop: '短期 z 降幅 ≥ 1.5σ',
  mid_term_icir_flip: '中期 ICIR 转负',
}

export const MARKET_STATE_MAP: Record<string, string> = {
  UPTREND: '上升趋势',
  OSCILLATION: '震荡',
  DOWNTREND: '下跌趋势',
}

export const WEIGHTS_SOURCE_MAP: Record<string, string> = {
  icir: 'ICIR 滚动加权',
  default_matrix: '默认矩阵',
  user_override: '人工覆盖',
}

export const HYSTERESIS_STATUS_MAP: Record<string, string> = {
  active: '已生效',
  stable: '稳定',
  pending_switch: '待切换',
  cooled_down: '冷却中',
}

export const STRATEGY_LABELS: Record<string, string> = {
  trend: '趋势跟踪',
  momentum: '动量',
  mean_reversion: '均值回归',
  value: '价值',
}

export function translateTriggerReason(reason: string | null | undefined): string {
  if (!reason) return '—'
  return TRIGGER_REASON_MAP[reason] ?? reason
}

export function translateMarketState(state: string | null | undefined): string {
  if (!state) return '—'
  return MARKET_STATE_MAP[state] ?? state
}

export function translateWeightsSource(source: string | null | undefined): string {
  if (!source) return '—'
  return WEIGHTS_SOURCE_MAP[source] ?? source
}

export function translateHysteresisStatus(status: string | null | undefined): string {
  if (!status) return '—'
  return HYSTERESIS_STATUS_MAP[status] ?? status
}
