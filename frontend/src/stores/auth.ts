import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as authApi from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem('access_token'))
  const refreshTokenValue = ref<string | null>(localStorage.getItem('refresh_token'))
  const username = ref<string | null>(localStorage.getItem('username'))

  const isLoggedIn = computed(() => token.value !== null)

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
  }

  function logout(): void {
    token.value = null
    refreshTokenValue.value = null
    username.value = null
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('username')
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

  return { token, refreshTokenValue, username, isLoggedIn, login, logout, tryRefresh }
})
