<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import { useSignalStore } from '@/stores/signals'
import { usePositionStore } from '@/stores/positions'
import SignalCard from '@/components/SignalCard.vue'
import KlineChart from '@/components/KlineChart.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import DisclaimerBanner from '@/components/DisclaimerBanner.vue'
import { fmtPct, fmtDate, fmtAmount } from '@/utils/format'
import { getKline } from '@/api/market'
import { message } from 'ant-design-vue'
import type { KlineBar, Signal } from '@/types/api'

const signalStore = useSignalStore()
const positionStore = usePositionStore()

const activeTab = ref('today')
const drawerOpen = ref(false)
const selectedSignal = ref<Signal | null>(null)
const tradeModalOpen = ref(false)
const tradeForm = ref({ ts_code: '', price: 0, shares: 0, trade_type: 'BUY' })
const tradeLoading = ref(false)
const tradeHint = ref('')   // 价格/股数的推算说明

// K 线数据
const klineBars = ref<KlineBar[]>([])
const klineLoading = ref(false)

// 建议金额 = suggested_pct × 总资产（买入：建议买入金额；卖出：建议减仓金额）
const suggestedAmount = computed(() => {
  if (!selectedSignal.value?.suggested_pct || !positionStore.account?.total_assets) return null
  return selectedSignal.value.suggested_pct * positionStore.account.total_assets
})

// 卖出信号：计算减仓金额占当前持仓市值的比例，让用户直观了解"要卖多少"
const sellPositionPct = computed(() => {
  if (selectedSignal.value?.signal_type !== 'SELL') return null
  if (!suggestedAmount.value) return null
  const pos = positionStore.positions.find((p) => p.ts_code === selectedSignal.value?.ts_code)
  if (!pos?.market_value || pos.market_value <= 0) return null
  return suggestedAmount.value / pos.market_value
})

// History filters
const historyStatus = ref<string | undefined>()
const historySignalType = ref<string | undefined>()

onMounted(() => {
  signalStore.fetchSignals()
  positionStore.fetchAccount()
  positionStore.fetchPositions()  // 卖出信号需要持仓市值计算减仓比例
})

async function openSignal(signal: Signal) {
  selectedSignal.value = signal
  drawerOpen.value = true
  klineBars.value = []
  // 并行加载血缘 + K线
  klineLoading.value = true
  await Promise.allSettled([
    signalStore.fetchLineage(signal.id),
    getKline(signal.ts_code, 60).then((bars) => { klineBars.value = bars }).finally(() => { klineLoading.value = false }),
  ])
  if (signal.status === 'NEW') {
    await signalStore.updateStatus(signal.id, 'VIEWED')
  }
}

function openTradeModal(signal: Signal) {
  const totalAssets = positionStore.account?.total_assets ?? 0
  const lastClose = klineBars.value.length > 0
    ? (klineBars.value[klineBars.value.length - 1].close ?? 0)
    : 0
  const hints: string[] = []

  let price = 0
  let shares = 0

  if (signal.signal_type === 'BUY') {
    // ── 买入：以推荐区间中间值为参考价，无推荐价则用收盘价 ──────────
    if (signal.suggested_price_low != null && signal.suggested_price_high != null) {
      price = parseFloat(((signal.suggested_price_low + signal.suggested_price_high) / 2).toFixed(2))
      hints.push(`价格：推荐区间 ¥${signal.suggested_price_low}～¥${signal.suggested_price_high} 的中间值`)
    } else if (signal.suggested_price_low != null) {
      price = signal.suggested_price_low
      hints.push(`价格：推荐买入下限 ¥${price}`)
    } else if (signal.suggested_price_high != null) {
      price = signal.suggested_price_high
      hints.push(`价格：推荐买入上限 ¥${price}`)
    } else if (lastClose > 0) {
      price = lastClose
      hints.push(`价格：最新收盘价 ¥${price}（无推荐价，仅供参考）`)
    }
  } else {
    // ── 卖出：优先用持仓 current_price（语义最准确），回退到 K 线收盘价 ──
    const pos = positionStore.positions.find((p) => p.ts_code === signal.ts_code)
    const posPrice = pos?.current_price ?? 0
    if (posPrice > 0) {
      price = posPrice
      hints.push(`价格：持仓同步价 ¥${price}`)
    } else if (lastClose > 0) {
      price = lastClose
      hints.push(`价格：最新收盘价 ¥${price}（持仓未同步，仅供参考）`)
    }
  }

  // ── 推算股数（买入和卖出均适用同一公式） ────────────────────────────
  if (price > 0 && signal.suggested_pct != null && totalAssets > 0) {
    const amount = totalAssets * signal.suggested_pct
    const rawShares = Math.floor(amount / price / 100) * 100
    if (rawShares >= 100) {
      shares = rawShares
      const label = signal.signal_type === 'BUY' ? '买入' : '减仓'
      hints.push(`股数：总资产 ${(signal.suggested_pct * 100).toFixed(0)}% ≈ ¥${Math.round(amount).toLocaleString()}，参考${label} ${shares} 股`)
    } else {
      // 建议金额不足 1 手（100 股）→ 最小单位兜底
      shares = 100
      hints.push(`股数：建议金额 ¥${Math.round(amount).toLocaleString()} 不足 1 手，已调整为最小减仓 100 股`)
    }
  }

  tradeHint.value = hints.join('\n')
  tradeForm.value = {
    ts_code: signal.ts_code,
    price,
    shares: Math.max(shares, 0),
    trade_type: signal.signal_type,
  }
  tradeModalOpen.value = true
}

