<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import { getFactorQuality, getFactorQualityHistory } from '@/api/factorQuality'
import EmptyState from '@/components/EmptyState.vue'
import TermLabel from '@/components/TermLabel.vue'
import type { FactorQualityItem } from '@/types/api'

use([LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer])

// ── 名称映射（专业名 → 中文通俗名） ─────────────────────────────────
const FACTOR_NAMES: Record<string, string> = {
  momentum_20d:  '动量因子（20日涨跌势）',
  pe_percentile: '估值因子（PE历史分位）',
  rsi_reversal:  '反转因子（RSI超买超卖）',
}
const STRATEGY_NAMES: Record<string, string> = {
  TrendStrategy:    '趋势策略',
  ValueStrategy:    '价值策略',
  ReversionStrategy:'反转策略',
}
const ALERT_LABELS: Record<string, { text: string; color: string; desc: string }> = {
  DECAY: { text: '效力衰减', color: 'red',
           desc: '该因子近3个月预测力持续为负，建议降低此因子权重或暂停使用' },
}

function factorLabel(name: string)    { return FACTOR_NAMES[name]    ?? name }
function strategyLabel(name: string)  { return STRATEGY_NAMES[name]  ?? name }

// ── 数据加载 ──────────────────────────────────────────────────────────
const loading = ref(true)
const latest  = ref<FactorQualityItem[]>([])
const history = ref<FactorQualityItem[]>([])

onMounted(async () => {
  loading.value = true
  const [l, h] = await Promise.all([
    getFactorQuality().catch(() => [] as FactorQualityItem[]),
    getFactorQualityHistory().catch(() => [] as FactorQualityItem[]),
  ])
  latest.value  = l
  history.value = h
  loading.value = false
})

// ── 健康状态判定 ──────────────────────────────────────────────────────
function healthStatus(item: FactorQualityItem): 'good' | 'warn' | 'alert' {
  if (item.alert_status) return 'alert'
  const ic = item.ic_mean_3m ?? 0
  return Math.abs(ic) >= 0.03 ? 'good' : 'warn'
}

// ── 预测力可读描述 ────────────────────────────────────────────────────
function icDesc(ic: number | null): string {
  if (ic === null) return '—'
  const abs = Math.abs(ic)
  if (abs >= 0.06) return '强'
  if (abs >= 0.03) return '有效'
  if (abs >= 0.01) return '较弱'
  return '无效'
}
function icColor(ic: number | null): string {
  if (ic === null) return ''
  const abs = Math.abs(ic)
  if (abs >= 0.06) return '#52c41a'
  if (abs >= 0.03) return '#1890ff'
  if (abs >= 0.01) return '#faad14'
  return '#ff4d4f'
}

// ── 图表（因子预测力趋势） ────────────────────────────────────────────
const factorKeys = computed(() => [...new Set(history.value.map((d) => d.factor_name))])

const chartOption = computed(() => {
  if (history.value.length === 0) return {}
  return {
    tooltip: {
      trigger: 'axis',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (params: any[]) =>
        (params[0]?.axisValue ?? '') + '<br/>' +
        params.map((p: any) =>
          `${p.marker}${factorLabel(p.seriesName)}: ${(+p.value[1]).toFixed(4)}`
        ).join('<br/>'),
    },
    legend: {
      data: factorKeys.value.map(factorLabel),
      bottom: 0,
      textStyle: { fontSize: 11 },
    },
    grid: { left: '3%', right: '4%', top: '8%', bottom: '14%', containLabel: true },
    xAxis: { type: 'time' },
    yAxis: {
      type: 'value',
      name: '预测力',
      scale: true,
      axisLabel: { formatter: (v: number) => v.toFixed(2) },
    },
    series: factorKeys.value.map((key) => ({
      name: factorLabel(key),
      type: 'line',
      data: history.value
        .filter((d) => d.factor_name === key)
        .map((d) => [d.calc_month, d.ic_mean_3m]),
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      markLine: {
        silent: true,
        lineStyle: { color: '#bbb', type: 'dashed' },
        data: [{ yAxis: 0, label: { show: false } }],
      },
    })),
  }
})

// ── 表格列 ───────────────────────────────────────────────────────────
// 列标题中需要 tooltip 的术语用 customHeaderCell 不可用，改用 slots+key 渲染。
const columns = [
  { title: '策略',   dataIndex: 'strategy_name', key: 'strategy_name', width: 90 },
  { title: '因子',   dataIndex: 'factor_name',   key: 'factor_name' },
  { title: '月份',   dataIndex: 'calc_month',    key: 'calc_month',   width: 90 },
  { dataIndex: 'ic_mean_3m',    key: 'ic_mean_3m',   width: 90, slots: { title: 'icHeader' } },
  { dataIndex: 'ir_3m',         key: 'ir_3m',        width: 90, slots: { title: 'irHeader' } },
  { dataIndex: 'half_life_days',key: 'half_life_days',width: 100, slots: { title: 'halfLifeHeader' } },
  { title: '状态',   dataIndex: 'alert_status',  key: 'alert_status', width: 100 },
]
</script>

