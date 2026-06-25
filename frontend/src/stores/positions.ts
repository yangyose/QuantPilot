import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as posApi from '@/api/positions'
import type { AccountSummary, FundFlow, PositionItem, TradeRecord } from '@/types/api'

export const usePositionStore = defineStore('positions', () => {
  const account = ref<AccountSummary | null>(null)
  const positions = ref<PositionItem[]>([])
  const cashflows = ref<FundFlow[]>([])
  const trades = ref<TradeRecord[]>([])
  const loading = ref(false)

  async function fetchAccount(): Promise<void> {
    account.value = await posApi.getAccount()
  }

  async function fetchPositions(): Promise<void> {
    loading.value = true
    try {
      positions.value = await posApi.getPositions()
    } finally {
      loading.value = false
    }
  }

  async function fetchCashflows(params?: posApi.CashflowParams): Promise<void> {
    loading.value = true
    try {
      cashflows.value = await posApi.getCashflows(params)
    } finally {
      loading.value = false
    }
  }

  async function fetchTrades(includeVoided = false): Promise<void> {
    loading.value = true
    try {
      trades.value = await posApi.getTrades(account.value?.id ?? 1, includeVoided)
    } finally {
      loading.value = false
    }
  }

  async function syncAccount(): Promise<void> {
    await posApi.syncAccount()
    await fetchAccount()
    await fetchPositions()
  }

  async function recordTrade(body: posApi.TradeBody): Promise<TradeRecord> {
    const record = await posApi.recordTrade(body)
    await fetchAccount()
    await fetchPositions()
    await fetchTrades()
    return record
  }

  async function voidTrade(tradeId: number, voidNote?: string): Promise<void> {
    await posApi.voidTrade(tradeId, voidNote)
    await fetchAccount()
    await fetchPositions()
    await fetchTrades()
    await fetchCashflows()
  }

  async function voidCashflow(flowId: number, voidNote?: string): Promise<void> {
    await posApi.voidCashflow(flowId, voidNote)
    await fetchAccount()
    await fetchPositions()
    await fetchCashflows()
  }

  async function deposit(body: posApi.DepositBody): Promise<void> {
    await posApi.deposit(body)
    await fetchAccount()
    await fetchCashflows()
  }

  async function withdraw(body: posApi.DepositBody): Promise<void> {
    await posApi.withdraw(body)
    await fetchAccount()
    await fetchCashflows()
  }

  return {
    account, positions, cashflows, trades, loading,
    fetchAccount, fetchPositions, fetchCashflows, fetchTrades,
    syncAccount, recordTrade, deposit, withdraw,
    voidTrade, voidCashflow,
  }
})
