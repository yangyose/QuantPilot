import client from './client'
import type { LoginResponse, UserLevel, UserMe } from '@/types/api'

export async function login(username: string, password: string): Promise<LoginResponse> {
  const res = await client.post('/api/v1/auth/login', { username, password })
  return res.data.data as LoginResponse
}

export async function refreshToken(token: string): Promise<LoginResponse> {
  const res = await client.post('/api/v1/auth/refresh', { refresh_token: token })
  return res.data.data as LoginResponse
}

// ── V1.5-G G-5 ──────────────────────────────────────────────────────

/** 注册（§4.4：注册成功不签发 token，前端跳登录页手动登录）。 */
export async function register(
  username: string,
  email: string,
  password: string,
): Promise<UserMe> {
  const res = await client.post('/api/v1/auth/register', { username, email, password })
  return res.data.data as UserMe
}

export async function getMe(): Promise<UserMe> {
  const res = await client.get('/api/v1/auth/me')
  return res.data.data as UserMe
}

export interface UpdateMeBody {
  level?: UserLevel
  email?: string
  password?: string
}

export async function updateMe(body: UpdateMeBody): Promise<UserMe> {
  const res = await client.patch('/api/v1/auth/me', body)
  return res.data.data as UserMe
}
