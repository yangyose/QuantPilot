/**
 * Phase 13 §3.7.3：WebSocket 通用客户端。
 *
 * 用法：
 *   const ws = new WebSocketClient('/api/v1/pipeline/progress')
 *   ws.onMessage((data) => { ... })
 *   ws.connect()
 *   // 卸载时
 *   ws.close()
 *
 * 自动重连：onclose 后最多重试 maxRetries=5 次，间隔 retryInterval=5000ms。
 * 主动 close() 会把 maxRetries 置 0，不再重连。
 */
export class WebSocketClient {
  private ws: WebSocket | null = null
  private readonly url: string
  private retries = 0
  private maxRetries = 5
  private readonly retryInterval = 5000
  private onMessageCallback: ((data: unknown) => void) | null = null
  private onErrorCallback: ((err: Event) => void) | null = null

  constructor(path: string) {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || window.location.origin
    const wsBase = baseUrl.replace(/^http/, 'ws')
    this.url = `${wsBase}${path}`
  }

  connect(): void {
    try {
      this.ws = new WebSocket(this.url)
    } catch (err) {
      console.error('WS 构造失败', err)
      return
    }
    this.ws.onopen = () => {
      this.retries = 0
    }
    this.ws.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        this.onMessageCallback?.(data)
      } catch (err) {
        console.warn('WS payload 解析失败', err, e.data)
      }
    }
    this.ws.onclose = () => {
      this.ws = null
      this._maybeReconnect()
    }
    this.ws.onerror = (e: Event) => {
      console.error('WS 错误', e)
      this.onErrorCallback?.(e)
    }
  }

  onMessage(cb: (data: unknown) => void): void {
    this.onMessageCallback = cb
  }

  onError(cb: (err: Event) => void): void {
    this.onErrorCallback = cb
  }

  close(): void {
    this.maxRetries = 0
    this.ws?.close()
    this.ws = null
  }

  private _maybeReconnect(): void {
    if (this.retries >= this.maxRetries) return
    this.retries++
    setTimeout(() => this.connect(), this.retryInterval)
  }
}

export default WebSocketClient