async function submitTrade() {
  tradeLoading.value = true
  try {
    await positionStore.recordTrade({
      account_id: positionStore.account?.id ?? 1,
      ts_code: tradeForm.value.ts_code,
      trade_type: tradeForm.value.trade_type as 'BUY' | 'SELL',
      price: tradeForm.value.price,
      shares: tradeForm.value.shares,
      trade_date: new Date().toISOString().slice(0, 10),
    })
    message.success('交易录入成功')
    tradeModalOpen.value = false
  } catch (err: unknown) {
    const e = err as { response?: { data?: { msg?: string; detail?: string } } }
    const detail = e.response?.data?.msg || e.response?.data?.detail || '交易录入失败'
    message.error(detail, 6)
  } finally {
    tradeLoading.value = false
  }
}

async function loadHistory() {
  await signalStore.fetchHistory({
    status: historyStatus.value,
    signal_type: historySignalType.value,
  })
}

watch(activeTab, (tab) => {
  if (tab === 'history' && signalStore.history.length === 0) {
    loadHistory()
  }
})

const historyColumns = [
  { title: '代码/名称', dataIndex: 'ts_code', key: 'ts_code' },
  { title: '日期', dataIndex: 'trade_date', key: 'trade_date' },
  { title: '类型', dataIndex: 'signal_type', key: 'signal_type' },
  { title: '评分', dataIndex: 'score', key: 'score' },
  { title: '建议仓位', dataIndex: 'suggested_pct', key: 'suggested_pct',
    customRender: ({ value }: { value: number }) => fmtPct(value) },
  { title: '状态', dataIndex: 'status', key: 'status' },
]
</script>

