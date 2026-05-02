import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSignalStore } from '@/stores/signals'
import type { Signal, SignalLineage } from '@/types/api'

vi.mock('@/api/signals', () => ({
  getSignals: vi.fn(),
  getSignalHistory: vi.fn(),
  patchSignalStatus: vi.fn(),
  getSignalLineage: vi.fn(),
}))

import * as signalApi from '@/api/signals'

const mockSignal: Signal = {
  id: 1,
  ts_code: '000001.SZ',
  name: null,
  signal_type: 'BUY',
  trade_date: '2026-04-15',
  score: 85,
  suggested_pct: 0.1,
  suggested_price_low: null,
  suggested_price_high: null,
  stop_loss_price: null,
  signal_strength: 'MODERATE',
  status: 'NEW',
  t1_warning: 'A股T+1',
  liquidity_note: null,
  reason: null,
  created_at: null,
}

describe('useSignalStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('初始状态：signals 为空，loading 为 false，currentLineage 为 null', () => {
    const store = useSignalStore()
    expect(store.signals).toEqual([])
    expect(store.history).toEqual([])
    expect(store.loading).toBe(false)
    expect(store.currentLineage).toBeNull()
  })

  it('fetchSignals 成功后写入 signals 并重置 loading', async () => {
    vi.mocked(signalApi.getSignals).mockResolvedValue([mockSignal])
    const store = useSignalStore()
    await store.fetchSignals()
    expect(store.signals).toEqual([mockSignal])
    expect(store.loading).toBe(false)
  })

  it('fetchSignals 过程中 loading 为 true，完成后恢复 false', async () => {
    let resolveSignals!: (v: Signal[]) => void
    vi.mocked(signalApi.getSignals).mockReturnValue(
      new Promise<Signal[]>((res) => { resolveSignals = res }),
    )
    const store = useSignalStore()
    const promise = store.fetchSignals()
    expect(store.loading).toBe(true)
    resolveSignals([mockSignal])
    await promise
    expect(store.loading).toBe(false)
  })

  it('updateStatus 调用 API 并传入正确参数', async () => {
    vi.mocked(signalApi.patchSignalStatus).mockResolvedValue(undefined)
    const store = useSignalStore()
    await store.updateStatus(1, 'VIEWED')
    expect(signalApi.patchSignalStatus).toHaveBeenCalledWith(1, 'VIEWED')
  })

  it('fetchLineage 将结果写入 currentLineage', async () => {
    const mockLineage: SignalLineage = {
      signal_id: 1,
      trade_date: '2026-04-15',
      score_snapshot: { composite: 85 },
      pipeline_run: { id: 42 },
    }
    vi.mocked(signalApi.getSignalLineage).mockResolvedValue(mockLineage)
    const store = useSignalStore()
    await store.fetchLineage(1)
    expect(store.currentLineage).toEqual(mockLineage)
    expect(signalApi.getSignalLineage).toHaveBeenCalledWith(1)
  })
})
