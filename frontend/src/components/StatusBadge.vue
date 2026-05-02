<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string }>()

const CONFIG: Record<string, { color: string; text: string }> = {
  UPTREND:     { color: 'green',   text: '上涨趋势' },
  OSCILLATION: { color: 'blue',    text: '震荡市' },
  DOWNTREND:   { color: 'red',     text: '下跌趋势' },
  PENDING:     { color: 'default', text: '等待中' },
  RUNNING:     { color: 'processing', text: '运行中' },
  SUCCESS:     { color: 'green',   text: '成功' },
  FAILED:      { color: 'error',   text: '失败' },
  NEW:         { color: 'blue',    text: '新信号' },
  VIEWED:      { color: 'default', text: '已查看' },
  ACTED:       { color: 'green',   text: '已操作' },
  EXPIRED:     { color: 'default', text: '已过期' },
  SUPERSEDED:  { color: 'warning', text: '已替代' },
}

const cfg = computed(() => CONFIG[props.status] ?? { color: 'default', text: props.status })
</script>

<template>
  <a-tag :color="cfg.color">{{ cfg.text }}</a-tag>
</template>
