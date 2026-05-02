import client from './client'
import type {
  NotificationListData,
  UnreadCountData,
  WxStatusData,
} from '@/types/api'

export async function listNotifications(
  params: {
    notify_type?: string
    unread_only?: boolean
    limit?: number
    offset?: number
  } = {},
): Promise<NotificationListData> {
  const res = await client.get('/api/v1/notifications', { params })
  return res.data.data as NotificationListData
}

export async function getUnreadCount(): Promise<number> {
  const res = await client.get('/api/v1/notifications/unread-count')
  return (res.data.data as UnreadCountData).unread
}

export async function getWxStatus(): Promise<WxStatusData> {
  const res = await client.get('/api/v1/notifications/wx-status')
  return res.data.data as WxStatusData
}

export async function markNotificationRead(id: number): Promise<void> {
  await client.post(`/api/v1/notifications/${id}/read`)
}

export async function markAllRead(): Promise<number> {
  const res = await client.post('/api/v1/notifications/read-all')
  return (res.data.data?.updated ?? 0) as number
}
