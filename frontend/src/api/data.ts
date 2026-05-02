import client from './client'
import type {
  DataStatus,
  IngestDailyResult,
  IngestHistoryResult,
  RefreshStockListResult,
} from '@/types/api'

export async function getDataStatus(): Promise<DataStatus> {
  const res = await client.get('/api/v1/data/status')
  return res.data.data as DataStatus
}

export async function ingestDaily(tradeDate?: string): Promise<IngestDailyResult> {
  const res = await client.post('/api/v1/data/ingest/daily', {
    trade_date: tradeDate ?? null,
  })
  return res.data.data as IngestDailyResult
}

export async function ingestHistory(
  startDate: string,
  endDate: string,
): Promise<IngestHistoryResult> {
  const res = await client.post('/api/v1/data/ingest/history', {
    start_date: startDate,
    end_date: endDate,
  })
  return res.data.data as IngestHistoryResult
}

export async function refreshStockList(): Promise<RefreshStockListResult> {
  const res = await client.post('/api/v1/data/refresh/stock-list')
  return res.data.data as RefreshStockListResult
}
