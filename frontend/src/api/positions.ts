import client from './client'
import type {
  AccountSummary,
  FundFlow,
  PositionItem,
  TradeRecord,
} from '@/types/api'

export async function getAccount(): Promise<AccountSummary> {
  const res = await client.get('/api/v1/account')
  return res.data.data as AccountSummary
}

export async function syncAccount(account_id = 1): Promise<void> {
  await client.post('/api/v1/account/sync', null, { params: { account_id } })
}

export async function getPositions(account_id = 1): Promise<PositionItem[]> {
  const res = await client.get('/api/v1/positions', { params: { account_id } })
  return res.data.data as PositionItem[]
}

export interface AddPositionBody {
  account_id: number
  ts_code: string
  shares: number
  cost_price?: number
  open_date?: string
  phase?: string
}

export async function addPosition(body: AddPositionBody): Promise<PositionItem> {
  const res = await client.post('/api/v1/positions', body)
  return res.data.data as PositionItem
}

export async function patchPosition(
  id: number,
  body: { phase?: string },
): Promise<void> {
  await client.patch(`/api/v1/positions/${id}`, body)
}

export interface TradeBody {
  account_id: number
  ts_code: string
  trade_type: 'BUY' | 'SELL'
  shares: number
  price: number
  trade_date: string
  commission?: number
  note?: string
}

export async function recordTrade(body: TradeBody): Promise<TradeRecord> {
  const res = await client.post('/api/v1/account/trades', body)
  return res.data.data as TradeRecord
}

export async function getTrades(
  account_id = 1,
  include_voided = false,
): Promise<TradeRecord[]> {
  const res = await client.get('/api/v1/account/trades', {
    params: { account_id, include_voided },
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
  account_id: number
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
  account_id?: number
  start_date?: string
  end_date?: string
  flow_type?: string
  limit?: number
  offset?: number
  include_voided?: boolean
}

export async function getCashflows(params?: CashflowParams): Promise<FundFlow[]> {
  const res = await client.get('/api/v1/account/cashflow', {
    params: { account_id: 1, ...params },
  })
  return (res.data.data?.items ?? []) as FundFlow[]
}
