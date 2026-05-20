<script setup lang="ts">
/**
 * Phase 12 §3.3.3：多因子归因 OLS 展示（4 因子 β bar chart + R² + IC 时序）。
 *
 * 数据源：GET /attribution/history?start_date=...&end_date=...
 * 区间内每月 1 次 calc_date × 4 因子 → 取最近一个月的 β 显示。
 */
import { computed, ref, watch } from 'vue'
import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import DisclaimerBanner from '@/components/DisclaimerBanner.vue'
import { getAttributionHistory } from '@/api/attribution'
import { STRATEGY_LABELS } from '@/utils/lineage'
import type { AttributionHistoryItem } from '@/types/api'

use([BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{
  startDate: string
  endDate: string
}>()

const loading = ref(false)
const items = ref<AttributionHistoryItem[]>([])
const errorMsg = ref('')

const FACTOR_COLORS: Record<string, string> = {
  trend: '#2f54eb',
  momentum: '#13c2c2',
  mean_reversion: '#fa8c16',
  value: '#722ed1',
}

async function refresh(): Promise<void> {
  if (!props.startDate || !props.endDate) return
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await getAttributionHistory({
      start_date: props.startDate,
      end_date: props.endDate,
    })
    items.value = data.items ?? []
  } catch (err: unknown) {
    const e = err as { response?: { data?: { msg?: string } } }
    errorMsg.value = e.response?.data?.msg ?? '加载归因数据失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

watch(() => [props.startDate, props.endDate], refresh, { immediate: true })

// 取最近 calc_date 的 4 因子 β（backend desc 排序，items[0].calc_date 为最新）
const latestMonth = computed<AttributionHistoryItem[]>(() => {
  if (items.value.length === 0) return []
  const latest = items.value[0].calc_date
  return items.value.filter((i) => i.calc_date === latest)
})

const latestRSquared = computed(() => {
  const row = latestMonth.value[0]
  return row?.r_squared ?? null
})

const latestSample = computed(() => {
  const row = latestMonth.value[0]
  return row?.sample_size ?? null
})

const latestWindow = computed(() => {
  const row = latestMonth.value[0]
  return row?.window_days ?? null
})

const chartOption = computed(() => {
  const data = latestMonth.value
  if (data.length === 0) {
    return {
      tooltip: {},
      xAxis: { type: 'category', data: [] },
      yAxis: { type: 'value' },
      series: [],
    }
  }
  const factors = data.map((d) => d.factor)
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p: { name: string; value: number; data: { itemStyle?: { color?: string } } }) => {
        const factor = p.name
        const row = data.find((d) => d.factor === factor)
        const tStat = row?.t_stat != null ? row.t_stat.toFixed(2) : '—'
        return `${STRATEGY_LABELS[factor] ?? factor}<br/>
          β = <b>${p.value.toFixed(4)}</b><br/>
          t = ${tStat}<br/>
          sample = ${row?.sample_size ?? '—'}`
      },
    },
    grid: { left: '3%', right: '4%', bottom: '8%', containLabel: true },
    xAxis: {
      type: 'category',
      data: factors.map((f) => STRATEGY_LABELS[f] ?? f),
    },
    yAxis: { type: 'value', name: 'β' },
    series: [
      {
        type: 'bar',
        data: data.map((d) => ({
          value: Number(d.beta),
          itemStyle: { color: FACTOR_COLORS[d.factor] ?? '#999' },
        })),
        barWidth: '40%',
        label: {
          show: true,
          position: 'top',
          formatter: ({ value }: { value: number }) => value.toFixed(4),
        },
      },
    ],
  }
})

const noData = computed(() => !loading.value && items.value.length === 0)
</script>

<template>
  <div class="attribution-panel">
    <a-alert
      v-if="errorMsg"
      :message="errorMsg"
      type="warning"
      show-icon
      style="margin-bottom: 8px"
    />
    <a-spin :spinning="loading">
      <template v-if="!noData">
        <div class="meta">
          R² <b>{{ latestRSquared?.toFixed(4) ?? '—' }}</b> ·
          样本 <b>{{ latestSample ?? '—' }}</b> ·
          窗口 <b>{{ latestWindow ?? '—' }}d</b>
        </div>
        <v-chart :option="chartOption" style="height: 220px; width: 100%" autoresize />
      </template>
      <a-empty v-else description="该区间无归因数据" />
    </a-spin>
    <DisclaimerBanner
      text="历史归因仅用于内部审计与策略反思，反映模型对历史数据的拟合结果，不构成未来收益预测、不构成任何投资建议、不接受委托、不构成投顾服务。"
    />
  </div>
</template>

<style scoped>
.attribution-panel {
  padding: 8px 0;
}
.meta {
  margin-bottom: 8px;
  color: rgba(0, 0, 0, 0.65);
  font-size: 13px;
}
</style>
