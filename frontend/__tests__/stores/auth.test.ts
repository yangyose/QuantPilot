import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  refreshToken: vi.fn(),
  register: vi.fn(),
  getMe: vi.fn(),
  updateMe: vi.fn(),
}))

import * as authApi from '@/api/auth'

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('初始状态：token 为 null，isLoggedIn 为 false', () => {
    const store = useAuthStore()
    expect(store.token).toBeNull()
    expect(store.isLoggedIn).toBe(false)
  })

  it('login 成功后写入 token，isLoggedIn 变 true', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      access_token: 'test-access-token',
      token_type: 'bearer',
    })
    vi.mocked(authApi.getMe).mockResolvedValue({
      username: 'admin',
      email: 'admin@local.host',
      level: 'L3',
    })
    const store = useAuthStore()
    await store.login('admin', 'password')
    expect(store.token).toBe('test-access-token')
    expect(store.isLoggedIn).toBe(true)
    expect(localStorage.getItem('access_token')).toBe('test-access-token')
  })

  // ── V1.5-G G-5：/auth/me 用户资料 + level ──────────────────────────

  it('login 成功后自动拉取 /auth/me 存 level 与 email', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      access_token: 't',
      token_type: 'bearer',
    })
    vi.mocked(authApi.getMe).mockResolvedValue({
      username: 'alice',
      email: 'alice@example.com',
      level: 'L2',
    })
    const store = useAuthStore()
    await store.login('alice', 'password')
    expect(store.level).toBe('L2')
    expect(store.email).toBe('alice@example.com')
    expect(localStorage.getItem('user_level')).toBe('L2')
  })

  it('login 时 /auth/me 失败不阻塞登录，level 回落 L1', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      access_token: 't',
      token_type: 'bearer',
    })
    vi.mocked(authApi.getMe).mockRejectedValue(new Error('500'))
    const store = useAuthStore()
    await store.login('alice', 'password')
    expect(store.isLoggedIn).toBe(true)
    expect(store.level).toBe('L1')
  })

  it('fetchMe 刷新用户资料并持久化', async () => {
    vi.mocked(authApi.getMe).mockResolvedValue({
      username: 'bob',
      email: 'bob@example.com',
      level: 'L3',
    })
    const store = useAuthStore()
    await store.fetchMe()
    expect(store.username).toBe('bob')
    expect(store.level).toBe('L3')
    expect(localStorage.getItem('user_level')).toBe('L3')
  })

  it('updateLevel 调 PATCH /auth/me 并更新本地 level', async () => {
    vi.mocked(authApi.updateMe).mockResolvedValue({
      username: 'alice',
      email: 'alice@example.com',
      level: 'L3',
    })
    const store = useAuthStore()
    await store.updateLevel('L3')
    expect(authApi.updateMe).toHaveBeenCalledWith({ level: 'L3' })
    expect(store.level).toBe('L3')
    expect(localStorage.getItem('user_level')).toBe('L3')
  })

  it('levelNum 将 L1/L2/L3 映射为 1/2/3（非法值回落 1）', () => {
    const store = useAuthStore()
    expect(store.levelNum).toBe(1)
    store.level = 'L3'
    expect(store.levelNum).toBe(3)
  })

  it('login 失败时 token 保持 null', async () => {
    vi.mocked(authApi.login).mockRejectedValue(new Error('401'))
    const store = useAuthStore()
    await expect(store.login('admin', 'wrong')).rejects.toThrow()
    expect(store.token).toBeNull()
    expect(store.isLoggedIn).toBe(false)
  })

  it('logout 清空 token 和 localStorage（含 level/email）', () => {
    const store = useAuthStore()
    store.token = 'old-token'
    store.level = 'L3'
    localStorage.setItem('access_token', 'old-token')
    localStorage.setItem('user_level', 'L3')
    store.logout()
    expect(store.token).toBeNull()
    expect(store.isLoggedIn).toBe(false)
    expect(store.level).toBe('L1')
    expect(store.email).toBeNull()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('user_level')).toBeNull()
  })

  it('tryRefresh 成功时返回 true 并更新 token', async () => {
    vi.mocked(authApi.refreshToken).mockResolvedValue({
      access_token: 'new-access-token',
      token_type: 'bearer',
    })
    const store = useAuthStore()
    store.refreshTokenValue = 'valid-refresh-token'
    localStorage.setItem('refresh_token', 'valid-refresh-token')
    const result = await store.tryRefresh()
    expect(result).toBe(true)
    expect(store.token).toBe('new-access-token')
  })

  it('tryRefresh 无 refresh token 时返回 false', async () => {
    const store = useAuthStore()
    const result = await store.tryRefresh()
    expect(result).toBe(false)
    expect(authApi.refreshToken).not.toHaveBeenCalled()
  })

  it('tryRefresh 失败时返回 false', async () => {
    vi.mocked(authApi.refreshToken).mockRejectedValue(new Error('expired'))
    const store = useAuthStore()
    store.refreshTokenValue = 'expired-refresh'
    localStorage.setItem('refresh_token', 'expired-refresh')
    const result = await store.tryRefresh()
    expect(result).toBe(false)
  })
})
