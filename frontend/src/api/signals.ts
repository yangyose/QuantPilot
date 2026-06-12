import client from './client'
import type { Signal, SignalLineage, SignalStatus } from '@/types/api'

export interface SignalListParams {
  trade_date?: string
  signal_type?: string
  status?: string
  limit?: number
  offset?: number
}

export interface SignalHistoryParams {
  ts_code?: string
  signal_type?: string
  status?: string
  limit?: number
  offset?: number
}

export interface SignalListResult {
  signals: Signal[]
  /** 实际信号日期（缺省查询时为最新有信号的交易日；无任何信号时为 null） */
  tradeDate: string | null
}

/** GET /signals 响应：{trade_date, signals:[...], total} */
export async function getSignals(params?: SignalListParams): Promise<SignalListResult> {
  const res = await client.get('/api/v1/signals', { params })
  return {
    signals: (res.data.data?.signals ?? []) as Signal[],
    tradeDate: (res.data.data?.trade_date ?? null) as string | null,
  }
}

/** GET /signals/history 响应：{signals:[...], limit, offset} */
export async function getSignalHistory(params?: SignalHistoryParams): Promise<Signal[]> {
  const res = await client.get('/api/v1/signals/history', { params })
  return (res.data.data?.signals ?? []) as Signal[]
}

export async function patchSignalStatus(id: number, status: SignalStatus): Promise<void> {
  await client.patch(`/api/v1/signals/${id}/status`, { status })
}

export async function getSignalLineage(id: number): Promise<SignalLineage> {
  const res = await client.get(`/api/v1/signals/${id}/lineage`)
  return res.data.data as SignalLineage
}
