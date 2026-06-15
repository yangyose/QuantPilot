<script setup lang="ts">
import { ref, computed, onUnmounted } from 'vue'
import { useBacktestStore } from '@/stores/backtest'
import NavChart from '@/components/NavChart.vue'
import DisclaimerBanner from '@/components/DisclaimerBanner.vue'
import BacktestLimitationsBanner from '@/components/BacktestLimitationsBanner.vue'
import TermLabel from '@/components/TermLabel.vue'
import { fmtPct, BACKTEST_STATUS_LABELS } from '@/utils/format'
import { message } from 'ant-design-vue'

// 已用时（秒）
function elapsedSeconds(startedAt: string | null): number {
  if (!startedAt) return 0
  return Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)
}

const store = useBacktestStore()

const form = ref({
  start_date: '2023-01-01',
  end_date: '2023-12-31',
  initial_capital: 1000000,
  commission_rate: 0.0003,
  stamp_tax_rate: 0.001,
  slippage_rate: 0.0002,
})
const showAdvanced = ref<string[]>([])
const submitting = ref(false)

onUnmounted(() => store.stopPolling())

async function submit() {
  submitting.value = true
  try {
    await store.submitRun({
      start_date: form.value.start_date,
      end_date: form.value.end_date,
      initial_capital: form.value.initial_capital,
      commission_rate: form.value.commission_rate,
      stamp_tax_rate: form.value.stamp_tax_rate,
      slippage_rate: form.value.slippage_rate,
    })
  } catch (err: unknown) {
    const axiosErr = err as { response?: { data?: { msg?: string; detail?: string } } }
    const detail = axiosErr.response?.data?.msg
      || axiosErr.response?.data?.detail
      || '提交失败，请检查参数'
    message.error(detail)
  } finally {
    submitting.value = false
  }
}

const statusColor = computed(() => {
  switch (store.status) {
    case 'SUCCESS': return '#52c41a'
    case 'FAILED': return '#ff4d4f'
    case 'RUNNING': return '#1677ff'
    default: return '#d9d9d9'
  }
})

const perf = computed(() => store.result?.performance ?? null)
const isRunning = computed(() => store.status === 'PENDING' || store.status === 'RUNNING')

// 进度条：PENDING 期间显示 0%，RUNNING 期间显示实际进度；未知时显示条纹动画
const progressPct = computed(() => {
  if (store.status === 'PENDING') return 0
  if (store.status === 'RUNNING') return store.progressPct ?? 0
  return 100
})
const progressStatus = computed(() => {
  if (store.status === 'FAILED') return 'exception'
  if (store.status === 'SUCCESS') return 'success'
  return 'active'  // 动画条纹
})
</script>

