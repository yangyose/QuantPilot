// ── 统一响应包装 ────────────────────────────────────────────────────
export interface ApiResponse<T> {
  code: number
  data: T
  msg: string
}

// ── 认证 ────────────────────────────────────────────────────────────
export interface LoginResponse {
  access_token: string
  token_type: string
  refresh_token?: string
}

// ── 市场状态 ─────────────────────────────────────────────────────────
export type MarketStateEnum = 'UPTREND' | 'OSCILLATION' | 'DOWNTREND'

/** 后端 MarketStateItem 结构（字段与 market.py 一致） */
export interface MarketStateItem {
  trade_date: string
  market_state: MarketStateEnum
  trend_strength: number
  adx_value: number
  ma20: number
  ma60: number
  state_changed: boolean
  description: string
}

/** GET /market/state 响应 data 字段：{current: MarketStateItem | null} */
export interface MarketStateResponse {
  current: MarketStateItem | null
}

/** GET /market/state/history 响应 data.items 中每条记录 */
export type MarketStateHistory = MarketStateItem

// ── 信号 ─────────────────────────────────────────────────────────────
export type SignalType = 'BUY' | 'SELL'
export type SignalStatus = 'NEW' | 'VIEWED' | 'ACTED' | 'EXPIRED' | 'SUPERSEDED'
export type SignalStrength = 'STRONG' | 'MODERATE'

/** 与后端 SignalResponse schema 字段对齐（Phase 11 §9.1 新增三层输出 + trigger_reason） */
export interface Signal {
  id: number
  ts_code: string
  name: string | null
  signal_type: SignalType
  trade_date: string
  score: number | null
  suggested_pct: number | null
  suggested_price_low: number | null
  suggested_price_high: number | null
  stop_loss_price: number | null
  signal_strength: SignalStrength | null
  status: SignalStatus
  t1_warning: string | null
  liquidity_note: string | null
  reason: string | null
  created_at: string | null
  // Phase 11 §9.1：分位主路径三层输出 + trigger_reason 细分
  composite_z: number | null
  composite_pct_in_market: number | null
  trigger_reason: string | null
}

// ── Phase 12 §3.1.3：信号血缘三层 schema（19 字段 score_snapshot + 5 字段 pipeline_run）

/** ScoreSnapshotLineage：score_snapshot 19 字段（标识 1 + L1 5 + L2 9 + L3 4） */
export interface ScoreSnapshotLineage {
  // 标识
  ts_code: string
  // L1 业务可解释（5）
  composite_score: number | null
  composite_z: number | null
  composite_pct_in_market: number | null
  market_state: string | null
  trigger_reason: string | null
  // L2 ICIR + 中性化（9）
  trend_score: number | null
  momentum_score: number | null
  reversion_score: number | null
  value_score: number | null
  weights_source: string | null
  hysteresis_status: string | null
  score_breakdown: Record<string, unknown> | null
  factor_winsorized: Record<string, unknown> | null
  factor_neutralized: Record<string, unknown> | null
  // L3 正交化 + 审计（4）
  raw_factors: Record<string, unknown> | null
  factor_orthogonal: Record<string, unknown> | null
  score_breakdown_raw: Record<string, unknown> | null
  score_breakdown_residual: Record<string, unknown> | null
}

export interface PipelineRunLineage {
  trade_date: string
  cp1_at: string | null
  cp2_at: string | null
  cp3_at: string | null
  data_snapshot_version: string | null
}

/** GET /signals/{id}/lineage 响应 data 字段 */
export interface SignalLineage {
  signal_id: number
  trade_date: string
  score_snapshot: ScoreSnapshotLineage | null
  pipeline_run: PipelineRunLineage | null
}

// ── Phase 12 §4.2：多因子归因 API ─────────────────────────────────────
export interface AttributionHistoryItem {
  calc_date: string
  factor: string
  beta: number
  t_stat: number | null
  residual_std: number | null
  r_squared: number | null
  sample_size: number
  window_days: number
  created_at: string | null
}

export interface AttributionHistoryData {
  items: AttributionHistoryItem[]
  total: number
  start_date: string
  end_date: string
  factor: string | null
}

export interface AttributionSummaryData {
  start_date: string
  end_date: string
  cum_beta: Record<string, number>
  avg_r_squared: number | null
  total_sample: number
  months: number
}

