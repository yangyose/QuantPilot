<script setup lang="ts">
/**
 * Phase 10 §6 Settings 完整化：
 * - Tab 1 参数配置：12 个 config_key 按 basic/advanced/expert 三段折叠
 * - Tab 2 提醒设置：notification_prefs 单独 Tab（含事件开关 + 时段）
 * - Tab 3 黑白名单：/watchlist API 的 CRUD
 * - Tab 4 变更历史：已有
 * - Tab 5 导入/导出：YAML
 *
 * V1.5-G G-5：
 * - Tab 6 个人资料：账号信息 + L1/L2/L3 层级选择器（PATCH /auth/me）
 * - 参数面板按用户 level 显隐（requiredLevel 镜像 backend CONFIG_KEY_LEVEL；
 *   偏好非权限——用户随时可在个人资料切换层级解锁）
 */
import { message, Modal } from 'ant-design-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'

import {
  exportSettingsYaml,
  getConfigHistory,
  getSettings,
  importSettingsYaml,
  putSetting,
  revertConfig,
} from '@/api/settings'
import {
  addToWatchlist,
  listWatchlist,
  removeFromWatchlist,
} from '@/api/watchlist'
import { getWxStatus } from '@/api/notifications'
import TermLabel from '@/components/TermLabel.vue'
import { useAuthStore } from '@/stores/auth'
import type {
  UserLevel,
  ImportChange,
  ImportResponse,
  UserConfigHistory,
  UserConfigItem,
  WatchlistItem,
  WatchlistType,
  WxStatusData,
} from '@/types/api'

type Tier = 'basic' | 'advanced' | 'expert'
type FieldType = 'percent' | 'integer' | 'number' | 'boolean' | 'hour'

interface ConfigField {
  key: string
  label: string
  type: FieldType
  default: number | boolean
  min?: number
  max?: number
  step?: number
  suffix?: string
  help?: string
  // Phase 10 §6.1：字段级 tier 覆盖 config_key 默认 tier
  // 允许同一 config_key 内部分字段提升至更高分层（e.g. signal_params 大部分 basic，
  // price_low_mult/price_high_mult 极少调整 → expert）
  tier?: Tier
  // Phase 10 §6.8：字段级术语解释，命中 glossary 即在 label 旁挂 tooltip
  tooltipTerm?: string
}

interface ConfigDefinition {
  config_key: string
  title: string
  description: string
  consumer: string
  // 默认分层（字段未指定时回退此值）；同时决定该 def 在哪个 panel 显示其默认字段集
  tier: Tier
  // V1.5-G G-5：该 config_key 要求的最低用户层级（镜像 backend
  // core/config_defaults.py CONFIG_KEY_LEVEL；notification_prefs=L1 在独立 Tab）
  requiredLevel: 'L2' | 'L3'
  fields: ConfigField[]
}

// ─────────────── 12 config_key 全量目录（严格对齐 core/config_defaults.py） ───────────────

