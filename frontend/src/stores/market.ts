import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as marketApi from '@/api/market'
import type { MarketStateItem, MarketStateResponse } from '@/types/api'

export const useMarketStore = defineStore('market', () => {
  const currentState = ref<MarketStateResponse | null>(null)
  const stateHistory = ref<MarketStateItem[]>([])

  async function fetchCurrentState(): Promise<void> {
    currentState.value = await marketApi.getMarketState()
  }

  async function fetchStateHistory(start: string, end: string): Promise<void> {
    stateHistory.value = await marketApi.getMarketStateHistory(start, end)
  }

  return { currentState, stateHistory, fetchCurrentState, fetchStateHistory }
})
