<script setup lang="ts">
/**
 * Phase 13 §3.7.4：日度流水线实时进度卡片。
 *
 * 订阅 WS /api/v1/pipeline/progress；后端 Redis 未配置时显示降级提示，
 * 不影响其他卡片渲染。组件卸载时自动断开 WS（防内存泄漏）。
 */
import { onMounted, onUnmounted, ref, computed } from 'vue'
import { WebSocketClient } from '@/api/websocket'

interface PipelineProgress {
  trade_date: string
  step: string
  status: string
  progress_pct: number
}

const progress = ref<PipelineProgress | null>(null)
const wsError = ref<string | null>(null)
let wsClient: WebSocketClient | null = null

const stepLabel = computed(() => {
  if (!progress.value) return ''
  const step = progress.value.step
  const map: Record<string, string> = {
    pipeline: '流水线',
    CP1: 'CP1 数据采集',
    CP2: 'CP2 全市场评分',
    CP3: 'CP3 信号生成',
    Step4: '盯市同步',
    Step5: '自动分红',
    Step6: '过期信号扫描',
  }
  return map[step] ?? step
})

const statusColor = computed(() => {
  const s = progress.value?.status ?? ''
  if (s === 'completed') return '#52c41a'
  if (s === 'failed') return '#f5222d'
  if (s === 'started') return '#1890ff'
  return '#999'
})

onMounted(() => {
  wsClient = new WebSocketClient('/api/v1/pipeline/progress')
  wsClient.onMessage((data) => {
    // Phase 14 §14-7 R13-P2-5：WS error 帧统一为 REST 格式 {code, data, msg}；
    // 旧 {error: string} 兜底保留兼容（虽然后端已不再发送旧 schema）。
    const d = data as Partial<PipelineProgress> & {
      error?: string
      code?: number
      msg?: string
    }
    if (typeof d.code === 'number' && d.code !== 0) {
      wsError.value = d.msg ?? `推送异常 code=${d.code}`
      return
    }
    if (d.error) {
      wsError.value = d.error
      return
    }
    progress.value = d as PipelineProgress
  })
  wsClient.onError(() => {
    wsError.value = 'WebSocket 连接异常'
  })
  wsClient.connect()
})

onUnmounted(() => {
  wsClient?.close()
  wsClient = null
})
</script>

<template>
  <a-card size="small" title="日度流水线进度" style="margin-bottom: 16px">
    <div v-if="wsError" style="color: rgba(0,0,0,.45); font-size: 12px">
      {{ wsError }}（推送不可用，不影响数据查询）
    </div>
    <div v-else-if="progress">
      <div style="margin-bottom: 8px">
        <span style="font-weight: 600">{{ stepLabel }}</span>
        <span :style="{ color: statusColor, marginLeft: '12px' }">
          {{ progress.status }}
        </span>
        <span style="margin-left: 12px; color: rgba(0,0,0,.45); font-size: 12px">
          {{ progress.trade_date }}
        </span>
      </div>
      <a-progress
        :percent="progress.progress_pct"
        :status="progress.status === 'failed' ? 'exception' : 'active'"
        :stroke-color="statusColor"
      />
    </div>
    <div v-else style="color: rgba(0,0,0,.45); font-size: 12px">
      等待流水线开始 …
    </div>
  </a-card>
</template>
