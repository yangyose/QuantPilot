import client from './client'
import type { WatchlistItem, WatchlistType } from '@/types/api'

export async function listWatchlist(
  listType?: WatchlistType,
): Promise<WatchlistItem[]> {
  const res = await client.get('/api/v1/watchlist', {
    params: listType ? { list_type: listType } : {},
  })
  return (res.data.data?.items ?? res.data.data) as WatchlistItem[]
}

export async function addToWatchlist(
  ts_code: string,
  list_type: WatchlistType,
  reason?: string,
): Promise<WatchlistItem> {
  const res = await client.post('/api/v1/watchlist', { ts_code, list_type, reason })
  return res.data.data as WatchlistItem
}

export async function removeFromWatchlist(
  ts_code: string,
  list_type: WatchlistType,
): Promise<void> {
  await client.delete(`/api/v1/watchlist/${ts_code}`, {
    params: { list_type },
  })
}
