import axios from 'axios'

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 15000,
})

// 请求拦截：从 localStorage 注入 Authorization
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截：401 → 尝试用 refresh token 换新 token，失败则跳登录页
client.interceptors.response.use(
  (res) => res,
  async (err) => {
    if (err.response?.status === 401 && !err.config._retry) {
      err.config._retry = true
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken) {
        try {
          const res = await axios.post(
            `${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/auth/refresh`,
            { refresh_token: refreshToken },
          )
          const newToken: string = res.data.data.access_token
          localStorage.setItem('access_token', newToken)
          err.config.headers.Authorization = `Bearer ${newToken}`
          return client.request(err.config)
        } catch {
          // refresh 失败，清空凭证
        }
      }
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  },
)

export default client