<template>
  <div>
    <BacktestLimitationsBanner />
    <a-row :gutter="16">
      <!-- 左：参数表单 -->
      <a-col :xs="24" :md="8">
        <a-card title="回测参数">
          <a-form layout="vertical">
            <a-form-item label="开始日期">
              <a-date-picker v-model:value="form.start_date" value-format="YYYY-MM-DD" style="width: 100%" />
            </a-form-item>
            <a-form-item label="结束日期">
              <a-date-picker v-model:value="form.end_date" value-format="YYYY-MM-DD" style="width: 100%" />
            </a-form-item>
            <a-form-item label="初始资金（元）">
              <a-input-number
                v-model:value="form.initial_capital"
                :min="10000"
                :step="100000"
                style="width: 100%"
              />
            </a-form-item>

            <a-collapse v-model:active-key="showAdvanced" ghost>
              <a-collapse-panel key="advanced" header="高级参数">
                <a-form-item label="佣金率">
                  <a-input-number v-model:value="form.commission_rate" :precision="4" :step="0.0001" style="width: 100%" />
                </a-form-item>
                <a-form-item label="印花税率">
                  <a-input-number v-model:value="form.stamp_tax_rate" :precision="4" :step="0.0001" style="width: 100%" />
                </a-form-item>
                <a-form-item label="滑点">
                  <a-input-number v-model:value="form.slippage_rate" :precision="4" :step="0.0001" style="width: 100%" />
                </a-form-item>
              </a-collapse-panel>
            </a-collapse>

            <a-button
              type="primary"
              block
              :loading="submitting"
              :disabled="isRunning"
              style="margin-top: 8px"
              @click="submit"
            >
              提交回测
            </a-button>
            <a-button
              v-if="store.taskId"
              block
              style="margin-top: 8px"
              @click="store.reset()"
            >
              重置
            </a-button>
          </a-form>
        </a-card>
      </a-col>

      <!-- 右：结果区 -->
      <a-col :xs="24" :md="16">
        <!-- 状态卡片 -->
        <a-card v-if="store.taskId" style="margin-bottom: 16px">
          <!-- 标题行：状态 + 已用时 -->
          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px">
            <a-badge :color="statusColor" :text="BACKTEST_STATUS_LABELS[store.status ?? ''] ?? store.status" />
            <a-spin v-if="isRunning" size="small" />
            <span v-if="store.status === 'PENDING'" style="color: rgba(0,0,0,.45); font-size: 13px">
              任务已提交，等待引擎启动…
            </span>
            <span v-if="store.status === 'RUNNING' && store.startedAt" style="color: rgba(0,0,0,.45); font-size: 13px">
              已用时 {{ elapsedSeconds(store.startedAt) }} 秒
            </span>
          </div>

          <!-- 进度条（运行中 / 完成 / 失败均显示） -->
          <a-progress
            :percent="progressPct"
            :status="progressStatus"
            :stroke-color="store.status === 'RUNNING' ? { from: '#1677ff', to: '#36cfc9' } : undefined"
            size="small"
            style="margin-bottom: 8px"
          />

          <!-- 运行中：实时进度细节 -->
          <div v-if="store.status === 'RUNNING'" style="display: flex; gap: 24px; font-size: 13px; color: rgba(0,0,0,.65)">
            <span v-if="store.currentDate">
              正在处理 <b>{{ store.currentDate }}</b>
            </span>
            <span v-else style="color: rgba(0,0,0,.35)">正在初始化数据…</span>
            <span v-if="store.currentNav != null">
              当前净值 <b :style="{ color: store.currentNav >= 1 ? '#52c41a' : '#ff4d4f' }">
                {{ store.currentNav.toFixed(4) }}
              </b>
            </span>
          </div>

          <!-- 错误信息 -->
          <a-alert v-if="store.errorMsg" type="error" :message="store.errorMsg" show-icon style="margin-top: 8px" />
        </a-card>

        <!-- 成功：绩效指标 -->
        <template v-if="store.status === 'SUCCESS' && perf">
          <a-row :gutter="[16, 16]" style="margin-bottom: 16px">
            <a-col :span="8">
              <div class="metric-card">
                <div class="metric-card__title"><TermLabel term="cumulative_return" /></div>
                <div class="metric-card__value">{{ fmtPct(perf['cumulative_return'] as number | null) }}</div>
              </div>
            </a-col>
            <a-col :span="8">
              <div class="metric-card">
                <div class="metric-card__title"><TermLabel term="max_drawdown" /></div>
                <div class="metric-card__value">{{ fmtPct(perf['max_drawdown'] as number | null) }}</div>
              </div>
            </a-col>
            <a-col :span="8">
              <div class="metric-card">
                <div class="metric-card__title"><TermLabel term="sharpe" /></div>
                <div class="metric-card__value">
                  {{ perf['sharpe_ratio'] != null ? (perf['sharpe_ratio'] as number).toFixed(3) : 'N/A' }}
                </div>
              </div>
            </a-col>
            <a-col :span="8">
              <div class="metric-card">
                <div class="metric-card__title"><TermLabel term="win_rate" /></div>
                <div class="metric-card__value">
                  {{ perf['win_rate'] != null ? fmtPct(perf['win_rate'] as number) : 'N/A' }}
                </div>
              </div>
            </a-col>
            <a-col :span="8">
              <div class="metric-card">
                <div class="metric-card__title"><TermLabel term="profit_loss_ratio" /></div>
                <div class="metric-card__value">
                  {{ perf['profit_loss_ratio'] != null ? (perf['profit_loss_ratio'] as number).toFixed(3) : 'N/A' }}
                </div>
              </div>
            </a-col>
          </a-row>

          <!-- 数据基线戳：本地算力中心回流的回测标注「跑在截至哪天的数据」 -->
          <a-alert
            v-if="store.result?.dataBaseline"
            type="info"
            show-icon
            style="margin-bottom: 16px"
            :message="`本结果基于截至 ${store.result.dataBaseline} 的数据（本地算力中心回流）`"
            description="回测截止日在过去时结果稳定可复现；若区间含此基线日之后服务器修订过的近端数据，重跑可能有细微出入。"
          />

          <!-- 回测声明 -->
          <DisclaimerBanner
            v-if="store.result?.disclaimer"
            :text="store.result.disclaimer"
          />

          <!-- 净值曲线 -->
          <a-card title="净值曲线">
            <NavChart
              v-if="store.result?.navSeries && store.result.navSeries.length > 0"
              :nav-series="store.result.navSeries"
            />
            <a-empty v-else description="历史价格数据暂不可用，净值曲线仅供结构验证" />
          </a-card>
        </template>

        <!-- 空状态提示 -->
        <a-card v-if="!store.taskId">
          <a-empty description="填写左侧参数后点击「提交回测」" />
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<style scoped>
.metric-card {
  background: #fff;
  padding: 12px;
  border-radius: 8px;
}
.metric-card__title {
  color: rgba(0, 0, 0, 0.45);
  font-size: 14px;
  margin-bottom: 4px;
}
.metric-card__value {
  font-size: 24px;
  font-weight: 500;
}
</style>
