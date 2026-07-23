<script setup lang="ts">
/**
 * Phase 12 §3.3.2：信号血缘三层折叠视图。
 *
 * L1 默认展开（业务可解释 trigger_reason 翻译 + market_state + composite_score）
 * L2 默认折叠（4 strategy_z + weights_source/hysteresis + 中性化前/后 JSONB tree）
 * L3 默认折叠（正交化残差 + 5 步管线 JSONB + pipeline_run 审计）
 *
 * V1.5-G G-5（设计 §6.2）：默认展开层数跟随用户 level（L1 只展开 L1 摘要，
 * L2/L3 逐层默认展开）；偏好非硬墙——任何层级用户仍可手动展开全部层。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeftOutlined } from '@ant-design/icons-vue'
import { useAuthStore } from '@/stores/auth'
import AttributionPanel from '@/components/AttributionPanel.vue'
import TermLabel from '@/components/TermLabel.vue'
import { getSignalLineage } from '@/api/signals'
import {
  STRATEGY_LABELS,
  translateHysteresisStatus,
  translateMarketState,
  translateTriggerReason,
  translateWeightsSource,
} from '@/utils/lineage'
import type { SignalLineage } from '@/types/api'

const route = useRoute()
const router = useRouter()

const auth = useAuthStore()

const lineage = ref<SignalLineage | null>(null)
const loading = ref(false)
const errorMsg = ref('')
// 默认展开层数随用户 level：L1→仅 L1；L2→L1+L2；L3→全部
const activeKeys = ref<string[]>(
  auth.levelNum >= 3 ? ['L1', 'L2', 'L3'] : auth.levelNum === 2 ? ['L1', 'L2'] : ['L1'],
)

const signalId = computed(() => Number(route.params.id))

onMounted(async () => {
  if (!Number.isFinite(signalId.value) || signalId.value <= 0) {
    errorMsg.value = '非法的信号 ID'
    return
  }
  loading.value = true
  try {
    lineage.value = await getSignalLineage(signalId.value)
  } catch (err: unknown) {
    const e = err as { response?: { status?: number; data?: { msg?: string } } }
    errorMsg.value = e.response?.status === 404
      ? `信号 #${signalId.value} 不存在`
      : (e.response?.data?.msg ?? '加载血缘数据失败')
  } finally {
    loading.value = false
  }
})

const snapshot = computed(() => lineage.value?.score_snapshot ?? null)
const pipelineRun = computed(() => lineage.value?.pipeline_run ?? null)

const compositePctText = computed(() => {
  const pct = snapshot.value?.composite_pct_in_market
  if (pct == null) return '—'
  return `市场顶 ${(pct * 100).toFixed(2)}%`
})

// Attribution 区间：信号日往前 30 天
const attributionStart = computed(() => {
  const d = lineage.value?.trade_date
  if (!d) return ''
  const start = new Date(d)
  start.setDate(start.getDate() - 30)
  return start.toISOString().slice(0, 10)
})
const attributionEnd = computed(() => lineage.value?.trade_date ?? '')

function back(): void {
  router.back()
}

// JSONB 树渲染 helper：将嵌套对象扁平化为 [{path, value}]
type FlatEntry = { path: string; value: string }
function flattenJson(obj: Record<string, unknown> | null, prefix = ''): FlatEntry[] {
  if (!obj) return []
  const out: FlatEntry[] = []
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      out.push(...flattenJson(v as Record<string, unknown>, path))
    } else if (typeof v === 'number') {
      out.push({ path, value: Number.isInteger(v) ? String(v) : v.toFixed(4) })
    } else {
      out.push({ path, value: String(v) })
    }
  }
  return out
}

const factorWinsorizedFlat = computed(() => flattenJson(snapshot.value?.factor_winsorized ?? null))
const factorNeutralizedFlat = computed(() => flattenJson(snapshot.value?.factor_neutralized ?? null))
const factorOrthogonalFlat = computed(() => flattenJson(snapshot.value?.factor_orthogonal ?? null))
const rawFactorsFlat = computed(() => flattenJson(snapshot.value?.raw_factors ?? null))
const breakdownRawFlat = computed(() => flattenJson(snapshot.value?.score_breakdown_raw ?? null))
const breakdownResidualFlat = computed(() => flattenJson(snapshot.value?.score_breakdown_residual ?? null))

const strategyZTable = computed(() => {
  const s = snapshot.value
  if (!s) return []
  const fmt = (v: number | null): string =>
    v == null ? '—' : Number(v).toFixed(4)
  return [
    { strategy: 'trend', label: STRATEGY_LABELS.trend, z: fmt(s.trend_score) },
    { strategy: 'momentum', label: STRATEGY_LABELS.momentum, z: fmt(s.momentum_score) },
    { strategy: 'mean_reversion', label: STRATEGY_LABELS.mean_reversion, z: fmt(s.reversion_score) },
    { strategy: 'value', label: STRATEGY_LABELS.value, z: fmt(s.value_score) },
  ]
})

const strategyZColumns = [
  { title: '策略', dataIndex: 'label', key: 'label' },
  { title: 'z-score', dataIndex: 'z', key: 'z' },
]

const jsonbColumns = [
  { title: '因子路径', dataIndex: 'path', key: 'path' },
  { title: '值', dataIndex: 'value', key: 'value' },
]
</script>

<template>
  <div class="lineage-page">
    <div class="header">
      <a-button type="text" @click="back">
        <template #icon><ArrowLeftOutlined /></template>
        返回
      </a-button>
      <h2>信号 #{{ signalId }} 评分溯源</h2>
    </div>

    <a-alert
      v-if="errorMsg"
      :message="errorMsg"
      type="error"
      show-icon
      style="margin-bottom: 12px"
    />

    <a-spin :spinning="loading">
      <template v-if="lineage && !errorMsg">
        <a-collapse v-model:active-key="activeKeys" :bordered="false">
          <!-- L1：业务可解释 -->
          <a-collapse-panel key="L1" header="💡 L1 · 业务可解释">
            <a-descriptions :column="1" size="small" bordered>
              <a-descriptions-item label="股票">
                {{ snapshot?.ts_code ?? '—' }}
              </a-descriptions-item>
              <a-descriptions-item label="交易日">
                {{ lineage.trade_date }}
              </a-descriptions-item>
              <a-descriptions-item label="触发原因">
                <a-tooltip :title="snapshot?.trigger_reason ?? ''">
                  <span style="font-weight: 600">
                    💡 {{ translateTriggerReason(snapshot?.trigger_reason) }}
                  </span>
                </a-tooltip>
              </a-descriptions-item>
              <a-descriptions-item label="市场状态">
                {{ translateMarketState(snapshot?.market_state) }}
              </a-descriptions-item>
              <a-descriptions-item label="综合评分">
                <b>{{ snapshot?.composite_score?.toFixed(2) ?? '—' }}</b>
                <span v-if="snapshot?.composite_z != null" style="margin-left: 8px; color: rgba(0,0,0,.45)">
                  (z = {{ snapshot.composite_z.toFixed(2) }})
                </span>
              </a-descriptions-item>
              <a-descriptions-item label="市场分位">
                {{ compositePctText }}
              </a-descriptions-item>
            </a-descriptions>
          </a-collapse-panel>

          <!-- L2：ICIR + 中性化 -->
          <a-collapse-panel key="L2" header="📊 L2 · 因子分数 + ICIR 权重 + 中性化前后">
            <h4>4 策略 z-score</h4>
            <a-table
              :columns="strategyZColumns"
              :data-source="strategyZTable"
              :pagination="false"
              row-key="strategy"
              size="small"
            />

            <a-descriptions :column="2" size="small" style="margin-top: 12px">
              <a-descriptions-item label="权重来源">
                <a-tag>{{ translateWeightsSource(snapshot?.weights_source) }}</a-tag>
              </a-descriptions-item>
              <a-descriptions-item label="Hysteresis">
                <a-tag>{{ translateHysteresisStatus(snapshot?.hysteresis_status) }}</a-tag>
              </a-descriptions-item>
            </a-descriptions>

            <h4 style="margin-top: 16px">中性化前 vs 中性化后（factor_winsorized → factor_neutralized）</h4>
            <a-row :gutter="16">
              <a-col :span="12">
                <div class="jsonb-block-title">winsorized（Step 1）</div>
                <a-table
                  v-if="factorWinsorizedFlat.length > 0"
                  :columns="[
                    { title: '因子路径', dataIndex: 'path' },
                    { title: '值', dataIndex: 'value' },
                  ]"
                  :data-source="factorWinsorizedFlat"
                  :pagination="false"
                  row-key="path"
                  size="small"
                />
                <a-empty v-else description="无数据" :image-style="{ height: '40px' }" />
              </a-col>
              <a-col :span="12">
                <div class="jsonb-block-title">neutralized（Step 2）</div>
                <a-table
                  v-if="factorNeutralizedFlat.length > 0"
                  :columns="[
                    { title: '因子路径', dataIndex: 'path' },
                    { title: '值', dataIndex: 'value' },
                  ]"
                  :data-source="factorNeutralizedFlat"
                  :pagination="false"
                  row-key="path"
                  size="small"
                />
                <a-empty v-else description="无数据" :image-style="{ height: '40px' }" />
              </a-col>
            </a-row>

            <a-divider>
              <TermLabel term="multi_factor_attribution" label="近 30 日多因子归因" />
            </a-divider>
            <AttributionPanel
              v-if="attributionStart && attributionEnd"
              :start-date="attributionStart"
              :end-date="attributionEnd"
            />
          </a-collapse-panel>

          <!-- L3：正交化残差 + Pipeline 审计 -->
          <a-collapse-panel key="L3" header="🔬 L3 · 正交化残差 + Pipeline 审计">
            <a-alert
              message="本节面向开发与审计人员，含 Gram-Schmidt 残差与 Pipeline 时间戳。"
              type="info"
              show-icon
              style="margin-bottom: 12px"
            />

            <h4>正交化（Step 4）</h4>
            <a-table
              v-if="factorOrthogonalFlat.length > 0"
              :columns="jsonbColumns"
              :data-source="factorOrthogonalFlat"
              :pagination="false"
              row-key="path"
              size="small"
            />
            <a-empty v-else description="无数据" :image-style="{ height: '40px' }" />

            <h4 style="margin-top: 16px">raw_factors（输入因子，未中性化）</h4>
            <a-table
              v-if="rawFactorsFlat.length > 0"
              :columns="jsonbColumns"
              :data-source="rawFactorsFlat"
              :pagination="false"
              row-key="path"
              size="small"
            />
            <a-empty v-else description="无数据" :image-style="{ height: '40px' }" />

            <h4 style="margin-top: 16px">合成 breakdown（raw 输入 vs 正交残差贡献）</h4>
            <a-row :gutter="16">
              <a-col :span="12">
                <div class="jsonb-block-title">breakdown_raw</div>
                <a-table
                  v-if="breakdownRawFlat.length > 0"
                  :columns="[
                    { title: '路径', dataIndex: 'path' },
                    { title: '值', dataIndex: 'value' },
                  ]"
                  :data-source="breakdownRawFlat"
                  :pagination="false"
                  row-key="path"
                  size="small"
                />
                <a-empty v-else description="无数据" :image-style="{ height: '40px' }" />
              </a-col>
              <a-col :span="12">
                <div class="jsonb-block-title">breakdown_residual</div>
                <a-table
                  v-if="breakdownResidualFlat.length > 0"
                  :columns="[
                    { title: '路径', dataIndex: 'path' },
                    { title: '值', dataIndex: 'value' },
                  ]"
                  :data-source="breakdownResidualFlat"
                  :pagination="false"
                  row-key="path"
                  size="small"
                />
                <a-empty v-else description="无数据" :image-style="{ height: '40px' }" />
              </a-col>
            </a-row>

            <a-divider>Pipeline 运行审计</a-divider>
            <a-descriptions :column="1" size="small" bordered>
              <a-descriptions-item label="交易日">
                {{ pipelineRun?.trade_date ?? '—' }}
              </a-descriptions-item>
              <a-descriptions-item label="CP1（数据就绪）">
                {{ pipelineRun?.cp1_at ?? '—' }}
              </a-descriptions-item>
              <a-descriptions-item label="CP2（评分完成）">
                {{ pipelineRun?.cp2_at ?? '—' }}
              </a-descriptions-item>
              <a-descriptions-item label="CP3（信号生成）">
                {{ pipelineRun?.cp3_at ?? '—' }}
              </a-descriptions-item>
              <a-descriptions-item label="数据快照版本">
                {{ pipelineRun?.data_snapshot_version ?? '—' }}
              </a-descriptions-item>
            </a-descriptions>
          </a-collapse-panel>
        </a-collapse>
      </template>
    </a-spin>
  </div>
</template>

<style scoped>
.lineage-page {
  padding: 16px;
  max-width: 1100px;
  margin: 0 auto;
}
.header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.header h2 {
  margin: 0;
  font-size: 18px;
}
.jsonb-block-title {
  color: rgba(0, 0, 0, 0.65);
  font-size: 13px;
  margin-bottom: 4px;
}
</style>
