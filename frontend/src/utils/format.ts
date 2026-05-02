/** 格式化百分比，如 0.1234 → "12.34%" */
export function fmtPct(value: number | null | undefined, decimals = 2): string {
  if (value == null) return 'N/A'
  return `${(value * 100).toFixed(decimals)}%`
}

/** 格式化金额，如 1234567.89 → "1,234,567.89" */
export function fmtAmount(value: number | null | undefined, decimals = 2): string {
  if (value == null) return 'N/A'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

/** 格式化净值，如 1.0234 → "1.0234" */
export function fmtNav(value: number | null | undefined): string {
  if (value == null) return 'N/A'
  return value.toFixed(4)
}

/** 格式化日期，如 "2026-04-15" → "2026-04-15"（直接返回，后端已标准格式） */
export function fmtDate(value: string | null | undefined): string {
  if (!value) return '—'
  return value.slice(0, 10)
}

/** 根据正负值返回颜色 class */
export function colorClass(value: number | null | undefined): string {
  if (value == null) return ''
  if (value > 0) return 'text-green-600'
  if (value < 0) return 'text-red-600'
  return ''
}

/** 市场状态中文标签 */
export const MARKET_STATE_LABELS: Record<string, string> = {
  UPTREND: '上涨趋势',
  OSCILLATION: '震荡市',
  DOWNTREND: '下跌趋势',
}

/** 信号类型中文标签 */
export const SIGNAL_TYPE_LABELS: Record<string, string> = {
  BUY: '买入',
  SELL: '卖出',
}

/** 信号状态中文标签 */
export const SIGNAL_STATUS_LABELS: Record<string, string> = {
  NEW: '新信号',
  VIEWED: '已查看',
  ACTED: '已操作',
  EXPIRED: '已过期',
  SUPERSEDED: '已替代',
}

/** 回测任务状态中文标签 */
export const BACKTEST_STATUS_LABELS: Record<string, string> = {
  PENDING: '等待中',
  RUNNING: '运行中',
  SUCCESS: '成功',
  FAILED: '失败',
}
