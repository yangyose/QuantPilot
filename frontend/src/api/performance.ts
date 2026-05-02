import client from './client'
import type {
  Attribution,
  BehavioralAnalysis,
  PerformanceHistory,
  PerformanceSummary,
} from '@/types/api'

export async function getPerformanceSummary(): Promise<PerformanceSummary | null> {
  const res = await client.get('/api/v1/performance/summary')
  return res.data.data as PerformanceSummary | null
}

export async function getPerformanceHistory(limit = 252): Promise<PerformanceHistory> {
  const res = await client.get('/api/v1/performance/history', { params: { limit } })
  return res.data.data as PerformanceHistory
}

export async function getAttribution(
  period_start: string,
  period_end: string,
): Promise<Attribution> {
  const res = await client.get('/api/v1/performance/attribution', {
    params: { period_start, period_end },
  })
  return res.data.data as Attribution
}

export async function getBehavioralAnalysis(): Promise<BehavioralAnalysis> {
  const res = await client.get('/api/v1/performance/behavior')
  return res.data.data as BehavioralAnalysis
}
