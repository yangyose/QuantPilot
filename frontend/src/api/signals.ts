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

/** GET /signals 响应：{trade_date, signals:[...], total} */
export async function getSignals(params?: SignalListParams): Promise<Signal[]> {
  const res = await client.get('/api/v1/signals', { params })
  return (res.data.data?.signals ?? []) as Signal[]
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
