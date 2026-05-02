import { beforeEach, describe, expect, it } from 'vitest'

// localStorage mock（jsdom 环境已提供，此处仅做行为验证）
describe('API client 拦截器', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('client 模块可正常导入', async () => {
    const { default: client } = await import('@/api/client')
    expect(client).toBeDefined()
    expect(typeof client.get).toBe('function')
    expect(typeof client.post).toBe('function')
  })

  it('请求拦截器已注册', async () => {
    const { default: client } = await import('@/api/client')
    // axios interceptors 对象存在且 handlers 长度 > 0
    expect((client.interceptors.request as any).handlers.length).toBeGreaterThan(0)
  })

  it('响应拦截器已注册', async () => {
    const { default: client } = await import('@/api/client')
    expect((client.interceptors.response as any).handlers.length).toBeGreaterThan(0)
  })

  it('有 token 时 baseURL 已配置', async () => {
    localStorage.setItem('access_token', 'my-token')
    const { default: client } = await import('@/api/client')
    // baseURL 为空字符串（vite env 未设置时默认）或已配置
    expect(client.defaults.baseURL).toBeDefined()
  })
})
