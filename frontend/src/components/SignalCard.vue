<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from './StatusBadge.vue'
import { fmtPct } from '@/utils/format'
import type { Signal } from '@/types/api'

const props = defineProps<{ signal: Signal }>()
const emit = defineEmits<{
  (e: 'click', signal: Signal): void
}>()

const isBuy = computed(() => props.signal.signal_type === 'BUY')
const tagColor = computed(() => (isBuy.value ? '#52c41a' : '#ff4d4f'))
</script>

<template>
  <a-card
    hoverable
    size="small"
    style="margin-bottom: 8px; cursor: pointer"
    @click="emit('click', signal)"
  >
    <div style="display: flex; justify-content: space-between; align-items: center">
      <div>
        <a-tag :color="tagColor">{{ isBuy ? '买入' : '卖出' }}</a-tag>
        <span style="font-weight: 600; margin-right: 4px">{{ signal.ts_code }}</span>
        <span v-if="signal.name" style="color: rgba(0,0,0,.65); margin-right: 8px; font-size: 13px">{{ signal.name }}</span>
        <a-tag v-if="signal.signal_strength" color="purple">{{ signal.signal_strength }}</a-tag>
      </div>
      <StatusBadge :status="signal.status" />
    </div>
    <div style="margin-top: 4px; color: rgba(0,0,0,.65); font-size: 13px">
      评分 <b>{{ signal.score ?? 'N/A' }}</b> ·
      建议仓位 <b>{{ fmtPct(signal.suggested_pct) }}</b> ·
      {{ signal.trade_date }}
    </div>
    <div v-if="signal.t1_warning" style="margin-top: 4px; color: #faad14; font-size: 12px">
      ⚠️ {{ signal.t1_warning }}
    </div>
  </a-card>
</template>
