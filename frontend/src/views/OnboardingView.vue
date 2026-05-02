<script setup lang="ts">
/**
 * Phase 10 §6.6 首次启动向导。
 *
 * 6 步：欢迎 → Tushare Token → 初始数据拉取 → 账户初始资金 → 参数默认 → 完成
 *
 * 「初始数据拉取」步骤（v1.2 评审 C-07）：
 * - 自动 GET /data/status 检测当前数据新鲜度
 * - 默认提议回填最近 60 个自然日（满足 SDD §10 因子计算 60 日窗口下限）
 * - 调用 POST /data/ingest/history 同步阻塞执行（Phase 2 接口现状）
 *   服务后端持久化处理；前端展示 Spinner + 完成后写入摘要
 * - 当 TUSHARE_TOKEN 未配置（503）时给出明确提示并允许跳过
 *
 * V1.0 降级说明：
 * - Tushare Token 通过 .env 配置，向导仅引导用户复制字符串到 .env，无后端 Token 写入 API
 * - 历史回填同步执行没有真实进度推送（受限于 Phase 2 端点；Phase 11 改异步后接 WS 进度）
 * - 参数默认：向导完成时仅写 `POST /setup/complete`，不会重置任何 user_config
 */
import { message } from 'ant-design-vue'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getDataStatus, ingestHistory } from '@/api/data'
import { deposit } from '@/api/positions'
import { completeSetup, getSetupStatus } from '@/api/setup'
import type { DataStatus, IngestHistoryResult } from '@/types/api'

const router = useRouter()

const currentStep = ref(0)
const loading = ref(false)
const completedAt = ref<string | null>(null)

const tushareToken = ref('')
const initialCash = ref<number>(100_000)

// 数据拉取步状态
const dataStatus = ref<DataStatus | null>(null)
const dataStatusLoading = ref(false)
const dataStatusError = ref<string | null>(null)
const ingestRunning = ref(false)
const ingestResult = ref<IngestHistoryResult | null>(null)
const backfillDays = ref<number>(60)

const STEPS = [
  { title: '欢迎' },
  { title: 'Tushare Token' },
  { title: '初始数据拉取' },
  { title: '初始资金' },
  { title: '参数默认' },
  { title: '完成' },
]

onMounted(async () => {
  try {
    const status = await getSetupStatus()
    if (status.completed) {
      completedAt.value = status.completed_at
      message.info('向导已完成，正在跳转…')
      router.replace('/dashboard')
    }
  } catch {
    // 静默失败：允许用户继续向导
  }
})

function next() {
  if (currentStep.value < STEPS.length - 1) {
    currentStep.value += 1
    if (currentStep.value === 2 && dataStatus.value === null && !dataStatusError.value) {
      void loadDataStatus()
    }
  }
}

function prev() {
  if (currentStep.value > 0) {
    currentStep.value -= 1
  }
}

async function loadDataStatus() {
  dataStatusLoading.value = true
  dataStatusError.value = null
  try {
    dataStatus.value = await getDataStatus()
  } catch (err: unknown) {
    // 503 → TUSHARE_TOKEN 未配置；其余错误统一展示
    const e = err as { response?: { status?: number; data?: { msg?: string } } }
    if (e?.response?.status === 503) {
      dataStatusError.value =
        'Tushare 数据源未配置（503）。请先在 backend/.env 设置 TUSHARE_TOKEN 并重启服务。'
    } else {
      dataStatusError.value = e?.response?.data?.msg ?? '数据状态查询失败'
    }
  } finally {
    dataStatusLoading.value = false
  }
}