<template>
  <div>
    <DisclaimerBanner
      text="信号为算法量化结果，反映模型对历史数据的拟合判断，不构成任何投资建议、不接受委托、不构成投顾服务。是否依据信号交易由用户自行决策并承担全部后果。"
    />
    <a-tabs v-model:active-key="activeTab">
      <!-- 最新信号 Tab -->
      <a-tab-pane key="today" tab="最新信号">
        <a-spin :spinning="signalStore.loading">
          <div
            v-if="signalStore.signalDate"
            style="margin-bottom: 12px; color: rgba(0,0,0,.45); font-size: 13px"
          >
            信号日期：{{ signalStore.signalDate }}
          </div>
          <template v-if="signalStore.signals.length > 0">
            <SignalCard
              v-for="signal in signalStore.signals"
              :key="signal.id"
              :signal="signal"
              @click="openSignal"
            />
          </template>
          <EmptyState v-else title="暂无信号" description="每日收盘后 17:30 Pipeline 生成当日信号" />
        </a-spin>
      </a-tab-pane>

      <!-- 历史信号 Tab -->
      <a-tab-pane key="history" tab="历史信号">
        <a-space style="margin-bottom: 12px">
          <a-select
            v-model:value="historySignalType"
            placeholder="类型筛选"
            allow-clear
            style="width: 110px"
          >
            <a-select-option value="BUY">买入</a-select-option>
            <a-select-option value="SELL">卖出</a-select-option>
          </a-select>
          <a-select
            v-model:value="historyStatus"
            placeholder="状态筛选"
            allow-clear
            style="width: 110px"
          >
            <a-select-option value="NEW">新信号</a-select-option>
            <a-select-option value="VIEWED">已查看</a-select-option>
            <a-select-option value="ACTED">已操作</a-select-option>
            <a-select-option value="EXPIRED">已过期</a-select-option>
          </a-select>
          <a-button type="primary" @click="loadHistory">查询</a-button>
        </a-space>
        <a-table
          :columns="historyColumns"
          :data-source="signalStore.history"
          :loading="signalStore.loading"
          row-key="id"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'ts_code'">
              <div style="font-weight: 600; font-size: 13px">{{ record.ts_code }}</div>
              <div v-if="record.name" style="color: rgba(0,0,0,.45); font-size: 12px">{{ record.name }}</div>
            </template>
            <template v-if="column.key === 'signal_type'">
              <a-tag :color="record.signal_type === 'BUY' ? '#52c41a' : '#ff4d4f'">
                {{ record.signal_type === 'BUY' ? '买入' : '卖出' }}
              </a-tag>
            </template>
            <template v-if="column.key === 'status'">
              <StatusBadge :status="record.status" />
            </template>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>

    <!-- 信号详情抽屉 -->
    <a-drawer
      v-model:open="drawerOpen"
      title="信号详情"
      width="480"
      @close="selectedSignal = null"
    >
      <template v-if="selectedSignal">
        <a-descriptions :column="1" size="small" bordered>
          <a-descriptions-item label="股票">
            <span style="font-weight: 600">{{ selectedSignal.ts_code }}</span>
            <span v-if="selectedSignal.name" style="margin-left: 8px; color: rgba(0,0,0,.65)">{{ selectedSignal.name }}</span>
          </a-descriptions-item>
          <a-descriptions-item label="信号类型">
            <a-tag :color="selectedSignal.signal_type === 'BUY' ? '#52c41a' : '#ff4d4f'">
              {{ selectedSignal.signal_type === 'BUY' ? '买入' : '卖出' }}
            </a-tag>
          </a-descriptions-item>
          <a-descriptions-item label="综合评分">{{ selectedSignal.score }}</a-descriptions-item>
          <a-descriptions-item label="信号强度">{{ selectedSignal.signal_strength ?? '—' }}</a-descriptions-item>
          <!-- 推荐价格区间（买入信号有值；卖出信号通常为空） -->
          <a-descriptions-item
            v-if="selectedSignal.signal_type === 'BUY'"
            label="推荐买入区间"
          >
            <template v-if="selectedSignal.suggested_price_low != null || selectedSignal.suggested_price_high != null">
              <span style="color: #52c41a; font-weight: 600">
                ¥{{ selectedSignal.suggested_price_low ?? '—' }}
                ～
                ¥{{ selectedSignal.suggested_price_high ?? '—' }}
              </span>
            </template>
            <span v-else style="color: rgba(0,0,0,.45)">—</span>
          </a-descriptions-item>
          <a-descriptions-item
            v-if="selectedSignal.stop_loss_price != null"
            label="止损价"
          >
            <span style="color: #cf1322; font-weight: 600">¥{{ selectedSignal.stop_loss_price }}</span>
          </a-descriptions-item>
          <a-descriptions-item
            :label="selectedSignal.signal_type === 'BUY' ? '建议仓位' : '建议减仓'"
          >
            <template v-if="selectedSignal.signal_type === 'BUY'">
              <!-- 买入：占总资产 X%，约 ¥N -->
              {{ fmtPct(selectedSignal.suggested_pct) }}
              <span v-if="suggestedAmount !== null" style="margin-left: 8px; color: rgba(0,0,0,.45)">
                ≈ {{ fmtAmount(suggestedAmount) }}
              </span>
            </template>
            <template v-else>
              <!-- 卖出：相当于总资产 X%，即约 ¥N；若有持仓则换算成持仓占比 -->
              <span>相当于总资产 {{ fmtPct(selectedSignal.suggested_pct) }}</span>
              <span v-if="suggestedAmount !== null" style="margin-left: 4px; color: rgba(0,0,0,.45)">
                （≈ {{ fmtAmount(suggestedAmount) }}）
              </span>
              <div v-if="sellPositionPct !== null" style="margin-top: 2px; font-size: 12px; color: #cf1322">
                即减持当前持仓约 {{ fmtPct(sellPositionPct) }}
              </div>
            </template>
          </a-descriptions-item>
          <a-descriptions-item label="交易日">{{ selectedSignal.trade_date }}</a-descriptions-item>
          <a-descriptions-item label="状态">
            <StatusBadge :status="selectedSignal.status" />
          </a-descriptions-item>
        </a-descriptions>

        <!-- T+1 提示 -->
        <a-alert
          v-if="selectedSignal.t1_warning"
          type="warning"
          :message="selectedSignal.t1_warning"
          show-icon
          style="margin-top: 12px"
        />

        <!-- 血缘信息（Phase 12 精简：仅展示关键 L1 概览 + 跳转完整三层视图） -->
        <template v-if="signalStore.currentLineage">
          <a-divider>信号血缘</a-divider>
          <a-descriptions :column="1" size="small">
            <a-descriptions-item label="CP1 数据就绪">
              {{ fmtDate(signalStore.currentLineage.pipeline_run?.cp1_at ?? null) }}
            </a-descriptions-item>
            <a-descriptions-item label="CP3 信号生成">
              {{ fmtDate(signalStore.currentLineage.pipeline_run?.cp3_at ?? null) }}
            </a-descriptions-item>
          </a-descriptions>
          <template v-if="signalStore.currentLineage.score_snapshot">
            <a-descriptions :column="1" size="small" style="margin-top: 8px">
              <a-descriptions-item label="综合评分">
                {{ signalStore.currentLineage.score_snapshot.composite_score?.toFixed(2) ?? '—' }}
              </a-descriptions-item>
              <a-descriptions-item label="市场状态">
                {{ signalStore.currentLineage.score_snapshot.market_state ?? '—' }}
              </a-descriptions-item>
              <a-descriptions-item label="触发原因">
                {{ signalStore.currentLineage.score_snapshot.trigger_reason ?? '—' }}
              </a-descriptions-item>
            </a-descriptions>
          </template>
          <a-button
            type="link"
            size="small"
            style="margin-top: 4px; padding: 0"
            @click="$router.push(`/signals/${selectedSignal.id}/lineage`)"
          >
            查看完整三层评分溯源 →
          </a-button>
        </template>

        <!-- K 线图 -->
        <a-divider>K 线走势（近60日）</a-divider>
        <a-spin :spinning="klineLoading">
          <KlineChart v-if="klineBars.length >= 2" :bars="klineBars" height="280px" />
          <EmptyState v-else title="暂无 K 线数据" description="需采集日线行情后显示" />
        </a-spin>

        <a-divider />
        <a-space>
          <a-button type="primary" @click="openTradeModal(selectedSignal)">录入交易</a-button>
          <a-button
            v-if="selectedSignal.status === 'NEW' || selectedSignal.status === 'VIEWED'"
            @click="signalStore.updateStatus(selectedSignal.id, 'ACTED')"
          >
            标记已操作
          </a-button>
        </a-space>
      </template>
    </a-drawer>

    <!-- 交易录入 Modal -->
    <a-modal
      v-model:open="tradeModalOpen"
      title="录入交易"
      :confirm-loading="tradeLoading"
      @ok="submitTrade"
    >
      <a-form layout="vertical">
        <!-- 推算说明 -->
        <a-alert
          v-if="tradeHint"
          type="info"
          :message="tradeHint"
          show-icon
          style="margin-bottom: 12px; white-space: pre-line; font-size: 12px"
        />
        <a-form-item label="股票代码">
          <a-input v-model:value="tradeForm.ts_code" />
        </a-form-item>
        <a-form-item label="交易方向">
          <a-radio-group v-model:value="tradeForm.trade_type">
            <a-radio value="BUY">买入</a-radio>
            <a-radio value="SELL">卖出</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="成交价格">
          <a-input-number v-model:value="tradeForm.price" :min="0" :precision="2" style="width: 100%" />
        </a-form-item>
        <a-form-item label="成交数量（股）">
          <a-input-number v-model:value="tradeForm.shares" :min="100" :step="100" style="width: 100%" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>
