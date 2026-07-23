import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as authApi from '@/api/auth'
import type { UserLevel } from '@/types/api'

const LEVEL_ORDER: Record<string, number> = { L1: 1, L2: 2, L3: 3 }

function readStoredLevel(): UserLevel {
  const v = localStorage.getItem('user_level')
  return v === 'L2' || v === 'L3' ? v : 'L1'
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem('access_token'))
  const refreshTokenValue = ref<string | null>(localStorage.getItem('refresh_token'))
  const username = ref<string | null>(localStorage.getItem('username'))
  // V1.5-G G-5：/auth/me 用户资料。level 驱动分层显隐（偏好非权限），
  // 持久化到 localStorage 使刷新后无需等待 /auth/me 即可渲染。
  const email = ref<string | null>(localStorage.getItem('user_email'))
  const level = ref<UserLevel>(readStoredLevel())

  const isLoggedIn = computed(() => token.value !== null)
  /** L1/L2/L3 → 1/2/3（非法回落 1），用于 `levelNum >= n` 显隐比较。 */
  const levelNum = computed(() => LEVEL_ORDER[level.value] ?? 1)

  function applyMe(me: { username: string; email: string; level: string }): void {
    username.value = me.username
    email.value = me.email
    level.value = (me.level === 'L2' || me.level === 'L3' ? me.level : 'L1') as UserLevel
    localStorage.setItem('username', me.username)
    localStorage.setItem('user_email', me.email)
    localStorage.setItem('user_level', level.value)
  }

  async function fetchMe(): Promise<void> {
    applyMe(await authApi.getMe())
  }

  async function updateLevel(newLevel: UserLevel): Promise<void> {
    applyMe(await authApi.updateMe({ level: newLevel }))
  }

  async function login(loginUsername: string, password: string): Promise<void> {
    const data = await authApi.login(loginUsername, password)
    token.value = data.access_token
    username.value = loginUsername
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('username', loginUsername)
    if (data.refresh_token) {
      refreshTokenValue.value = data.refresh_token
      localStorage.setItem('refresh_token', data.refresh_token)
    }
    // 拉取用户资料（level 驱动分层显隐）；失败不阻塞登录，level 回落 L1
    try {
      await fetchMe()
    } catch {
      level.value = 'L1'
    }
  }

  function logout(): void {
    token.value = null
    refreshTokenValue.value = null
    username.value = null
    email.value = null
    level.value = 'L1'
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('username')
    localStorage.removeItem('user_email')
    localStorage.removeItem('user_level')
  }

  async function tryRefresh(): Promise<boolean> {
    const rt = refreshTokenValue.value || localStorage.getItem('refresh_token')
    if (!rt) return false
    try {
      const data = await authApi.refreshToken(rt)
      token.value = data.access_token
      localStorage.setItem('access_token', data.access_token)
      return true
    } catch {
      return false
    }
  }

  return {
    token, refreshTokenValue, username, email, level,
    isLoggedIn, levelNum,
    login, logout, tryRefresh, fetchMe, updateLevel,
  }
})
