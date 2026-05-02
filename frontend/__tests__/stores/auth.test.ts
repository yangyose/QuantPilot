import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  refreshToken: vi.fn(),
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
    const store = useAuthStore()
    await store.login('admin', 'password')
    expect(store.token).toBe('test-access-token')
    expect(store.isLoggedIn).toBe(true)
    expect(localStorage.getItem('access_token')).toBe('test-access-token')
  })

  it('login 失败时 token 保持 null', async () => {
    vi.mocked(authApi.login).mockRejectedValue(new Error('401'))
    const store = useAuthStore()
    await expect(store.login('admin', 'wrong')).rejects.toThrow()
    expect(store.token).toBeNull()
    expect(store.isLoggedIn).toBe(false)
  })

  it('logout 清空 token 和 localStorage', () => {
    const store = useAuthStore()
    store.token = 'old-token'
    localStorage.setItem('access_token', 'old-token')
    store.logout()
    expect(store.token).toBeNull()
    expect(store.isLoggedIn).toBe(false)
    expect(localStorage.getItem('access_token')).toBeNull()
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
