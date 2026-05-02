<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CandlestickChart, BarChart, LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  LegendComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import type { KlineBar } from '@/types/api'

use([
  CandlestickChart, BarChart, LineChart,
  GridComponent, TooltipComponent, DataZoomComponent, LegendComponent,
  CanvasRenderer,
])

const props = defineProps<{
  bars: KlineBar[]
  height?: string
}>()

/** 计算移动平均线，不足周期的位置返回 null */
function calcMA(closes: (number | null)[], period: number): (number | null)[] {
  return closes.map((_, i) => {
    if (i < period - 1) return null
    const slice = closes.slice(i - period + 1, i + 1)
    if (slice.some((v) => v === null)) return null
    const sum = slice.reduce<number>((s, v) => s + (v as number), 0)
    return parseFloat((sum / period).toFixed(3))
  })
}

const option = computed(() => {
  const dates = props.bars.map((b) => b.date)
  // ECharts candlestick 格式：[open, close, low, high]
  const ohlc = props.bars.map((b) => [b.open, b.close, b.low, b.high])
  const closes = props.bars.map((b) => b.close)
  const vols = props.bars.map((b) => b.vol ?? 0)

  const ma5  = calcMA(closes, 5)
  const ma10 = calcMA(closes, 10)
  const ma20 = calcMA(closes, 20)

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (params: any[]) => {
        const kline = params.find((p: any) => p.seriesName === 'K线')
        if (!kline) return ''
        const [o, c, l, h] = kline.value as [number, number, number, number]
        const date = (params[0] as any)?.axisValue ?? ''
        const maLines = params
          .filter((p: any) => ['MA5', 'MA10', 'MA20'].includes(p.seriesName) && p.value != null)
          .map((p: any) => `${p.marker}${p.seriesName}: ${(p.value as number).toFixed(2)}`)
        return [
          date,
          `开: ${o?.toFixed(2)}  收: ${c?.toFixed(2)}`,
          `低: ${l?.toFixed(2)}  高: ${h?.toFixed(2)}`,
          ...maLines,
        ].join('<br/>')
      },
    },
    legend: {
      top: 0,
      data: ['MA5', 'MA10', 'MA20'],
      textStyle: { fontSize: 11 },
      itemWidth: 16,
      itemHeight: 8,
    },
    grid: [
      { left: '8%', right: '4%', top: '28px', height: '58%' },
      { left: '8%', right: '4%', top: '76%', height: '16%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      { type: 'value', scale: true, gridIndex: 0, splitNumber: 4 },
      { type: 'value', gridIndex: 1, splitNumber: 2, axisLabel: { fontSize: 10 } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: ohlc,
        itemStyle: {
          color: '#ef5350',
          color0: '#26a69a',
          borderColor: '#ef5350',
          borderColor0: '#26a69a',
        },
      },
      {
        name: 'MA5',
        type: 'line',
        color: '#ffa000',          // 同时控制图例符号和折线颜色
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: ma5,
        smooth: false,
        symbol: 'none',
        lineStyle: { width: 1 },
        connectNulls: false,
      },
      {
        name: 'MA10',
        type: 'line',
        color: '#7b1fa2',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: ma10,
        smooth: false,
        symbol: 'none',
        lineStyle: { width: 1 },
        connectNulls: false,
      },
      {
        name: 'MA20',
        type: 'line',
        color: '#0288d1',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: ma20,
        smooth: false,
        symbol: 'none',
        lineStyle: { width: 1 },
        connectNulls: false,
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: vols,
        itemStyle: { color: 'rgba(100,160,200,0.55)' },
      },
    ],
  }
})
</script>

<template>
  <v-chart
    :option="option"
    :style="{ height: height ?? '340px', width: '100%' }"
    autoresize
  />
</template>
