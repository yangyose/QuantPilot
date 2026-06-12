import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as signalApi from '@/api/signals'
import type { Signal, SignalLineage, SignalStatus } from '@/types/api'

export const useSignalStore = defineStore('signals', () => {
  const signals = ref<Signal[]>([])
  const signalDate = ref<string | null>(null)
  const history = ref<Signal[]>([])
  const loading = ref(false)
  const currentLineage = ref<SignalLineage | null>(null)

  async function fetchSignals(params?: signalApi.SignalListParams): Promise<void> {
    loading.value = true
    try {
      const res = await signalApi.getSignals(params)
      signals.value = res.signals
      signalDate.value = res.tradeDate
    } finally {
      loading.value = false
    }
  }

  async function fetchHistory(params?: signalApi.SignalHistoryParams): Promise<void> {
    loading.value = true
    try {
      history.value = await signalApi.getSignalHistory(params)
    } finally {
      loading.value = false
    }
  }

  async function updateStatus(id: number, status: SignalStatus): Promise<void> {
    await signalApi.patchSignalStatus(id, status)
    // 同步更新本地状态
    const signal = signals.value.find((s) => s.id === id)
    if (signal) signal.status = status
  }

  async function fetchLineage(id: number): Promise<void> {
    currentLineage.value = await signalApi.getSignalLineage(id)
  }

  return { signals, signalDate, history, loading, currentLineage, fetchSignals, fetchHistory, updateStatus, fetchLineage }
})