<template>
  <div>
    <!-- 说明面板 -->
    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 16px"
      message="因子健康监控"
    >
      <template #description>
        <div style="font-size: 13px; line-height: 1.8">
          本页监控量化策略内各<b>因子</b>（选股依据）的有效性。
          <b>预测力</b>（IC值）衡量因子能否准确预测未来涨跌，绝对值越大越有效（&ge;0.03为有效，&ge;0.06为强）；
          <b>稳定性</b>（IR值）衡量预测力是否持续稳定，越高越好；
          <b>有效窗口</b>（半衰期）表示因子信号能持续发挥作用的大致天数。
          出现<a-tag color="red" style="margin: 0 4px">效力衰减</a-tag>告警时，
          建议在"设置"中降低该因子对应策略的权重。
        </div>
      </template>
    </a-alert>

    <a-spin :spinning="loading">
      <a-row :gutter="16">
        <!-- 左：因子状态表格 -->
        <a-col :xs="24" :lg="12">
          <a-card title="各因子近期健康状况">
            <a-table
              :columns="columns"
              :data-source="latest"
              :row-key="(r: FactorQualityItem) => `${r.factor_name}-${r.calc_month}`"
              size="small"
              :row-class-name="(r: FactorQualityItem) => r.alert_status ? 'row-degraded' : ''"
              :pagination="false"
            >
              <template #icHeader>
                <TermLabel term="ic_mean_3m" label="预测力" />
              </template>
              <template #irHeader>
                <TermLabel term="ir" label="稳定性" />
              </template>
              <template #halfLifeHeader>
                <TermLabel term="half_life" label="有效窗口" />
              </template>
              <template #bodyCell="{ column, record }">

                <!-- 策略名称 -->
                <template v-if="column.key === 'strategy_name'">
                  {{ strategyLabel(record.strategy_name) }}
                </template>

                <!-- 因子名称 -->
                <template v-if="column.key === 'factor_name'">
                  <a-tooltip :title="`技术名称：${record.factor_name}`">
                    <span style="border-bottom: 1px dashed #ccc; cursor: help">
                      {{ factorLabel(record.factor_name) }}
                    </span>
                  </a-tooltip>
                </template>

                <!-- 预测力 -->
                <template v-if="column.key === 'ic_mean_3m'">
                  <a-tooltip :title="`IC均值（3月）= ${record.ic_mean_3m?.toFixed(4) ?? '—'}，单月 IC = ${record.ic_value?.toFixed(4) ?? '—'}`">
                    <span :style="{ color: icColor(record.ic_mean_3m), fontWeight: 600, borderBottom: '1px dashed #ccc', cursor: 'help' }">
                      {{ icDesc(record.ic_mean_3m) }}
                    </span>
                  </a-tooltip>
                </template>

                <!-- 稳定性 -->
                <template v-if="column.key === 'ir_3m'">
                  <a-tooltip :title="`IR（信息比率，3月）= ${record.ir_3m?.toFixed(4) ?? '—'}，数值越高代表预测力越稳定`">
                    <span style="border-bottom: 1px dashed #ccc; cursor: help">
                      {{ record.ir_3m != null ? record.ir_3m.toFixed(2) : '—' }}
                    </span>
                  </a-tooltip>
                </template>

                <!-- 有效窗口（半衰期） -->
                <template v-if="column.key === 'half_life_days'">
                  <a-tooltip :title="`半衰期：因子预测力衰减到一半所需的天数，越长说明信号越持久`">
                    <span style="border-bottom: 1px dashed #ccc; cursor: help">
                      {{ record.half_life_days != null ? `${record.half_life_days.toFixed(0)} 天` : '—' }}
                    </span>
                  </a-tooltip>
                </template>

                <!-- 健康状态 -->
                <template v-if="column.key === 'alert_status'">
                  <template v-if="record.alert_status">
                    <a-tooltip :title="ALERT_LABELS[record.alert_status]?.desc ?? record.alert_status">
                      <a-tag color="red" style="cursor: help">
                        ⚠ {{ ALERT_LABELS[record.alert_status]?.text ?? record.alert_status }}
                      </a-tag>
                    </a-tooltip>
                  </template>
                  <template v-else-if="healthStatus(record) === 'good'">
                    <a-tag color="green">健康</a-tag>
                  </template>
                  <template v-else>
                    <a-tooltip title="预测力（IC均值）低于0.03，效果偏弱，建议关注">
                      <a-tag color="orange" style="cursor: help">偏弱</a-tag>
                    </a-tooltip>
                  </template>
                </template>

              </template>
            </a-table>
            <EmptyState v-if="latest.length === 0 && !loading" title="暂无因子质量数据" />
          </a-card>
        </a-col>

        <!-- 右：预测力趋势图 -->
        <a-col :xs="24" :lg="12">
          <a-card title="各因子预测力趋势（近3月均值）">
            <div style="margin-bottom: 8px; font-size: 12px; color: rgba(0,0,0,.45)">
              曲线在零线以上表示因子对涨跌有正向预测能力，越高越好；持续低于零线说明因子失效。
            </div>
            <v-chart
              v-if="history.length > 0"
              :option="chartOption"
              style="height: 360px; width: 100%"
              autoresize
            />
            <EmptyState v-else title="暂无历史趋势数据" />
          </a-card>
        </a-col>
      </a-row>
    </a-spin>
  </div>
</template>

<style>
.row-degraded td {
  background-color: #fff2f0 !important;
}
</style>
