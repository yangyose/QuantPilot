import client from './client'
import type { SetupStatusData } from '@/types/api'

export async function getSetupStatus(): Promise<SetupStatusData> {
  const res = await client.get('/api/v1/setup/status')
  return res.data.data as SetupStatusData
}

export async function completeSetup(): Promise<SetupStatusData> {
  const res = await client.post('/api/v1/setup/complete')
  return res.data.data as SetupStatusData
}
