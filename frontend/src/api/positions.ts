import client from './client'
import type {
  AccountSummary,
  FundFlow,
  PositionItem,
  TradeRecord,
} from '@/types/api'

// V1.5-G G-5：所有账户层端点的 account_id 由后端按 token 推
// （get_current_account_id），前端不再传 account_id。

export async function getAccount(): Promise<AccountSummary> {
  const res = await client.get('/api/v1/account')
  return res.data.data as AccountSummary
}

export async function syncAccount(): Promise<void> {
  await client.post('/api/v1/account/sync')
}

export async function getPositions(): Promise<PositionItem[]> {
  const res = await client.get('/api/v1/positions')
  return res.data.data as PositionItem[]
}

export async function patchPosition(
  id: number,
  body: { phase?: string },
): Promise<void> {
  await client.patch(`/api/v1/positions/${id}`, body)
}

export interface TradeBody {
  ts_code: string
  trade_type: 'BUY' | 'SELL'
  shares: number
  price: number
  trade_date: string
  commission?: number
  stamp_tax?: number
  note?: string
}

export async function recordTrade(body: TradeBody): Promise<TradeRecord> {
  const res = await client.post('/api/v1/account/trades', body)
  return res.data.data as TradeRecord
}

// 单账户个人场景成交/流水总量有限，一次性拉全（后端默认 limit=50 会截断分页）。
const LIST_FETCH_LIMIT = 5000

export async function getTrades(include_voided = false): Promise<TradeRecord[]> {
  const res = await client.get('/api/v1/account/trades', {
    params: { include_voided, limit: LIST_FETCH_LIMIT },
  })
  return (res.data.data?.items ?? []) as TradeRecord[]
}

export async function voidTrade(trade_id: number, void_note?: string): Promise<void> {
  await client.post(`/api/v1/account/trades/${trade_id}/void`, { void_note })
}

export async function voidCashflow(flow_id: number, void_note?: string): Promise<void> {
  await client.post(`/api/v1/account/cashflow/${flow_id}/void`, { void_note })
}

export interface DepositBody {
  amount: number
  trade_date: string
  note?: string
}

export async function deposit(body: DepositBody): Promise<void> {
  await client.post('/api/v1/account/deposit', body)
}

export async function withdraw(body: DepositBody): Promise<void> {
  await client.post('/api/v1/account/withdraw', body)
}

export interface CashflowParams {
  start_date?: string
  end_date?: string
  flow_type?: string
  limit?: number
  offset?: number
  include_voided?: boolean
}

export async function getCashflows(params?: CashflowParams): Promise<FundFlow[]> {
  const res = await client.get('/api/v1/account/cashflow', {
    params: { limit: LIST_FETCH_LIMIT, ...params },
  })
  return (res.data.data?.items ?? []) as FundFlow[]
}
