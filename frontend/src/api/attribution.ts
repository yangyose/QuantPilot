import client from './client'
import type {
  AttributionHistoryData,
  AttributionSummaryData,
} from '@/types/api'

export interface AttributionHistoryParams {
  start_date: string
  end_date: string
  factor?: string
}

export interface AttributionSummaryParams {
  start_date: string
  end_date: string
}

/** GET /attribution/history → AttributionHistoryData */
export async function getAttributionHistory(
  params: AttributionHistoryParams,
): Promise<AttributionHistoryData> {
  const res = await client.get('/api/v1/attribution/history', { params })
  return res.data.data as AttributionHistoryData
}

/** GET /attribution/summary → AttributionSummaryData */
export async function getAttributionSummary(
  params: AttributionSummaryParams,
): Promise<AttributionSummaryData> {
  const res = await client.get('/api/v1/attribution/summary', { params })
  return res.data.data as AttributionSummaryData
}