function offsetDate(today: Date, days: number): string {
  const d = new Date(today.getTime())
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

async function runHistoryIngest() {
  if (backfillDays.value <= 0 || backfillDays.value > 365) {
    message.warning('回填天数请填 1–365')
    return
  }
  ingestRunning.value = true
  ingestResult.value = null
  try {
    const today = new Date()
    const startDate = offsetDate(today, backfillDays.value)
    const endDate = today.toISOString().slice(0, 10)
    ingestResult.value = await ingestHistory(startDate, endDate)
    message.success(
      `回填完成：成功 ${ingestResult.value.success_count} 日，失败 ${ingestResult.value.fail_count} 日`,
    )
    // 重新加载 status 让用户看到 latest_quote_date 更新
    await loadDataStatus()
  } catch (err: unknown) {
    const e = err as { response?: { status?: number; data?: { msg?: string } } }
    if (e?.response?.status === 503) {
      dataStatusError.value =
        'Tushare 数据源未配置（503）。请先在 backend/.env 设置 TUSHARE_TOKEN 并重启服务。'
    } else {
      message.error(e?.response?.data?.msg ?? '历史数据回填失败')
    }
  } finally {
    ingestRunning.value = false
  }
}

async function saveInitialCash() {
  if (!Number.isFinite(initialCash.value) || initialCash.value <= 0) {
    message.warning('请输入正数初始资金')
    return
  }
  loading.value = true
  try {
    // Phase 10 §6.6：通过 POST /account/deposit 在默认账户记入入金（FundFlow.DEPOSIT）。
    // 不再写 user_config（合法 key 仅 12 项白名单，见 phase10 §6.9）。
    const today = new Date().toISOString().slice(0, 10)
    await deposit({
      account_id: 1,
      amount: initialCash.value,
      trade_date: today,
      note: '首次向导设置初始资金',
    })
    message.success('已记录初始资金到默认账户，后续可在账户页调整')
    next()
  } catch {
    message.error('保存失败，可跳过后续在账户页手动入金')
  } finally {
    loading.value = false
  }
}

async function onComplete() {
  loading.value = true
  try {
    await completeSetup()
    message.success('向导完成！')
    router.replace('/dashboard')
  } catch {
    message.error('标记完成失败，请稍后重试')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="onboarding">
    <a-card class="onboarding-card">
      <h2 style="margin-bottom: 24px">QuantPilot 首次启动向导</h2>

      <a-steps :current="currentStep" size="small" style="margin-bottom: 32px">
        <a-step v-for="s in STEPS" :key="s.title" :title="s.title" />
      </a-steps>

      <!-- Step 0: 欢迎 -->
      <div v-if="currentStep === 0">
        <a-alert
          type="info"
          show-icon
          message="个人量化交易决策辅助系统"
          description="本工具为你提供每日信号、候选股池、账户管理和绩效回测。所有决策仅作参考，盈亏自负。"
          style="margin-bottom: 16px"
        />
        <p>下一步我们将引导你完成 Tushare 数据源、初始数据拉取、账户初始资金与参数默认。</p>
      </div>

      <!-- Step 1: Tushare Token -->
      <div v-else-if="currentStep === 1">
        <a-alert
          type="warning"
          show-icon
          message="TUSHARE_TOKEN 目前通过 .env 配置"
          description="请将以下 Token 粘贴到 backend/.env 中，并重启后端服务后继续。若暂不配置，系统将使用演示数据（数据采集 API 返回 503）。"
          style="margin-bottom: 16px"
        />
        <a-form layout="vertical">
          <a-form-item label="Tushare Token（可选）">
            <a-input
              v-model:value="tushareToken"
              placeholder="粘贴你的 Tushare Pro token 作提示；系统不保存此值"
              allow-clear
            />
          </a-form-item>
          <a-form-item label="环境变量写入">
            <a-typography-paragraph copyable>
              TUSHARE_TOKEN={{ tushareToken || '<你的token>' }}
            </a-typography-paragraph>
          </a-form-item>
        </a-form>
      </div>

      <!-- Step 2: 初始数据拉取（v1.2 评审 C-07 新增） -->
      <div v-else-if="currentStep === 2">
        <a-alert
          type="info"
          show-icon
          message="数据源就绪后，建议先回填最近的 OHLCV / 财务 / 指数数据"
          description="不回填也可继续，但首次因子计算 / 信号生成需要至少 60 个交易日的历史窗口。"
          style="margin-bottom: 16px"
        />

        <!-- 数据状态摘要 -->
        <a-spin :spinning="dataStatusLoading">
          <a-descriptions
            v-if="dataStatus"
            title="当前数据状态"
            bordered
            :column="1"
            size="small"
            style="margin-bottom: 16px"
          >
            <a-descriptions-item label="最近行情日">
              {{ dataStatus.latest_quote_date ?? '无' }}
            </a-descriptions-item>
            <a-descriptions-item label="股票数量">
              {{ dataStatus.stock_count }}
            </a-descriptions-item>
            <a-descriptions-item label="指数列表">
              {{ dataStatus.index_codes.length === 0 ? '无' : dataStatus.index_codes.join(' / ') }}
            </a-descriptions-item>
            <a-descriptions-item label="数据是否最新">
              <a-tag :color="dataStatus.is_up_to_date ? 'green' : 'orange'">
                {{ dataStatus.is_up_to_date ? '已是最新' : '需要回填' }}
              </a-tag>
            </a-descriptions-item>
          </a-descriptions>
          <a-alert
            v-else-if="dataStatusError"
            type="error"
            show-icon
            :message="dataStatusError"
            style="margin-bottom: 16px"
          />
        </a-spin>

        <a-form layout="inline" style="margin-bottom: 16px">
          <a-form-item label="回填天数">
            <a-input-number
              v-model:value="backfillDays"
              :min="1"
              :max="365"
              :step="10"
              addon-after="天"
              style="width: 160px"
            />
          </a-form-item>
          <a-form-item>
            <a-button
              type="primary"
              :loading="ingestRunning"
              :disabled="!!dataStatusError"
              @click="runHistoryIngest"
            >
              开始回填
            </a-button>
          </a-form-item>
          <a-form-item>
            <a-button :disabled="ingestRunning" @click="loadDataStatus">
              刷新状态
            </a-button>
          </a-form-item>
        </a-form>

        <!-- 拉取结果 -->
        <a-alert
          v-if="ingestResult"
          type="success"
          show-icon
          :message="`回填完成：成功 ${ingestResult.success_count} 日，失败 ${ingestResult.fail_count} 日`"
          :description="
            ingestResult.failed_dates && ingestResult.failed_dates.length > 0
              ? `失败日期：${ingestResult.failed_dates.slice(0, 5).join(', ')}${
                  ingestResult.failed_dates.length > 5 ? ' …' : ''
                }`
              : undefined
          "
          style="margin-bottom: 16px"
        />

        <p style="color: #8c8c8c; font-size: 12px">
          Phase 2 当前为同步阻塞执行（无 WS 进度推送）；后续 Phase 11 异步化后会展示实时进度。
          可点「下一步」直接跳过此步骤，稍后在数据采集端点手动触发。
        </p>
      </div>

      <!-- Step 3: 初始资金 -->
      <div v-else-if="currentStep === 3">
        <a-form layout="vertical">
          <a-form-item
            label="账户初始资金"
            help="默认 100,000 元（SDD §14.1）。可稍后在设置页调整。"
          >
            <a-input-number
              v-model:value="initialCash"
              :min="0"
              :step="10000"
              style="width: 240px"
              addon-after="元"
            />
          </a-form-item>
        </a-form>
      </div>

      <!-- Step 4: 参数默认 -->
      <div v-else-if="currentStep === 4">
        <a-alert
          type="info"
          show-icon
          message="推荐使用 SDD 默认参数"
          description="信号阈值、仓位上限、风险限制等均已按 SDD 附录 B 预置。你可稍后在『设置』页逐项调整，或直接导入 YAML 配置文件。"
          style="margin-bottom: 16px"
        />
        <p style="color: #8c8c8c; font-size: 12px">
          所有参数均可随时变更，变更历史在"设置 → 变更历史"查看。
        </p>
      </div>

      <!-- Step 5: 完成 -->
      <div v-else-if="currentStep === 5">
        <a-result
          status="success"
          title="即将开始使用 QuantPilot"
          sub-title="点击下方按钮标记向导完成。随后自动跳转到总览页。"
        >
          <template #extra>
            <a-button
              type="primary"
              :loading="loading"
              @click="onComplete"
            >
              完成向导，进入总览
            </a-button>
          </template>
        </a-result>
      </div>

      <!-- 导航按钮 -->
      <div
        v-if="currentStep < STEPS.length - 1"
        style="display: flex; justify-content: space-between; margin-top: 24px"
      >
        <a-button :disabled="currentStep === 0 || ingestRunning" @click="prev">上一步</a-button>
        <a-space>
          <a-button v-if="currentStep === 3" type="primary" :loading="loading" @click="saveInitialCash">
            保存并继续
          </a-button>
          <a-button v-else type="primary" :disabled="ingestRunning" @click="next">下一步</a-button>
        </a-space>
      </div>
    </a-card>
  </div>
</template>

<style scoped>
.onboarding {
  max-width: 720px;
  margin: 48px auto;
}
.onboarding-card {
  padding: 24px;
}
</style>
