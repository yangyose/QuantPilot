import client from './client'
import type { KlineBar, MarketStateItem, MarketStateResponse, PoolStock } from '@/types/api'

export async function getMarketState(): Promise<MarketStateResponse> {
  const res = await client.get('/api/v1/market/state')
  return res.data.data as MarketStateResponse
}

/**
 * 查询历史市场状态。
 * 后端要求 start / end（必填），响应结构为 {items: MarketStateItem[], total: N}。
 */
export async function getMarketStateHistory(
  start: string,
  end: string,
): Promise<MarketStateItem[]> {
  const res = await client.get('/api/v1/market/state/history', { params: { start, end } })
  return (res.data.data?.items ?? []) as MarketStateItem[]
}

export async function getMarketPool(): Promise<PoolStock[]> {
  const res = await client.get('/api/v1/market/pool')
  return res.data.data as PoolStock[]
}

export async function getKline(ts_code: string, days = 60): Promise<KlineBar[]> {
  const res = await client.get(`/api/v1/market/stock/${encodeURIComponent(ts_code)}/kline`, {
    params: { days },
  })
  return (res.data.data?.bars ?? []) as KlineBar[]
}
