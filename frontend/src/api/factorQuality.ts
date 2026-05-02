import client from './client'
import type { FactorQualityItem } from '@/types/api'

/** GET /factor-quality → {items: FactorQualityItem[]} */
export async function getFactorQuality(strategy_name?: string): Promise<FactorQualityItem[]> {
  const res = await client.get('/api/v1/factor-quality', { params: { strategy_name } })
  return (res.data.data?.items ?? []) as FactorQualityItem[]
}

/** GET /factor-quality/history → {items: FactorQualityItem[], total: N} */
export async function getFactorQualityHistory(
  factor_name?: string,
  limit = 24,
): Promise<FactorQualityItem[]> {
  const res = await client.get('/api/v1/factor-quality/history', {
    params: { factor_name, limit },
  })
  return (res.data.data?.items ?? []) as FactorQualityItem[]
}
