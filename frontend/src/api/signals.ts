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
  /**
   * 满仓提示：存在买入推荐但**全部不可执行**时由后端给出，否则 null。
   * 后端只陈述事实（可用资金/总资产/条数），不断言是哪个约束绑住的——
   * 前端照原样显示即可，**不要在这里重算可执行性**（那会复制一份仓位判定逻辑）。
   */
  fundingNote: string | null
}

/** GET /signals 响应：{trade_date, signals:[...], total} */
export async function getSignals(params?: SignalListParams): Promise<SignalListResult> {
  const res = await client.get('/api/v1/signals', { params })
  return {
    signals: (res.data.data?.signals ?? []) as Signal[],
    tradeDate: (res.data.data?.trade_date ?? null) as string | null,
    fundingNote: (res.data.data?.funding_note ?? null) as string | null,
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
