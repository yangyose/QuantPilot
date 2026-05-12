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
  // 同步阻塞执行：60 天回填经验值 ~6-8 分钟。client 全局 15s 超时不适用，
  // 这里单点覆盖到 15 分钟。Phase 11 改异步后改回 client 全局值
  const res = await client.post(
    '/api/v1/data/ingest/history',
    { start_date: startDate, end_date: endDate },
    { timeout: 900_000 },
  )
  return res.data.data as IngestHistoryResult
}

export async function refreshStockList(): Promise<RefreshStockListResult> {
  // 刷新全 A 股 stock_info 含一次 Tushare full pull，~30-60s
  const res = await client.post('/api/v1/data/refresh/stock-list', null, {
    timeout: 120_000,
  })
  return res.data.data as RefreshStockListResult
}

export async function ingestDailyForced(tradeDate: string): Promise<IngestDailyResult> {
  // 单日补拉的长超时版本：5 分钟足够单日全量
  const res = await client.post(
    '/api/v1/data/ingest/daily',
    { trade_date: tradeDate },
    { timeout: 300_000 },
  )
  return res.data.data as IngestDailyResult
}
