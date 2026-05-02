import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as posApi from '@/api/positions'
import type { AccountSummary, FundFlow, PositionItem, TradeRecord } from '@/types/api'

export const usePositionStore = defineStore('positions', () => {
  const account = ref<AccountSummary | null>(null)
  const positions = ref<PositionItem[]>([])
  const cashflows = ref<FundFlow[]>([])
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
    cashflows.value = await posApi.getCashflows(params)
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
    return record
  }

  async function addPosition(body: posApi.AddPositionBody): Promise<void> {
    await posApi.addPosition(body)
    await fetchPositions()
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
    account, positions, cashflows, loading,
    fetchAccount, fetchPositions, fetchCashflows,
    syncAccount, recordTrade, addPosition, deposit, withdraw,
  }
})
