import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as backtestApi from '@/api/backtest'
import type { BacktestResult, BacktestRunRequest, BacktestStatus } from '@/types/api'

export const useBacktestStore = defineStore('backtest', () => {
  const taskId = ref<string | null>(null)
  const status = ref<BacktestStatus | null>(null)
  const result = ref<BacktestResult | null>(null)
  const errorMsg = ref<string | null>(null)
  const progressPct = ref<number | null>(null)
  const currentNav = ref<number | null>(null)
  const currentDate = ref<string | null>(null)
  const startedAt = ref<string | null>(null)
  const pollTimer = ref<ReturnType<typeof setInterval> | null>(null)

  async function submitRun(params: BacktestRunRequest): Promise<void> {
    result.value = null
    errorMsg.value = null
    progressPct.value = null
    currentNav.value = null
    currentDate.value = null
    startedAt.value = null
    const data = await backtestApi.submitBacktest(params)
    taskId.value = data.task_id
    status.value = data.status
    startPolling()
  }

  function startPolling(): void {
    if (pollTimer.value) clearInterval(pollTimer.value)
    const deadline = Date.now() + 5 * 60 * 1000 // 最长轮询 5 分钟
    pollTimer.value = setInterval(async () => {
      if (!taskId.value) return
      if (Date.now() > deadline) {
        stopPolling()
        errorMsg.value = '回测超时，请检查后端日志'
        status.value = 'FAILED'
        return
      }
      try {
        const statusData = await backtestApi.getBacktestStatus(taskId.value)
        status.value = statusData.status
        progressPct.value = statusData.progress_pct ?? null
        currentNav.value = statusData.current_nav ?? null
        currentDate.value = statusData.trade_date ?? null
        if (statusData.started_at && !startedAt.value) {
          startedAt.value = statusData.started_at
        }

        if (statusData.status === 'SUCCESS') {
          stopPolling()
          const raw = await backtestApi.getBacktestResult(taskId.value)
          // 将 daily_nav dict → {date, nav}[] 数组，按日期升序（见设计文档 §5.5）
          const navSeries = Object.entries(raw.daily_nav)
            .map(([date, nav]) => ({ date, nav: nav as number }))
            .sort((a, b) => a.date.localeCompare(b.date))
          result.value = {
            performance: raw.performance,
            disclaimer: raw.disclaimer,
            dataBaseline: raw.data_baseline ?? null,
            navSeries,
          }
        } else if (statusData.status === 'FAILED') {
          stopPolling()
          errorMsg.value = statusData.error_msg ?? '回测执行失败'
        }
      } catch {
        // best-effort，轮询错误不中断
      }
    }, 3000)
  }

  function stopPolling(): void {
    if (pollTimer.value) {
      clearInterval(pollTimer.value)
      pollTimer.value = null
    }
  }

  function reset(): void {
    stopPolling()
    taskId.value = null
    status.value = null
    result.value = null
    errorMsg.value = null
    progressPct.value = null
    currentNav.value = null
    currentDate.value = null
    startedAt.value = null
  }

  return {
    taskId, status, result, errorMsg,
    progressPct, currentNav, currentDate, startedAt,
    submitRun, startPolling, stopPolling, reset,
  }
})