// ── 账户与持仓 ───────────────────────────────────────────────────────
export interface AccountSummary {
  id: number
  name: string
  account_type: string
  broker: string | null
  total_assets: number
  cash: number
  synced_at: string | null
}

export interface PositionItem {
  id: number
  account_id: number
  ts_code: string
  shares: number
  cost_price: number | null
  current_price: number | null
  market_value: number | null
  pnl_pct: number | null
  open_date: string | null
  phase: string | null
}

export interface TradeRecord {
  id: number
  account_id: number
  ts_code: string
  trade_type: string
  trade_date: string
  price: number | null
  shares: number | null
  amount: number | null
  commission: number | null
  stamp_tax: number | null
  signal_id: number | null
  note: string | null
  is_voided?: boolean
  voided_at?: string | null
  void_note?: string | null
  created_at: string | null
}

export interface FundFlow {
  id: number
  flow_type: string
  amount: number
  trade_date: string
  ts_code?: string | null
  related_trade_id?: number | null
  note: string | null
  is_voided?: boolean
  voided_at?: string | null
  void_note?: string | null
  created_at: string
}

// ── 绩效 ─────────────────────────────────────────────────────────────
export interface PerformanceSummary {
  cumulative_return: number
  annualized_return: number
  max_drawdown: number
  sharpe_ratio: number | null
  win_rate: number
  profit_loss_ratio: number | null
  benchmark_return: number | null
}

export interface NavPoint {
  date: string
  nav: number
}

/** 后端 benchmark_series 每条：{date, value}（已归一化为相对首日倍数） */
export interface BenchmarkPoint {
  date: string
  value: number
}

export interface PerformanceHistory {
  nav_series: NavPoint[]
  benchmark_series: BenchmarkPoint[]
}

export interface AttributionByStock {
  ts_code: string
  total_pnl: number
  trade_count: number
  win_count: number
}

export interface Attribution {
  by_stock: AttributionByStock[]
  by_strategy: Record<string, number>
}

export interface BehavioralAnalysis {
  signal_compliance_rate: number | null
  stop_loss_execution_rate: number | null
  avg_holding_days: number | null
  trade_frequency: number | null
  chase_up_rate: number | null
  pnl_distribution: number[]
}

// ── 回测 ─────────────────────────────────────────────────────────────
export type BacktestStatus = 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILED'

export interface BacktestRunRequest {
  start_date: string
  end_date: string
  initial_capital: number
  commission_rate?: number
  stamp_tax_rate?: number
  slippage_rate?: number
}

export interface BacktestRunResponse {
  task_id: string
  status: BacktestStatus
}

export interface BacktestStatusResponse {
  task_id: string
  status: BacktestStatus
  progress_pct: number | null
  current_nav: number | null
  trade_date: string | null
  started_at: string | null
  finished_at: string | null
  error_msg: string | null
}

/** 后端返回格式：daily_nav 为 dict */
export interface BacktestResultRaw {
  task_id: string
  performance: Record<string, number | null>
  daily_nav: Record<string, number>
  disclaimer: string
  /** 本地算力中心回流回测的数据基线日（null=生产机直接跑、无戳） */
  data_baseline: string | null
}

/** store 内转换后格式：navSeries 为 array，供 NavChart 使用 */
export interface BacktestResult {
  performance: Record<string, number | null>
  disclaimer: string
  dataBaseline: string | null
  navSeries: NavPoint[]
}

// ── 因子质量 ─────────────────────────────────────────────────────────
/** 与后端 FactorIcHistoryItem schema 字段对齐 */
export interface FactorQualityItem {
  id: number
  calc_month: string
  strategy_name: string
  factor_name: string
  ic_value: number | null
  ic_mean_3m: number | null
  ic_std_3m: number | null
  ir_3m: number | null
  half_life_days: number | null
  return_window: number
  alert_status: string | null
}

// ── 报告 ─────────────────────────────────────────────────────────────
export interface ReportTradeRecord {
  ts_code: string
  trade_type: string
  trade_date: string
  price: number | null
  shares: number | null
  amount: number | null
}

export interface ReportSignalRecord {
  ts_code: string
  signal_type: string
  trade_date: string
  score: number | null
  status: string | null
}

