import client from './client'
import type { LoginResponse } from '@/types/api'

export async function login(username: string, password: string): Promise<LoginResponse> {
  const res = await client.post('/api/v1/auth/login', { username, password })
  return res.data.data as LoginResponse
}

export async function refreshToken(token: string): Promise<LoginResponse> {
  const res = await client.post('/api/v1/auth/refresh', { refresh_token: token })
  return res.data.data as LoginResponse
}
