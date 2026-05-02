<script setup lang="ts">
import { onMounted, ref, computed, watch } from 'vue'
import { useMarketStore } from '@/stores/market'
import { useSignalStore } from '@/stores/signals'
import { getAccount } from '@/api/positions'
import { getPerformanceSummary, getPerformanceHistory } from '@/api/performance'
import NavChart from '@/components/NavChart.vue'
import SignalCard from '@/components/SignalCard.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import TermLabel from '@/components/TermLabel.vue'
import DisclaimerBanner from '@/components/DisclaimerBanner.vue'
import { fmtPct, fmtAmount } from '@/utils/format'
import type { AccountSummary, PerformanceSummary, NavPoint, BenchmarkPoint } from '@/types/api'

const marketStore = useMarketStore()
const signalStore = useSignalStore()

const account = ref<AccountSummary | null>(null)
const perfSummary = ref<PerformanceSummary | null>(null)
const navSeries = ref<NavPoint[]>([])
const benchmarkSeries = ref<BenchmarkPoint[]>([])
const loading = ref(true)
const chartLoading = ref(false)

// 期间选择器：trading days limit
const PERIOD_OPTIONS = [
  { label: '1个月', value: 21 },
  { label: '3个月', value: 63 },
  { label: '6个月', value: 126 },
  { label: '1年', value: 252 },
  { label: '全部', value: 9999 },
]
const selectedPeriod = ref(63)

const currentMarketState = computed(() => marketStore.currentState?.current ?? null)

const positionRatio = computed(() => {
  if (!account.value) return null
  const { total_assets, cash } = account.value
  if (!total_assets || total_assets === 0) return null
  return (total_assets - cash) / total_assets
})

onMounted(async () => {
  loading.value = true
  await Promise.allSettled([
    marketStore.fetchCurrentState(),
    signalStore.fetchSignals(),
    loadAccount(),
    loadPerformance(),
  ])
  loading.value = false
})

async function loadAccount() {
  try {
    account.value = await getAccount()
  } catch {
    account.value = null
  }
}

async function loadPerformance(limit?: number) {
  try {
    if (limit === undefined) {
      // 首次加载同时拉摘要
      perfSummary.value = await getPerformanceSummary()
    }
    const hist = await getPerformanceHistory(limit ?? selectedPeriod.value)
    navSeries.value = hist.nav_series
    benchmarkSeries.value = hist.benchmark_series ?? []
  } catch {
    if (limit === undefined) perfSummary.value = null
  }
}

watch(selectedPeriod, async (val) => {
  chartLoading.value = true
  try {
    await loadPerformance(val)
  } finally {
    chartLoading.value = false
  }
})

const topSignals = computed(() => signalStore.signals.slice(0, 5))
</script>

<template>
  <div>
    <DisclaimerBanner
      text="本系统为个人量化交易决策辅助工具，所有市场状态判断、信号、绩效与持仓展示均基于历史数据与算法模型，不构成任何投资建议、不接受委托、不构成投顾服务。投资决策与盈亏由用户自行承担。"
    />
    <a-spin :spinning="loading">
      <!-- 市场状态 -->
      <a-row :gutter="16" style="margin-bottom: 16px">
        <a-col :span="24">
          <a-card size="small">
            <span style="margin-right: 8px; font-weight: 600">当前市场状态：</span>
            <StatusBadge
              v-if="currentMarketState"
              :status="currentMarketState.market_state"
            />
            <span v-else style="color: rgba(0,0,0,.45)">暂无市场状态（尚未运行 Pipeline）</span>
            <span v-if="currentMarketState" style="margin-left: 12px; color: rgba(0,0,0,.45); font-size: 12px">
              {{ currentMarketState.description }}
            </span>
          </a-card>
        </a-col>
      </a-row>

      <!-- 资产概览卡片 -->
      <a-row :gutter="16" style="margin-bottom: 16px">
        <a-col :xs="24" :sm="12" :md="6">
          <a-statistic
            title="总资产"
            :value="account ? fmtAmount(account.total_assets) : '—'"
            style="background: #fff; padding: 16px; border-radius: 8px"
          />
        </a-col>
        <a-col :xs="24" :sm="12" :md="6">
          <a-statistic
            title="可用现金"
            :value="account ? fmtAmount(account.cash) : '—'"
            style="background: #fff; padding: 16px; border-radius: 8px"
          />
        </a-col>
        <a-col :xs="24" :sm="12" :md="6">
          <div class="metric-card">
            <div class="metric-card__title"><TermLabel term="cumulative_return" /></div>
            <div class="metric-card__value">
              {{ perfSummary ? fmtPct(perfSummary.cumulative_return) : '—' }}
            </div>
          </div>
        </a-col>
        <a-col :xs="24" :sm="12" :md="6">
          <a-statistic
            title="仓位水平"
            :value="positionRatio !== null ? fmtPct(positionRatio) : '—'"
            style="background: #fff; padding: 16px; border-radius: 8px"
          />
        </a-col>
      </a-row>

      <!-- 净值曲线 -->
      <a-card style="margin-bottom: 16px">
        <template #title>
          净值曲线
        </template>
        <template #extra>
          <a-radio-group
            v-model:value="selectedPeriod"
            size="small"
            button-style="solid"
          >
            <a-radio-button
              v-for="opt in PERIOD_OPTIONS"
              :key="opt.value"
              :value="opt.value"
            >
              {{ opt.label }}
            </a-radio-button>
          </a-radio-group>
        </template>
        <a-spin :spinning="chartLoading">
          <NavChart
            v-if="navSeries.length > 0"
            :nav-series="navSeries"
            :benchmark-series="benchmarkSeries.length > 1 ? benchmarkSeries : undefined"
          />
          <EmptyState v-else title="暂无净值数据" description="账户盯市同步后显示" />
        </a-spin>
      </a-card>

      <!-- 今日信号摘要 -->
      <a-card title="今日信号摘要（前5条）">
        <template v-if="topSignals.length > 0">
          <SignalCard
            v-for="signal in topSignals"
            :key="signal.id"
            :signal="signal"
          />
        </template>
        <EmptyState v-else title="今日暂无信号" description="Pipeline 运行后生成信号" />
      </a-card>
    </a-spin>
  </div>
</template>

<style scoped>
.metric-card {
  background: #fff;
  padding: 16px;
  border-radius: 8px;
}
.metric-card__title {
  color: rgba(0, 0, 0, 0.45);
  font-size: 14px;
  margin-bottom: 4px;
}
.metric-card__value {
  font-size: 24px;
  font-weight: 500;
}
</style>
