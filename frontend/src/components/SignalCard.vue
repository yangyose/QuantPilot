<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import StatusBadge from './StatusBadge.vue'
import { fmtPct } from '@/utils/format'
import { translateTriggerReason } from '@/utils/lineage'
import type { Signal } from '@/types/api'

const props = defineProps<{ signal: Signal }>()
const emit = defineEmits<{
  (e: 'click', signal: Signal): void
}>()

const router = useRouter()

const isBuy = computed(() => props.signal.signal_type === 'BUY')
const tagColor = computed(() => (isBuy.value ? '#52c41a' : '#ff4d4f'))

// Phase 12 §3.3.1：L1 业务可解释一行文本（trigger_reason 翻译）
const reasonText = computed(() => {
  const t = props.signal.trigger_reason
  if (!t) return null
  return translateTriggerReason(t)
})

function goLineage(e: Event): void {
  e.stopPropagation()
  router.push(`/signals/${props.signal.id}/lineage`)
}
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
    <!-- Phase 12 §3.3.1：L1 业务可解释 -->
    <div v-if="reasonText" style="margin-top: 4px; color: #1677ff; font-size: 12px">
      💡 {{ reasonText }}
    </div>
    <div v-if="signal.t1_warning" style="margin-top: 4px; color: #faad14; font-size: 12px">
      ⚠️ {{ signal.t1_warning }}
    </div>
    <!-- Phase 12 §3.3.1：跳转评分溯源页 -->
    <div style="margin-top: 6px; text-align: right">
      <a-button type="link" size="small" @click="goLineage">
        查看评分溯源 →
      </a-button>
    </div>
  </a-card>
</template>
