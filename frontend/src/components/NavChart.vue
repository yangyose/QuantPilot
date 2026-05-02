<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import type { BenchmarkPoint, NavPoint } from '@/types/api'

use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{
  navSeries: NavPoint[]
  benchmarkSeries?: BenchmarkPoint[]
  height?: string
}>()

const option = computed(() => ({
  tooltip: {
    trigger: 'axis',
    formatter: (params: { marker: string; seriesName: string; value: [string, number] }[]) => {
      const lines = params.map(
        (p) => `${p.marker}${p.seriesName}: ${(+p.value[1]).toFixed(4)}`,
      )
      return `${params[0]?.value[0]}<br/>${lines.join('<br/>')}`
    },
  },
  legend: { data: ['策略净值', ...(props.benchmarkSeries?.length ? ['沪深300基准'] : [])] },
  grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
  xAxis: { type: 'time' },
  yAxis: { type: 'value', scale: true },
  series: [
    {
      name: '策略净值',
      type: 'line',
      data: props.navSeries.map((p) => [p.date, p.nav]),
      smooth: true,
      lineStyle: { width: 2 },
      symbol: 'none',
    },
    ...(props.benchmarkSeries?.length
      ? [
          {
            name: '沪深300基准',
            type: 'line',
            // 后端已归一化（相对首日倍数），直接使用 value 字段
            data: props.benchmarkSeries.map((p) => [p.date, p.value]),
            smooth: true,
            lineStyle: { width: 1.5 },
            symbol: 'none',
          },
        ]
      : []),
  ],
}))
</script>

<template>
  <v-chart
    :option="option"
    :style="{ height: height ?? '320px', width: '100%' }"
    autoresize
  />
</template>