const CONFIG_CATALOG: ConfigDefinition[] = [
  // basic：日常使用参数
  {
    config_key: 'signal_params',
    requiredLevel: 'L2',
    title: '信号阈值',
    description: '综合评分驱动的买入/卖出/强信号阈值与建议价区间',
    consumer: 'SignalGenerator',
    tier: 'basic',
    fields: [
      { key: 'buy_threshold', label: '买入阈值', type: 'number', default: 80, min: 50, max: 100, step: 1, tier: 'basic' },
      { key: 'sell_threshold', label: '卖出阈值', type: 'number', default: 40, min: 0, max: 60, step: 1, tier: 'basic' },
      { key: 'strong_threshold', label: '强信号阈值', type: 'number', default: 90, min: 70, max: 100, step: 1, tier: 'basic' },
      { key: 'stop_loss_pct', label: '硬止损幅度', type: 'percent', default: 0.08, min: 0, max: 0.3, step: 0.005, tier: 'basic', tooltipTerm: 'stop_loss' },
      { key: 'add_cost_deviation_pct', label: '加仓价偏离阈值', type: 'percent', default: 0.1, min: 0, max: 0.3, step: 0.01, tier: 'advanced' },
      { key: 'price_low_mult', label: '建议买入价下限系数', type: 'number', default: 0.99, min: 0.9, max: 1.0, step: 0.001, tier: 'expert' },
      { key: 'price_high_mult', label: '建议买入价上限系数', type: 'number', default: 1.02, min: 1.0, max: 1.1, step: 0.001, tier: 'expert' },
    ],
  },
  {
    config_key: 'risk_limits',
    requiredLevel: 'L2',
    title: '风控上限',
    description: '单股/行业/账户持仓与单笔仓位上限',
    consumer: 'RiskChecker + PositionSizer',
    tier: 'basic',
    fields: [
      { key: 'max_single_stock_pct', label: '单股上限', type: 'percent', default: 0.2, min: 0.05, max: 1, step: 0.01, tier: 'basic' },
      { key: 'max_industry_pct', label: '单行业上限', type: 'percent', default: 0.3, min: 0.1, max: 1, step: 0.01, tier: 'basic' },
      { key: 'max_total_position_pct', label: '总仓位上限', type: 'percent', default: 0.8, min: 0.1, max: 1, step: 0.05, tier: 'basic' },
      { key: 'single_trade_pct', label: '单笔仓位比例', type: 'percent', default: 0.1, min: 0.01, max: 0.5, step: 0.01, tier: 'basic' },
    ],
  },
  {
    config_key: 'universe_params',
    requiredLevel: 'L2',
    title: '股票池',
    description: '候选股池容量 / 信号有效期 / 上市时长 / 流动性阈值',
    consumer: 'UniverseFilter + CandidatePoolManager',
    tier: 'basic',
    fields: [
      { key: 'pool_capacity', label: '候选池容量', type: 'integer', default: 20, min: 10, max: 100, step: 1, suffix: '只', tier: 'basic' },
      { key: 'signal_expiry_days', label: '信号有效期', type: 'integer', default: 3, min: 1, max: 10, step: 1, suffix: '日', tier: 'basic' },
      { key: 'new_stock_days', label: '次新股排除天数', type: 'integer', default: 60, min: 0, max: 365, step: 5, suffix: '日', tier: 'advanced' },
      { key: 'min_liquidity_amount', label: '流动性阈值（20日均）', type: 'number', default: 5_000_000, min: 0, step: 1_000_000, suffix: '元', tier: 'advanced' },
    ],
  },
  // advanced：策略/回测参数
  {
    config_key: 'market_state_params',
    requiredLevel: 'L3',
    title: '市场状态识别',
    description: 'ADX + 双 MA 均线三态识别参数',
    consumer: 'MarketStateEngine',
    tier: 'advanced',
    fields: [
      { key: 'ma_short', label: 'MA 短周期', type: 'integer', default: 20, min: 5, max: 60, step: 1, tier: 'advanced', tooltipTerm: 'ma_short' },
      { key: 'ma_long', label: 'MA 长周期', type: 'integer', default: 60, min: 20, max: 250, step: 1, tier: 'advanced', tooltipTerm: 'ma_long' },
      { key: 'adx_period', label: 'ADX 周期', type: 'integer', default: 14, min: 5, max: 30, step: 1, tier: 'advanced', tooltipTerm: 'adx' },
      { key: 'adx_threshold', label: 'ADX 阈值', type: 'number', default: 25, min: 15, max: 50, step: 0.5, tier: 'expert', tooltipTerm: 'adx' },
      { key: 'debounce_days', label: '状态防抖天数', type: 'integer', default: 3, min: 1, max: 10, step: 1, suffix: '日', tier: 'expert' },
    ],
  },
  {
    config_key: 'strategy_params_trend',
    requiredLevel: 'L2',
    title: '趋势策略参数',
    description: 'MA 周期 + MACD 参数（v1.1 ClosePrice 复权已修正）',
    consumer: 'TrendStrategy',
    tier: 'advanced',
    fields: [
      { key: 'ma_short', label: 'MA 短周期', type: 'integer', default: 20, min: 5, max: 60, step: 1, tier: 'advanced', tooltipTerm: 'ma_short' },
      { key: 'ma_long', label: 'MA 长周期', type: 'integer', default: 60, min: 20, max: 250, step: 1, tier: 'advanced', tooltipTerm: 'ma_long' },
      { key: 'macd_fast', label: 'MACD 快速 EMA', type: 'integer', default: 12, min: 3, max: 30, step: 1, tier: 'advanced', tooltipTerm: 'macd' },
      { key: 'macd_slow', label: 'MACD 慢速 EMA', type: 'integer', default: 26, min: 10, max: 60, step: 1, tier: 'advanced', tooltipTerm: 'macd' },
      { key: 'macd_signal', label: 'MACD 信号线', type: 'integer', default: 9, min: 3, max: 30, step: 1, tier: 'advanced', tooltipTerm: 'macd' },
    ],
  },
  {
    config_key: 'strategy_params_momentum',
    requiredLevel: 'L2',
    title: '动量策略参数',
    description: '3/6 月收益动量 + 反转剔除阈值',
    consumer: 'MomentumStrategy',
    tier: 'advanced',
    fields: [
      { key: 'lookback_short', label: '短周期回看', type: 'integer', default: 60, min: 20, max: 120, step: 5, suffix: '日', tier: 'advanced' },
      { key: 'lookback_long', label: '长周期回看', type: 'integer', default: 120, min: 60, max: 250, step: 5, suffix: '日', tier: 'advanced' },
      { key: 'reversal_exclude_pct', label: '反转剔除阈值（近月涨幅）', type: 'percent', default: 0.05, min: 0, max: 0.2, step: 0.005, tier: 'expert' },
    ],
  },
  {
    config_key: 'strategy_params_mean_reversion',
    requiredLevel: 'L2',
    title: '均值回归策略参数',
    description: 'RSI + 布林带',
    consumer: 'MeanReversionStrategy',
    tier: 'advanced',
    fields: [
      { key: 'rsi_period', label: 'RSI 周期', type: 'integer', default: 14, min: 5, max: 30, step: 1, tier: 'advanced', tooltipTerm: 'rsi' },
      { key: 'rsi_oversold', label: 'RSI 超卖阈值', type: 'number', default: 30, min: 10, max: 40, step: 1, tier: 'advanced', tooltipTerm: 'rsi' },
      { key: 'bbands_period', label: '布林带周期', type: 'integer', default: 20, min: 10, max: 60, step: 1, tier: 'advanced', tooltipTerm: 'bbands' },
      { key: 'bbands_std', label: '布林带标准差倍数', type: 'number', default: 2, min: 1, max: 3, step: 0.1, tier: 'expert', tooltipTerm: 'bbands' },
    ],
  },
  {
    config_key: 'strategy_params_value',
    requiredLevel: 'L2',
    title: '价值策略参数',
    description: 'PE-PB 历史分位窗口',
    consumer: 'ValueStrategy',
    tier: 'advanced',
    fields: [
      { key: 'pe_pb_history_years', label: 'PE/PB 历史窗口', type: 'integer', default: 5, min: 1, max: 10, step: 1, suffix: '年', tier: 'advanced', tooltipTerm: 'pe_pb_percentile' },
    ],
  },
  {
    config_key: 'backtest_defaults',
    requiredLevel: 'L2',
    title: '回测成本默认',
    description: '回测 POST 端点 partial-overlay 兜底值',
    consumer: 'BacktestEngine',
    tier: 'advanced',
    fields: [
      { key: 'commission_rate', label: '佣金率', type: 'percent', default: 0.00025, min: 0, max: 0.01, step: 0.00005, tier: 'advanced' },
      { key: 'stamp_tax_rate', label: '印花税率', type: 'percent', default: 0.0005, min: 0, max: 0.005, step: 0.00005, tier: 'advanced' },
      { key: 'slippage_rate', label: '滑点估算', type: 'percent', default: 0.001, min: 0, max: 0.01, step: 0.0005, tier: 'expert' },
    ],
  },
  // expert：因子监控
  {
    config_key: 'factor_monitor_params',
    requiredLevel: 'L3',
    title: '因子质量监控',
    description: 'IC 窗口 / 告警阈值 / 半衰期窗口（月末计算）',
    consumer: 'FactorMonitorService',
    tier: 'expert',
    fields: [
      { key: 'ic_window', label: 'IC 窗口', type: 'integer', default: 20, min: 5, max: 60, step: 1, suffix: '日', tier: 'expert', tooltipTerm: 'ic' },
      { key: 'ic_alert_threshold', label: 'IC 告警阈值（绝对值）', type: 'number', default: 0.02, min: 0, max: 0.1, step: 0.005, tier: 'expert', tooltipTerm: 'ic' },
      { key: 'half_life_window', label: '半衰期窗口', type: 'integer', default: 60, min: 30, max: 250, step: 5, suffix: '日', tier: 'expert', tooltipTerm: 'half_life' },
    ],
  },
]

