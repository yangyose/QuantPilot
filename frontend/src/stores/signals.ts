import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as signalApi from '@/api/signals'
import type { Signal, SignalLineage, SignalStatus } from '@/types/api'

export const useSignalStore = defineStore('signals', () => {
  const signals = ref<Signal[]>([])
  const history = ref<Signal[]>([])
  const loading = ref(false)
  const currentLineage = ref<SignalLineage | null>(null)

  async function fetchSignals(params?: signalApi.SignalListParams): Promise<void> {
    loading.value = true
    try {
      signals.value = await signalApi.getSignals(params)
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

  return { signals, history, loading, currentLineage, fetchSignals, fetchHistory, updateStatus, fetchLineage }
})
