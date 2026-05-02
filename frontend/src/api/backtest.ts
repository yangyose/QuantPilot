import client from './client'
import type {
  BacktestResultRaw,
  BacktestRunRequest,
  BacktestRunResponse,
  BacktestStatusResponse,
} from '@/types/api'

export async function submitBacktest(body: BacktestRunRequest): Promise<BacktestRunResponse> {
  const res = await client.post('/api/v1/backtest/run', body)
  return res.data.data as BacktestRunResponse
}

export async function getBacktestStatus(taskId: string): Promise<BacktestStatusResponse> {
  const res = await client.get(`/api/v1/backtest/${taskId}/status`)
  return res.data.data as BacktestStatusResponse
}

export async function getBacktestResult(taskId: string): Promise<BacktestResultRaw> {
  const res = await client.get(`/api/v1/backtest/${taskId}/result`)
  return res.data.data as BacktestResultRaw
}