// strategy_weights 单独处理（嵌套 dict，矩阵编辑）
const WEIGHTS_STATES = [
  { key: 'uptrend', label: '上涨趋势' },
  { key: 'oscillation', label: '震荡' },
  { key: 'downtrend', label: '下跌趋势' },
] as const
const WEIGHTS_STRATEGIES = [
  { key: 'trend', label: '趋势' },
  { key: 'momentum', label: '动量' },
  { key: 'mean_reversion', label: '均值回归' },
  { key: 'value', label: '价值' },
] as const

const WEIGHTS_DEFAULT: Record<string, Record<string, number>> = {
  uptrend: { trend: 0.4, momentum: 0.25, mean_reversion: 0.15, value: 0.2 },
  oscillation: { trend: 0.15, momentum: 0.15, mean_reversion: 0.4, value: 0.3 },
  downtrend: { trend: 0.1, momentum: 0.05, mean_reversion: 0.15, value: 0.7 },
}

// notification_prefs 单独 Tab
const NOTIFICATION_FIELDS: ConfigField[] = [
  { key: 'wx_enabled', label: '启用微信推送', type: 'boolean', default: true },
  { key: 'push_start_hour', label: '推送起始小时', type: 'hour', default: 15, min: 0, max: 23, step: 1 },
  { key: 'push_end_hour', label: '推送结束小时', type: 'hour', default: 22, min: 0, max: 23, step: 1 },
  { key: 'notify_signal_buy', label: '买入信号', type: 'boolean', default: true },
  { key: 'notify_signal_sell', label: '卖出信号', type: 'boolean', default: true },
  { key: 'notify_market_state', label: '市场状态变化', type: 'boolean', default: true },
  { key: 'notify_stop_loss_warn', label: '止损预警', type: 'boolean', default: true },
  { key: 'notify_risk_warn', label: '风险告警', type: 'boolean', default: true },
  { key: 'notify_factor_alert', label: '因子告警', type: 'boolean', default: true },
]

// ─────────────────── state ───────────────────

const auth = useAuthStore()
const activeTab = ref('config')
const loading = ref(false)
const saveLoading = ref(false)
const savedConfigs = ref<Record<string, Record<string, unknown>>>({})
const savedAt = ref<Record<string, string | null>>({})
const history = ref<UserConfigHistory[]>([])

const editMap = reactive<Record<string, Record<string, number | boolean>>>(
  Object.fromEntries(
    CONFIG_CATALOG.map((def) => [
      def.config_key,
      Object.fromEntries(def.fields.map((f) => [f.key, f.default])),
    ]),
  ),
)

// strategy_weights 矩阵编辑状态
const weightsEdit = reactive<Record<string, Record<string, number>>>(
  JSON.parse(JSON.stringify(WEIGHTS_DEFAULT)),
)

// notification_prefs 编辑
const notifyEdit = reactive<Record<string, number | boolean>>(
  Object.fromEntries(NOTIFICATION_FIELDS.map((f) => [f.key, f.default])),
)

// watchlist
const watchlistWhite = ref<WatchlistItem[]>([])
const watchlistBlack = ref<WatchlistItem[]>([])
const watchAdd = reactive<{ ts_code: string; list_type: WatchlistType; reason: string }>({
  ts_code: '',
  list_type: 'WHITELIST',
  reason: '',
})

// YAML
const yamlDraft = ref('')
const yamlDryRunResult = ref<ImportResponse | null>(null)
const yamlUploadLoading = ref(false)

// wx-status
const wxStatus = ref<WxStatusData>({ wx_configured: false, uid_masked: null })

// V1.5-G G-5：个人资料 / 层级切换
const LEVEL_OPTIONS: { value: UserLevel; label: string; desc: string }[] = [
  { value: 'L1', label: 'L1 · 新手', desc: '只看核心信号与提醒设置，隐藏进阶参数' },
  { value: 'L2', label: 'L2 · 进阶', desc: '开放信号阈值、风控、股票池与策略参数配置' },
  { value: 'L3', label: 'L3 · 专业', desc: '开放策略权重矩阵、市场状态识别与因子监控参数' },
]
const levelDraft = ref<UserLevel>(auth.level)
const levelSaving = ref(false)
// auth.level 外部变化（如登录后 fetchMe）时同步草稿
watch(() => auth.level, (v) => { levelDraft.value = v })

async function saveLevel() {
  if (levelDraft.value === auth.level) return
  levelSaving.value = true
  try {
    await auth.updateLevel(levelDraft.value)
    message.success(`已切换到 ${levelDraft.value}`)
    // level 变化影响 GET /settings 可见集，重新拉取
    await loadConfigs()
  } catch {
    message.error('层级切换失败')
    levelDraft.value = auth.level
  } finally {
    levelSaving.value = false
  }
}

/** 字段有效 tier：未声明则回退到 def.tier。 */
function effectiveFieldTier(def: ConfigDefinition, field: ConfigField): Tier {
  return field.tier ?? def.tier
}

/** 给定 panel tier，返回该 def 在此 panel 下应展示的字段列表。 */
function fieldsForPanel(def: ConfigDefinition, panelTier: Tier): ConfigField[] {
  return def.fields.filter((f) => effectiveFieldTier(def, f) === panelTier)
}

/** 给定 panel tier，返回该 panel 下应渲染的 def 列表（仅当 def 至少有一个字段属于该 tier）。
 * V1.5-G G-5：同时按用户 level 过滤（requiredLevel > 用户 level 的 def 隐藏）。 */
const LEVEL_NUM: Record<string, number> = { L1: 1, L2: 2, L3: 3 }
function defsForPanel(panelTier: Tier): ConfigDefinition[] {
  return CONFIG_CATALOG.filter(
    (d) =>
      LEVEL_NUM[d.requiredLevel] <= auth.levelNum &&
      fieldsForPanel(d, panelTier).length > 0,
  )
}

