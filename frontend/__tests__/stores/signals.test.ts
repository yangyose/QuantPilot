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
  // Phase 11 §9.1：分位主路径三层输出 + trigger_reason
  composite_z: null,
  composite_pct_in_market: null,
  trigger_reason: null,
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

  it('fetchSignals 成功后写入 signals + signalDate 并重置 loading', async () => {
    vi.mocked(signalApi.getSignals).mockResolvedValue({
      signals: [mockSignal],
      tradeDate: '2026-04-15',
    })
    const store = useSignalStore()
    await store.fetchSignals()
    expect(store.signals).toEqual([mockSignal])
    expect(store.signalDate).toBe('2026-04-15')
    expect(store.loading).toBe(false)
  })

  it('fetchSignals 过程中 loading 为 true，完成后恢复 false', async () => {
    let resolveSignals!: (v: signalApi.SignalListResult) => void
    vi.mocked(signalApi.getSignals).mockReturnValue(
      new Promise<signalApi.SignalListResult>((res) => { resolveSignals = res }),
    )
    const store = useSignalStore()
    const promise = store.fetchSignals()
    expect(store.loading).toBe(true)
    resolveSignals({ signals: [mockSignal], tradeDate: '2026-04-15' })
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
      score_snapshot: {
        ts_code: '000001.SZ',
        composite_score: 85,
        composite_z: null,
        composite_pct_in_market: null,
        market_state: 'UPTREND',
        trigger_reason: null,
        trend_score: null,
        momentum_score: null,
        reversion_score: null,
        value_score: null,
        weights_source: null,
        hysteresis_status: null,
        score_breakdown: null,
        factor_winsorized: null,
        factor_neutralized: null,
        raw_factors: null,
        factor_orthogonal: null,
        score_breakdown_raw: null,
        score_breakdown_residual: null,
      },
      pipeline_run: {
        trade_date: '2026-04-15',
        cp1_at: null,
        cp2_at: null,
        cp3_at: null,
        data_snapshot_version: null,
      },
    }
    vi.mocked(signalApi.getSignalLineage).mockResolvedValue(mockLineage)
    const store = useSignalStore()
    await store.fetchLineage(1)
    expect(store.currentLineage).toEqual(mockLineage)
    expect(signalApi.getSignalLineage).toHaveBeenCalledWith(1)
  })
})