export interface ReportHolding {
  ts_code: string
  shares: number
  cost_price: number | null
  current_price: number | null
  market_value: number | null
  pnl_pct: number | null
}

export interface ReportFactorAlert {
  strategy: string
  factor: string
  ic_mean_3m: number | null
  alert: string
}

export interface ReportContent {
  period?: { start: string; end: string }
  trade_summary?: {
    count?: number
    total?: number
    buy?: number
    sell?: number
    buy_count?: number
    sell_count?: number
    buy_amount?: number
    sell_amount?: number
    records?: ReportTradeRecord[]
  }
  trade_count?: number
  new_signals?: number
  signal_summary?: {
    count: number
    acted_count: number
    compliance_rate: number | null
    records?: ReportSignalRecord[]
  }
  holdings_snapshot?: ReportHolding[]
  top_holdings?: string[]
  factor_alerts?: ReportFactorAlert[]
}

export interface Report {
  id: number
  report_type: string
  period_start: string
  period_end: string
  content: ReportContent | null
  summary: string | null
  generated_at: string | null
}

// ── 设置 ─────────────────────────────────────────────────────────────
export interface UserConfigItem {
  config_key: string
  config_value: Record<string, unknown>
  user_level: string
  description: string | null
  updated_at: string | null
}

export interface UserConfigHistory {
  id: number
  config_key: string
  old_value: Record<string, unknown> | null
  new_value: Record<string, unknown>
  changed_at: string
  change_note: string | null
}

// ── K 线 ─────────────────────────────────────────────────────────────
export interface KlineBar {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  vol: number | null
}

// ── 候选股池 ─────────────────────────────────────────────────────────
export interface PoolStock {
  ts_code: string
  name: string | null
  composite_score: number
  in_pool: boolean
  is_holding: boolean
  sw_industry_l1: string | null
}

// ── 通知（Phase 10 §5.4） ────────────────────────────────────────────
export type NotifyType =
  | 'SIGNAL_BUY'
  | 'SIGNAL_SELL'
  | 'MARKET_STATE'
  | 'STOP_LOSS_WARN'
  | 'RISK_WARN'
  | 'FACTOR_ALERT'
  | 'PIPELINE_FAILURE'

export interface NotificationItem {
  id: number
  notify_type: NotifyType | string
  title: string
  body: string
  payload: Record<string, unknown> | null
  wx_pushed: boolean
  wx_error: string | null
  read_at: string | null
  created_at: string
}

export interface NotificationListData {
  items: NotificationItem[]
  total: number
  unread: number
}

export interface UnreadCountData {
  unread: number
}

export interface WxStatusData {
  wx_configured: boolean
  uid_masked: string | null
}

// ── 首次向导（Phase 10 §6.6） ────────────────────────────────────────
export interface SetupStatusData {
  completed: boolean
  completed_at: string | null
}

// ── 数据采集（Phase 10 §6.6 OnboardingView 初始数据拉取步） ───────────
export interface DataStatus {
  latest_quote_date: string | null
  stock_count: number
  index_codes: string[]
  is_up_to_date: boolean
  latest_financial_date: string | null
}

export interface IngestDailyResult {
  trade_date: string
  quote_count: number
  financial_count: number
  snapshot_version: string
  duration_seconds: number
  errors: string[]
}

export interface IngestHistoryResult {
  task_id: string
  status: string
  success_count: number
  fail_count: number
  failed_dates: string[]
}

export type RefreshStockListResult = Record<string, number | string | string[]>


// ── 黑白名单（Phase 4 API） ──────────────────────────────────────────
export type WatchlistType = 'WHITELIST' | 'BLACKLIST'

export interface WatchlistItem {
  id: number
  ts_code: string
  list_type: WatchlistType
  reason: string | null
  created_at: string | null
  updated_at: string | null
}

// ── YAML 配置导入返回（Phase 10 §6.9） ───────────────────────────────
export interface ImportChange {
  config_key: string
  action: 'create' | 'update' | 'noop'
  old_value: Record<string, unknown> | null
  new_value: Record<string, unknown>
}

export interface ImportResponse {
  applied: boolean
  total_in_yaml: number
  changes: ImportChange[]
  skipped_keys: string[]
}