const panelDefs = computed(() => ({
  basic: defsForPanel('basic'),
  advanced: defsForPanel('advanced'),
  expert: defsForPanel('expert'),
}))

const defByKey = computed(() => {
  const m: Record<string, ConfigDefinition> = {}
  for (const d of CONFIG_CATALOG) m[d.config_key] = d
  return m
})

// ─────────────────── 加载 ───────────────────

onMounted(async () => {
  await Promise.all([
    loadConfigs(),
    loadHistory(),
    loadWatchlist(),
    loadWxStatus(),
    // G-5：刷新用户资料（level 可能在其他会话被改过）；失败沿用本地缓存
    auth.fetchMe().catch(() => undefined),
  ])
})

async function loadConfigs() {
  loading.value = true
  try {
    const items = await getSettings()
    const byKey: Record<string, UserConfigItem> = Object.fromEntries(
      items.map((c) => [c.config_key, c]),
    )

    savedConfigs.value = {}
    savedAt.value = {}
    for (const def of CONFIG_CATALOG) {
      const saved = byKey[def.config_key]?.config_value
      savedConfigs.value[def.config_key] = saved ?? {}
      savedAt.value[def.config_key] = byKey[def.config_key]?.updated_at ?? null
      const current: Record<string, number | boolean> = {}
      for (const f of def.fields) {
        const raw = saved?.[f.key]
        if (f.type === 'boolean') {
          current[f.key] = typeof raw === 'boolean' ? raw : (f.default as boolean)
        } else {
          const num = typeof raw === 'number' ? raw : Number(raw)
          current[f.key] = Number.isFinite(num) ? num : (f.default as number)
        }
      }
      editMap[def.config_key] = current
    }

    // strategy_weights（嵌套 dict）
    const savedWeights = byKey.strategy_weights?.config_value as
      | Record<string, Record<string, number>>
      | undefined
    savedConfigs.value.strategy_weights = savedWeights ?? {}
    savedAt.value.strategy_weights = byKey.strategy_weights?.updated_at ?? null
    for (const s of WEIGHTS_STATES) {
      const row = savedWeights?.[s.key] ?? WEIGHTS_DEFAULT[s.key]
      weightsEdit[s.key] = {}
      for (const st of WEIGHTS_STRATEGIES) {
        const v = row?.[st.key]
        weightsEdit[s.key][st.key] =
          typeof v === 'number' && Number.isFinite(v) ? v : WEIGHTS_DEFAULT[s.key][st.key]
      }
    }

    // notification_prefs
    const savedNotif = byKey.notification_prefs?.config_value
    savedConfigs.value.notification_prefs = savedNotif ?? {}
    savedAt.value.notification_prefs = byKey.notification_prefs?.updated_at ?? null
    for (const f of NOTIFICATION_FIELDS) {
      const raw = savedNotif?.[f.key]
      if (f.type === 'boolean') {
        notifyEdit[f.key] = typeof raw === 'boolean' ? raw : (f.default as boolean)
      } else {
        const num = typeof raw === 'number' ? raw : Number(raw)
        notifyEdit[f.key] = Number.isFinite(num) ? num : (f.default as number)
      }
    }
  } catch {
    message.error('配置加载失败')
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  try {
    history.value = await getConfigHistory()
  } catch {
    history.value = []
  }
}

async function loadWatchlist() {
  try {
    const all = await listWatchlist()
    watchlistWhite.value = all.filter((w) => w.list_type === 'WHITELIST')
    watchlistBlack.value = all.filter((w) => w.list_type === 'BLACKLIST')
  } catch {
    // 静默：watchlist API 可能未初始化
  }
}

async function loadWxStatus() {
  try {
    wxStatus.value = await getWxStatus()
  } catch {
    // 静默
  }
}

// ─────────────────── 操作 ───────────────────

function isCustomized(def: ConfigDefinition): boolean {
  return Object.keys(savedConfigs.value[def.config_key] ?? {}).length > 0
}

function isDirty(def: ConfigDefinition): boolean {
  const saved = (savedConfigs.value[def.config_key] ?? {}) as Record<string, unknown>
  const current = editMap[def.config_key] ?? {}
  for (const f of def.fields) {
    const savedVal = saved[f.key]
    const currentVal = current[f.key]
    if (!isCustomized(def)) {
      if (currentVal !== f.default) return true
    } else if (currentVal !== savedVal) {
      return true
    }
  }
  return false
}

async function saveOne(def: ConfigDefinition) {
  saveLoading.value = true
  try {
    const value: Record<string, unknown> = { ...editMap[def.config_key] }
    await putSetting(def.config_key, value)
    message.success(`${def.title} 已保存`)
    await Promise.all([loadConfigs(), loadHistory()])
  } catch {
    message.error(`${def.title} 保存失败`)
  } finally {
    saveLoading.value = false
  }
}

function resetOne(def: ConfigDefinition) {
  for (const f of def.fields) {
    editMap[def.config_key][f.key] = f.default as number | boolean
  }
}

// strategy_weights
async function saveWeights() {
  const errs: string[] = []
  for (const s of WEIGHTS_STATES) {
    const sum = WEIGHTS_STRATEGIES.reduce((a, st) => a + (weightsEdit[s.key][st.key] ?? 0), 0)
    if (Math.abs(sum - 1) > 0.01) errs.push(`${s.label}(合计=${sum.toFixed(2)})`)
  }
  if (errs.length > 0) {
    message.warning(`权重行合计应为 1.0：${errs.join('，')}`)
    return
  }
  saveLoading.value = true
  try {
    await putSetting('strategy_weights', weightsEdit as unknown as Record<string, unknown>)
    message.success('策略权重矩阵已保存')
    await Promise.all([loadConfigs(), loadHistory()])
  } catch {
    message.error('保存失败')
  } finally {
    saveLoading.value = false
  }
}

function resetWeights() {
  for (const s of WEIGHTS_STATES) {
    weightsEdit[s.key] = { ...WEIGHTS_DEFAULT[s.key] }
  }
}

// notification_prefs
async function saveNotify() {
  if ((notifyEdit.push_end_hour as number) <= (notifyEdit.push_start_hour as number)) {
    message.warning('推送结束小时必须大于起始小时')
    return
  }
  saveLoading.value = true
  try {
    await putSetting('notification_prefs', { ...notifyEdit })
    message.success('提醒设置已保存')
    await Promise.all([loadConfigs(), loadHistory()])
  } catch {
    message.error('保存失败')
  } finally {
    saveLoading.value = false
  }
}

function resetNotify() {
  for (const f of NOTIFICATION_FIELDS) notifyEdit[f.key] = f.default as number | boolean
}

// watchlist
async function addWatch() {
  if (!watchAdd.ts_code) {
    message.warning('请输入股票代码')
    return
  }
  try {
    await addToWatchlist(watchAdd.ts_code, watchAdd.list_type, watchAdd.reason || undefined)
    watchAdd.ts_code = ''
    watchAdd.reason = ''
    message.success('已添加')
    await loadWatchlist()
  } catch {
    message.error('添加失败')
  }
}

async function removeWatch(item: WatchlistItem) {
  try {
    await removeFromWatchlist(item.ts_code, item.list_type)
    message.success('已删除')
    await loadWatchlist()
  } catch {
    message.error('删除失败')
  }
}

// YAML
async function onExportYaml() {
  try {
    const body = await exportSettingsYaml()
    const blob = new Blob([body], { type: 'text/yaml;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `quantpilot-settings-${new Date().toISOString().slice(0, 10)}.yaml`
    a.click()
    URL.revokeObjectURL(url)
    message.success('已下载 YAML')
  } catch {
    message.error('导出失败')
  }
}

async function onDryRunYaml() {
  if (!yamlDraft.value.trim()) {
    message.warning('请先粘贴或上传 YAML 内容')
    return
  }
  yamlUploadLoading.value = true
  try {
    yamlDryRunResult.value = await importSettingsYaml(yamlDraft.value, true)
    message.info(
      `预览：${yamlDryRunResult.value.changes.length} 项变更，` +
        `${yamlDryRunResult.value.skipped_keys.length} 项忽略`,
    )
  } catch {
    message.error('YAML 解析失败，请检查格式')
    yamlDryRunResult.value = null
  } finally {
    yamlUploadLoading.value = false
  }
}

async function onApplyYaml() {
  if (!yamlDraft.value.trim()) {
    message.warning('请先粘贴或上传 YAML 内容')
    return
  }
  Modal.confirm({
    title: '确认应用 YAML？',
    content: '将写入 user_config 并记录变更历史。',
    onOk: async () => {
      yamlUploadLoading.value = true
      try {
        const r = await importSettingsYaml(yamlDraft.value, false)
        yamlDryRunResult.value = r
        message.success(`已应用 ${r.changes.filter((c) => c.action !== 'noop').length} 项变更`)
        await Promise.all([loadConfigs(), loadHistory()])
      } catch {
        message.error('应用失败')
      } finally {
        yamlUploadLoading.value = false
      }
    },
  })
}

function onYamlFileChange(evt: Event) {
  const input = evt.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    yamlDraft.value = String(reader.result ?? '')
  }
  reader.readAsText(file)
}

// 变更历史
async function revert(id: number) {
  try {
    await revertConfig(id)
    message.success('已回退')
    await Promise.all([loadConfigs(), loadHistory()])
    activeTab.value = 'config'
  } catch {
    message.error('回退失败')
  }
}

function formatFieldValue(field: ConfigField, value: unknown): string {
  if (field.type === 'boolean') return value ? '启用' : '停用'
  const num = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(num)) return '—'
  if (field.type === 'percent') return `${(num * 100).toFixed(2)}%`
  if (field.type === 'integer') return `${Math.round(num)}${field.suffix ?? ''}`
  if (field.type === 'hour') return `${num}:00`
  return `${num}${field.suffix ?? ''}`
}

function formatHistoryValue(
  def: ConfigDefinition | undefined,
  val: Record<string, unknown> | null | undefined,
): string {
  if (!val) return '—'
  if (!def) return JSON.stringify(val)
  const parts: string[] = []
  for (const f of def.fields) {
    if (val[f.key] !== undefined) parts.push(`${f.label}=${formatFieldValue(f, val[f.key])}`)
  }
  return parts.length > 0 ? parts.join('，') : JSON.stringify(val)
}

const historyColumns = [
  { title: '配置项', key: 'title', width: 160 },
  { title: '变更前', key: 'old_value' },
  { title: '变更后', key: 'new_value' },
  {
    title: '变更时间',
    dataIndex: 'changed_at',
    key: 'changed_at',
    width: 160,
    customRender: ({ value }: { value: string }) => value?.slice(0, 19) ?? '—',
  },
  { title: '操作', key: 'action', width: 80 },
]

const watchlistColumns = [
  { title: '股票代码', dataIndex: 'ts_code', key: 'ts_code', width: 120 },
  { title: '原因', dataIndex: 'reason', key: 'reason' },
  {
    title: '添加时间',
    dataIndex: 'created_at',
    key: 'created_at',
    width: 160,
    customRender: ({ value }: { value: string }) => value?.slice(0, 19) ?? '—',
  },
  { title: '操作', key: 'action', width: 80 },
]
</script>

<template>
  <div>
    <a-tabs v-model:active-key="activeTab">
      <!-- Tab 1: 参数配置（三段折叠） -->
      <a-tab-pane key="config" tab="参数配置">
        <!-- V1.5-G G-5：L1 用户不展示参数面板（偏好非权限，可随时切换层级解锁） -->
        <template v-if="auth.levelNum < 2">
          <a-alert
            type="info"
            show-icon
            message="参数配置面向 L2 及以上层级"
            description="当前层级为 L1（新手），系统使用 SDD 推荐默认参数。如需调整信号阈值、风控与策略参数，请先在『个人资料』中切换到 L2/L3。"
          />
          <a-button type="primary" ghost style="margin-top: 12px" @click="activeTab = 'profile'">
            前往个人资料切换层级
          </a-button>
        </template>
        <a-spin v-else :spinning="loading">
          <a-alert
            type="info"
            show-icon
            message="未自定义的参数使用下方显示的 SDD 默认值。修改后点击『保存此项』。"
            style="margin-bottom: 16px"
          />

          <a-collapse
            :default-active-key="['basic']"
            expand-icon-position="end"
            style="margin-bottom: 16px"
          >
            <!-- basic -->
            <a-collapse-panel key="basic" header="【基础】日常使用参数">
              <div
                v-for="def in panelDefs.basic"
                :key="def.config_key"
                class="config-block"
              >
                <div class="config-header">
                  <div>
                    <span class="config-title">{{ def.title }}</span>
                    <a-tag v-if="isCustomized(def)" color="blue" style="margin-left: 8px">已自定义</a-tag>
                    <a-tag v-else color="default" style="margin-left: 8px">使用默认值</a-tag>
                    <a-tag v-if="isDirty(def)" color="orange" style="margin-left: 4px">未保存</a-tag>
                  </div>
                  <a-space>
                    <a-button size="small" type="link" @click="resetOne(def)">恢复默认</a-button>
                    <a-button
                      size="small"
                      type="primary"
                      ghost
                      :disabled="!isDirty(def)"
                      :loading="saveLoading"
                      @click="saveOne(def)"
                    >
                      保存此项
                    </a-button>
                  </a-space>
                </div>
                <div class="config-desc">
                  {{ def.description }}
                  <span class="config-consumer">（用于：{{ def.consumer }}）</span>
                </div>
                <a-form layout="horizontal" :label-col="{ style: 'width: 180px' }" :colon="false">
                  <a-form-item
                    v-for="field in fieldsForPanel(def, 'basic')"
                    :key="field.key"
                    :help="field.help"
                    style="margin-bottom: 8px"
                  >
                    <template #label>
                      <TermLabel
                        v-if="field.tooltipTerm"
                        :term="field.tooltipTerm"
                        :label="field.label"
                        :show-icon="false"
                      />
                      <span v-else>{{ field.label }}</span>
                    </template>
                    <a-input-number
                      v-if="field.type === 'percent'"
                      v-model:value="editMap[def.config_key][field.key] as number"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step"
                      :formatter="(v: number | string | undefined) => v === undefined || v === '' ? '' : `${(Number(v) * 100).toFixed(3)}%`"
                      :parser="(v: string | undefined) => v ? Number(v.replace('%', '')) / 100 : 0"
                      style="width: 200px"
                    />
                    <a-input-number
                      v-else
                      v-model:value="editMap[def.config_key][field.key] as number"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step"
                      :precision="field.type === 'integer' ? 0 : undefined"
                      :addon-after="field.suffix"
                      style="width: 200px"
                    />
                    <span class="config-default">
                      默认：{{ formatFieldValue(field, field.default) }}
                    </span>
                  </a-form-item>
                </a-form>
              </div>
            </a-collapse-panel>

            <!-- advanced -->
            <a-collapse-panel key="advanced" header="【高级】策略参数 / 权重矩阵 / 回测成本">
              <!-- 策略权重矩阵（唯一嵌套 dict；G-5：CONFIG_KEY_LEVEL 定为 L3） -->
              <div v-if="auth.levelNum >= 3" class="config-block">
                <div class="config-header">
                  <div>
                    <span class="config-title">策略权重矩阵</span>
                    <a-tag v-if="Object.keys(savedConfigs.strategy_weights ?? {}).length" color="blue" style="margin-left: 8px">已自定义</a-tag>
                    <a-tag v-else color="default" style="margin-left: 8px">使用默认值</a-tag>
                  </div>
                  <a-space>
                    <a-button size="small" type="link" @click="resetWeights">恢复默认</a-button>
                    <a-button size="small" type="primary" ghost :loading="saveLoading" @click="saveWeights">
                      保存
                    </a-button>
                  </a-space>
                </div>
                <div class="config-desc">
                  三态 × 4 策略权重；每行合计必须为 1.0（用于：Scorer 综合评分）
                </div>
                <table class="weights-table">
                  <thead>
                    <tr>
                      <th>市场状态</th>
                      <th v-for="s in WEIGHTS_STRATEGIES" :key="s.key">{{ s.label }}</th>
                      <th>合计</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in WEIGHTS_STATES" :key="row.key">
                      <td>{{ row.label }}</td>
                      <td v-for="st in WEIGHTS_STRATEGIES" :key="st.key">
                        <a-input-number
                          v-model:value="weightsEdit[row.key][st.key]"
                          :min="0"
                          :max="1"
                          :step="0.05"
                          style="width: 96px"
                        />
                      </td>
                      <td :class="{ invalid: Math.abs(WEIGHTS_STRATEGIES.reduce((a, st) => a + (weightsEdit[row.key][st.key] ?? 0), 0) - 1) > 0.01 }">
                        {{ WEIGHTS_STRATEGIES.reduce((a, st) => a + (weightsEdit[row.key][st.key] ?? 0), 0).toFixed(2) }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div
                v-for="def in panelDefs.advanced"
                :key="def.config_key"
                class="config-block"
              >
                <div class="config-header">
                  <div>
                    <span class="config-title">{{ def.title }}</span>
                    <a-tag v-if="isCustomized(def)" color="blue" style="margin-left: 8px">已自定义</a-tag>
                    <a-tag v-else color="default" style="margin-left: 8px">使用默认值</a-tag>
                    <a-tag v-if="isDirty(def)" color="orange" style="margin-left: 4px">未保存</a-tag>
                  </div>
                  <a-space>
                    <a-button size="small" type="link" @click="resetOne(def)">恢复默认</a-button>
                    <a-button
                      size="small"
                      type="primary"
                      ghost
                      :disabled="!isDirty(def)"
                      :loading="saveLoading"
                      @click="saveOne(def)"
                    >
                      保存此项
                    </a-button>
                  </a-space>
                </div>
                <div class="config-desc">
                  {{ def.description }}
                  <span class="config-consumer">（用于：{{ def.consumer }}）</span>
                </div>
                <a-form layout="horizontal" :label-col="{ style: 'width: 180px' }" :colon="false">
                  <a-form-item
                    v-for="field in fieldsForPanel(def, 'advanced')"
                    :key="field.key"
                    style="margin-bottom: 8px"
                  >
                    <template #label>
                      <TermLabel
                        v-if="field.tooltipTerm"
                        :term="field.tooltipTerm"
                        :label="field.label"
                        :show-icon="false"
                      />
                      <span v-else>{{ field.label }}</span>
                    </template>
                    <a-input-number
                      v-if="field.type === 'percent'"
                      v-model:value="editMap[def.config_key][field.key] as number"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step"
                      :formatter="(v: number | string | undefined) => v === undefined || v === '' ? '' : `${(Number(v) * 100).toFixed(3)}%`"
                      :parser="(v: string | undefined) => v ? Number(v.replace('%', '')) / 100 : 0"
                      style="width: 200px"
                    />
                    <a-input-number
                      v-else
                      v-model:value="editMap[def.config_key][field.key] as number"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step"
                      :precision="field.type === 'integer' ? 0 : undefined"
                      :addon-after="field.suffix"
                      style="width: 200px"
                    />
                    <span class="config-default">
                      默认：{{ formatFieldValue(field, field.default) }}
                    </span>
                  </a-form-item>
                </a-form>
              </div>
            </a-collapse-panel>

            <!-- expert -->
            <a-collapse-panel key="expert" header="【专家】因子监控 / 细粒度阈值">
              <div
                v-for="def in panelDefs.expert"
                :key="def.config_key"
                class="config-block"
              >
                <div class="config-header">
                  <div>
                    <span class="config-title">{{ def.title }}</span>
                    <a-tag v-if="isCustomized(def)" color="blue" style="margin-left: 8px">已自定义</a-tag>
                    <a-tag v-else color="default" style="margin-left: 8px">使用默认值</a-tag>
                    <a-tag v-if="isDirty(def)" color="orange" style="margin-left: 4px">未保存</a-tag>
                  </div>
                  <a-space>
                    <a-button size="small" type="link" @click="resetOne(def)">恢复默认</a-button>
                    <a-button
                      size="small"
                      type="primary"
                      ghost
                      :disabled="!isDirty(def)"
                      :loading="saveLoading"
                      @click="saveOne(def)"
                    >
                      保存此项
                    </a-button>
                  </a-space>
                </div>
                <div class="config-desc">
                  {{ def.description }}
                  <span class="config-consumer">（用于：{{ def.consumer }}）</span>
                </div>
                <a-form layout="horizontal" :label-col="{ style: 'width: 180px' }" :colon="false">
                  <a-form-item
                    v-for="field in fieldsForPanel(def, 'expert')"
                    :key="field.key"
                    style="margin-bottom: 8px"
                  >
                    <template #label>
                      <TermLabel
                        v-if="field.tooltipTerm"
                        :term="field.tooltipTerm"
                        :label="field.label"
                        :show-icon="false"
                      />
                      <span v-else>{{ field.label }}</span>
                    </template>
                    <a-input-number
                      v-if="field.type === 'percent'"
                      v-model:value="editMap[def.config_key][field.key] as number"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step"
                      :formatter="(v: number | string | undefined) => v === undefined || v === '' ? '' : `${(Number(v) * 100).toFixed(3)}%`"
                      :parser="(v: string | undefined) => v ? Number(v.replace('%', '')) / 100 : 0"
                      style="width: 200px"
                    />
                    <a-input-number
                      v-else
                      v-model:value="editMap[def.config_key][field.key] as number"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step"
                      :precision="field.type === 'integer' ? 0 : undefined"
                      :addon-after="field.suffix"
                      style="width: 200px"
                    />
                    <span class="config-default">
                      默认：{{ formatFieldValue(field, field.default) }}
                    </span>
                  </a-form-item>
                </a-form>
              </div>
            </a-collapse-panel>
          </a-collapse>
        </a-spin>
      </a-tab-pane>

      <!-- Tab 2: 提醒设置 -->
      <a-tab-pane key="notify" tab="提醒设置">
        <a-alert
          v-if="!wxStatus.wx_configured"
          type="warning"
          show-icon
          message="微信通道未配置"
          description="未配置 WXPUSHER_APP_TOKEN / UID，所有通知仍会写入站内通知中心；微信推送不可用。"
          style="margin-bottom: 16px"
        />
        <a-alert
          v-else
          type="success"
          show-icon
          :message="`微信已绑定（${wxStatus.uid_masked ?? '已配置'}）`"
          style="margin-bottom: 16px"
        />
        <a-card size="small">
          <a-form layout="horizontal" :label-col="{ style: 'width: 200px' }" :colon="false">
            <a-form-item
              v-for="f in NOTIFICATION_FIELDS"
              :key="f.key"
              :label="f.label"
              style="margin-bottom: 8px"
            >
              <a-switch
                v-if="f.type === 'boolean'"
                v-model:checked="notifyEdit[f.key] as boolean"
              />
              <a-input-number
                v-else
                v-model:value="notifyEdit[f.key] as number"
                :min="f.min"
                :max="f.max"
                :step="f.step"
                :precision="0"
                :addon-after="'时'"
                style="width: 120px"
              />
            </a-form-item>
          </a-form>
          <a-space style="margin-top: 12px">
            <a-button @click="resetNotify">恢复默认</a-button>
            <a-button type="primary" :loading="saveLoading" @click="saveNotify">保存</a-button>
          </a-space>
        </a-card>
      </a-tab-pane>

      <!-- Tab 3: 黑白名单 -->
      <a-tab-pane key="watchlist" tab="黑白名单">
        <a-card size="small" style="margin-bottom: 16px">
          <a-form layout="inline">
            <a-form-item label="股票代码">
              <a-input v-model:value="watchAdd.ts_code" placeholder="如 000001.SZ" allow-clear style="width: 140px" />
            </a-form-item>
            <a-form-item label="分类">
              <a-select v-model:value="watchAdd.list_type" style="width: 120px">
                <a-select-option value="WHITELIST">白名单</a-select-option>
                <a-select-option value="BLACKLIST">黑名单</a-select-option>
              </a-select>
            </a-form-item>
            <a-form-item label="原因">
              <a-input v-model:value="watchAdd.reason" placeholder="可选" allow-clear style="width: 200px" />
            </a-form-item>
            <a-form-item>
              <a-button type="primary" @click="addWatch">添加</a-button>
            </a-form-item>
          </a-form>
        </a-card>

        <a-tabs size="small">
          <a-tab-pane key="white" :tab="`白名单 (${watchlistWhite.length})`">
            <a-table
              :columns="watchlistColumns"
              :data-source="watchlistWhite"
              row-key="id"
              size="small"
              :pagination="{ pageSize: 10 }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'action'">
                  <a-popconfirm title="确定删除？" @confirm="removeWatch(record as WatchlistItem)">
                    <a-button size="small" type="link" danger>删除</a-button>
                  </a-popconfirm>
                </template>
              </template>
            </a-table>
          </a-tab-pane>
          <a-tab-pane key="black" :tab="`黑名单 (${watchlistBlack.length})`">
            <a-table
              :columns="watchlistColumns"
              :data-source="watchlistBlack"
              row-key="id"
              size="small"
              :pagination="{ pageSize: 10 }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'action'">
                  <a-popconfirm title="确定删除？" @confirm="removeWatch(record as WatchlistItem)">
                    <a-button size="small" type="link" danger>删除</a-button>
                  </a-popconfirm>
                </template>
              </template>
            </a-table>
          </a-tab-pane>
        </a-tabs>
      </a-tab-pane>

      <!-- Tab 4: 变更历史 -->
      <a-tab-pane key="history" tab="变更历史">
        <a-table
          :columns="historyColumns"
          :data-source="history"
          row-key="id"
          size="small"
          :pagination="{ pageSize: 20 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'title'">
              {{ defByKey[record.config_key]?.title ?? record.config_key }}
            </template>
            <template v-else-if="column.key === 'old_value'">
              {{ formatHistoryValue(defByKey[record.config_key], record.old_value) }}
            </template>
            <template v-else-if="column.key === 'new_value'">
              {{ formatHistoryValue(defByKey[record.config_key], record.new_value) }}
            </template>
            <template v-else-if="column.key === 'action'">
              <a-popconfirm title="确定回退？" @confirm="revert(record.id)">
                <a-button size="small" type="link" :disabled="!record.old_value">回退</a-button>
              </a-popconfirm>
            </template>
          </template>
        </a-table>
      </a-tab-pane>

      <!-- Tab 5: 导入/导出 -->
      <a-tab-pane key="yaml" tab="导入/导出">
        <a-card size="small" style="margin-bottom: 16px">
          <a-space>
            <a-button type="primary" @click="onExportYaml">下载 YAML 配置文件</a-button>
            <span style="color: #8c8c8c; font-size: 12px">
              导出当前已自定义的 user_config（已使用默认值的项不会出现）
            </span>
          </a-space>
        </a-card>

        <a-card size="small" title="导入 YAML">
          <input type="file" accept=".yaml,.yml" @change="onYamlFileChange" style="margin-bottom: 12px" />
          <a-textarea
            v-model:value="yamlDraft"
            placeholder="将 YAML 内容粘贴到这里，或上传文件"
            :rows="10"
            style="font-family: Menlo, Consolas, monospace"
          />
          <a-space style="margin-top: 12px">
            <a-button :loading="yamlUploadLoading" @click="onDryRunYaml">预览差异</a-button>
            <a-button type="primary" danger :loading="yamlUploadLoading" @click="onApplyYaml">
              应用到 user_config
            </a-button>
          </a-space>

          <a-alert
            v-if="yamlDryRunResult"
            type="info"
            show-icon
            style="margin-top: 16px"
            :message="`预览：${yamlDryRunResult.changes.length} 项变更，${yamlDryRunResult.skipped_keys.length} 项未识别`"
          />
          <a-table
            v-if="yamlDryRunResult && yamlDryRunResult.changes.length"
            :columns="[
              { title: 'config_key', dataIndex: 'config_key', key: 'config_key', width: 200 },
              { title: '动作', dataIndex: 'action', key: 'action', width: 80 },
              { title: '新值', key: 'new_value' },
            ]"
            :data-source="(yamlDryRunResult as ImportResponse).changes"
            row-key="config_key"
            size="small"
            :pagination="false"
            style="margin-top: 16px"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'action'">
                <a-tag
                  :color="(record as ImportChange).action === 'create' ? 'green' : (record as ImportChange).action === 'update' ? 'blue' : 'default'"
                >
                  {{ (record as ImportChange).action }}
                </a-tag>
              </template>
              <template v-else-if="column.key === 'new_value'">
                <code style="font-size: 11px">{{ JSON.stringify((record as ImportChange).new_value) }}</code>
              </template>
            </template>
          </a-table>
        </a-card>
      </a-tab-pane>

      <!-- Tab 6: 个人资料（V1.5-G G-5） -->
      <a-tab-pane key="profile" tab="个人资料">
        <a-card size="small" title="账号信息" style="max-width: 560px; margin-bottom: 16px">
          <a-descriptions :column="1" size="small">
            <a-descriptions-item label="用户名">
              {{ auth.username ?? '—' }}
            </a-descriptions-item>
            <a-descriptions-item label="邮箱">
              {{ auth.email ?? '—' }}
            </a-descriptions-item>
            <a-descriptions-item label="当前层级">
              <a-tag color="blue">{{ auth.level }}</a-tag>
            </a-descriptions-item>
          </a-descriptions>
        </a-card>

        <a-card size="small" title="用户层级" style="max-width: 560px">
          <a-alert
            type="info"
            show-icon
            message="层级是内容深浅偏好，不是权限——随时可改，立即生效。"
            style="margin-bottom: 16px"
          />
          <a-radio-group v-model:value="levelDraft">
            <a-space direction="vertical" size="middle">
              <a-radio v-for="opt in LEVEL_OPTIONS" :key="opt.value" :value="opt.value">
                <b>{{ opt.label }}</b>
                <div class="level-desc">{{ opt.desc }}</div>
              </a-radio>
            </a-space>
          </a-radio-group>
          <div style="margin-top: 16px">
            <a-button
              type="primary"
              :loading="levelSaving"
              :disabled="levelDraft === auth.level"
              @click="saveLevel"
            >
              保存层级
            </a-button>
          </div>
        </a-card>
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<style scoped>
.config-block {
  padding: 12px 0;
  border-bottom: 1px dashed #e8e8e8;
}
.config-block:last-child {
  border-bottom: none;
}
.config-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.config-title {
  font-weight: 600;
  font-size: 14px;
}
.config-desc {
  color: #8c8c8c;
  font-size: 12px;
  margin-bottom: 12px;
}
.config-consumer {
  color: #1677ff;
}
.config-default {
  margin-left: 12px;
  color: #8c8c8c;
  font-size: 12px;
}

.weights-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.weights-table th,
.weights-table td {
  border: 1px solid #f0f0f0;
  padding: 8px;
  text-align: center;
}
.weights-table th {
  background: #fafafa;
}
.weights-table td.invalid {
  color: #cf1322;
  font-weight: 600;
}

.level-desc {
  color: #8c8c8c;
  font-size: 12px;
  margin-top: 2px;
}
</style>
