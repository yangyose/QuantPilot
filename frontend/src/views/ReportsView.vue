<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getReports, getReport, generateReport } from '@/api/reports'
import EmptyState from '@/components/EmptyState.vue'
import TermLabel from '@/components/TermLabel.vue'
import DisclaimerBanner from '@/components/DisclaimerBanner.vue'
import { message } from 'ant-design-vue'
import type { Report } from '@/types/api'

const FACTOR_NAMES_MAP: Record<string, string> = {
  momentum_20d:  '动量因子（20日涨跌势）',
  pe_percentile: '估值因子（PE历史分位）',
  rsi_reversal:  '反转因子（RSI超买超卖）',
}
const STRATEGY_NAMES_MAP: Record<string, string> = {
  TrendStrategy:    '趋势策略',
  ValueStrategy:    '价值策略',
  ReversionStrategy:'反转策略',
}
const ALERT_LABELS_MAP: Record<string, { text: string; desc: string }> = {
  DECAY: {
    text: '效力衰减',
    desc: '该因子近3个月预测力持续偏低，建议在"设置"中降低此因子对应策略的权重，或暂停使用',
  },
}

function factorLabel(name: string)    { return FACTOR_NAMES_MAP[name]    ?? name }
function strategyLabel(name: string)  { return STRATEGY_NAMES_MAP[name]  ?? name }
function alertLabel(code: string)     { return ALERT_LABELS_MAP[code]?.text ?? code }
function alertDesc(code: string)      { return ALERT_LABELS_MAP[code]?.desc ?? code }

function icDesc(ic: number | null): string {
  if (ic === null) return '—'
  const abs = Math.abs(ic)
  if (abs >= 0.06) return '强'
  if (abs >= 0.03) return '有效'
  if (abs >= 0.01) return '较弱'
  return '无效'
}

const loading = ref(false)
const reports = ref<Report[]>([])
const selectedReport = ref<Report | null>(null)
const activeTab = ref('all')
const generateOpen = ref(false)
const generateForm = ref({ period_start: '', period_end: '' })
const generateLoading = ref(false)

onMounted(() => loadReports())

async function loadReports() {
  loading.value = true
  try {
    reports.value = await getReports()
  } catch {
    message.error('报告加载失败')
  } finally {
    loading.value = false
  }
}

async function selectReport(report: Report) {
  try {
    selectedReport.value = await getReport(report.id)
  } catch {
    message.error('报告详情加载失败')
  }
}

async function submitGenerate() {
  if (!generateForm.value.period_start || !generateForm.value.period_end) {
    message.warning('请选择完整日期范围')
    return
  }
  generateLoading.value = true
  try {
    await generateReport({
      start_date: generateForm.value.period_start,
      end_date: generateForm.value.period_end,
    })
    message.success('报告生成任务已提交，请稍后刷新')
    generateOpen.value = false
    await loadReports()
  } catch {
    message.error('报告生成失败')
  } finally {
    generateLoading.value = false
  }
}

const filteredReports = (type?: string) => {
  if (!type || type === 'all') return reports.value
  return reports.value.filter((r) => r.report_type === type)
}

const columns = [
  { title: '报告类型', dataIndex: 'report_type', key: 'report_type' },
  { title: '开始日期', dataIndex: 'period_start', key: 'period_start' },
  { title: '结束日期', dataIndex: 'period_end', key: 'period_end' },
  { title: '生成时间', dataIndex: 'generated_at', key: 'generated_at',
    customRender: ({ value }: { value: string | null }) => value?.slice(0, 10) ?? '—' },
]

const tradeRecordCols = [
  { title: '日期', dataIndex: 'trade_date', key: 'trade_date' },
  { title: '代码', dataIndex: 'ts_code', key: 'ts_code' },
  { title: '方向', dataIndex: 'trade_type', key: 'trade_type' },
  { title: '价格', dataIndex: 'price', key: 'price',
    customRender: ({ value }: { value: number | null }) => value != null ? value.toFixed(2) : '—' },
  { title: '数量', dataIndex: 'shares', key: 'shares' },
  { title: '金额', dataIndex: 'amount', key: 'amount',
    customRender: ({ value }: { value: number | null }) => value != null ? `¥${(value / 10000).toFixed(2)}万` : '—' },
]

const signalRecordCols = [
  { title: '日期', dataIndex: 'trade_date', key: 'trade_date' },
  { title: '代码', dataIndex: 'ts_code', key: 'ts_code' },
  { title: '类型', dataIndex: 'signal_type', key: 'signal_type' },
  { title: '评分', dataIndex: 'score', key: 'score',
    customRender: ({ value }: { value: number | null }) => value != null ? value.toFixed(3) : '—' },
  { title: '状态', dataIndex: 'status', key: 'status' },
]

const holdingCols = [
  { title: '代码', dataIndex: 'ts_code', key: 'ts_code' },
  { title: '股数', dataIndex: 'shares', key: 'shares' },
  { title: '成本价', dataIndex: 'cost_price', key: 'cost_price',
    customRender: ({ value }: { value: number | null }) => value != null ? value.toFixed(2) : '—' },
  { title: '市值', dataIndex: 'market_value', key: 'market_value',
    customRender: ({ value }: { value: number | null }) => value != null ? `¥${(value / 10000).toFixed(2)}万` : '—' },
  { title: '盈亏', dataIndex: 'pnl_pct', key: 'pnl_pct',
    customRender: ({ value }: { value: number | null }) => value != null ? `${(value * 100).toFixed(1)}%` : '—' },
]

</script>

<template>
  <div>
    <DisclaimerBanner
      text="报告内容（市场状态/信号/因子/持仓/绩效）基于历史数据与算法模型生成，仅作为决策辅助参考，不构成任何投资建议、不接受委托、不构成投顾服务。报告中的任何观点或数据均不可作为绝对收益预期。"
    />
    <a-row :gutter="16">
      <!-- 左：报告列表 -->
      <a-col :span="10">
        <a-card title="报告中心">
          <template #extra>
            <a-button type="primary" size="small" @click="generateOpen = true">生成自定义报告</a-button>
          </template>
          <a-tabs v-model:active-key="activeTab" size="small">
            <a-tab-pane key="all" tab="全部" />
            <a-tab-pane key="WEEKLY" tab="周报" />
            <a-tab-pane key="MONTHLY" tab="月报" />
            <a-tab-pane key="CUSTOM" tab="自定义" />
          </a-tabs>
          <a-spin :spinning="loading">
            <a-table
              :columns="columns"
              :data-source="filteredReports(activeTab)"
              row-key="id"
              size="small"
              :custom-row="(record: Report) => ({ onClick: () => selectReport(record) })"
            />
            <EmptyState v-if="filteredReports(activeTab).length === 0 && !loading" />
          </a-spin>
        </a-card>
      </a-col>

      <!-- 右：报告详情 -->
      <a-col :span="14">
        <a-card title="报告详情">
          <template v-if="selectedReport">
            <!-- 标题栏 -->
            <div style="margin-bottom: 12px; display: flex; align-items: center; gap: 8px">
              <a-tag :color="selectedReport.report_type === 'WEEKLY' ? 'blue' : selectedReport.report_type === 'MONTHLY' ? 'purple' : 'cyan'">
                {{ { WEEKLY: '周报', MONTHLY: '月报', CUSTOM: '自定义' }[selectedReport.report_type] ?? selectedReport.report_type }}
              </a-tag>
              <span style="color: rgba(0,0,0,.65)">{{ selectedReport.period_start }} ～ {{ selectedReport.period_end }}</span>
              <span style="margin-left: auto; font-size: 12px; color: rgba(0,0,0,.35)">生成于 {{ selectedReport.generated_at?.slice(0,10) ?? '—' }}</span>
            </div>

            <!-- 摘要 -->
            <a-alert
              v-if="selectedReport.summary"
              type="info"
              :message="selectedReport.summary"
              show-icon
              style="margin-bottom: 16px"
            />

            <!-- 结构化内容 -->
            <a-collapse :default-active-key="['trade','signal','holdings','factor']" ghost>

              <!-- 交易汇总 -->
              <a-collapse-panel
                v-if="selectedReport.content?.trade_summary"
                key="trade"
                header="交易汇总"
              >
                <a-descriptions :column="2" size="small" bordered>
                  <a-descriptions-item label="总成交笔数">{{ selectedReport.content.trade_summary.count }}</a-descriptions-item>
                  <a-descriptions-item label="买入笔数">{{ selectedReport.content.trade_summary.buy_count ?? selectedReport.content.trade_summary.buy }}</a-descriptions-item>
                  <a-descriptions-item label="卖出笔数">{{ selectedReport.content.trade_summary.sell_count ?? selectedReport.content.trade_summary.sell }}</a-descriptions-item>
                  <a-descriptions-item v-if="selectedReport.content.trade_summary.buy_amount != null" label="买入金额">
                    ¥{{ (selectedReport.content.trade_summary.buy_amount / 10000).toFixed(2) }} 万
                  </a-descriptions-item>
                  <a-descriptions-item v-if="selectedReport.content.trade_summary.sell_amount != null" label="卖出金额">
                    ¥{{ (selectedReport.content.trade_summary.sell_amount / 10000).toFixed(2) }} 万
                  </a-descriptions-item>
                </a-descriptions>
                <!-- 成交明细 -->
                <template v-if="selectedReport.content.trade_summary.records?.length">
                  <div style="margin-top: 10px; font-size: 12px; color: rgba(0,0,0,.45); margin-bottom: 4px">成交明细</div>
                  <a-table
                    :data-source="selectedReport.content.trade_summary.records"
                    :columns="tradeRecordCols"
                    size="small"
                    :pagination="false"
                    row-key="trade_date"
                  />
                </template>
              </a-collapse-panel>

              <!-- 信号统计 -->
              <a-collapse-panel
                v-if="selectedReport.content?.signal_summary || selectedReport.content?.new_signals != null"
                key="signal"
                header="信号统计"
              >
                <a-descriptions :column="2" size="small" bordered>
                  <a-descriptions-item label="信号总数">
                    {{ selectedReport.content.signal_summary?.count ?? selectedReport.content.new_signals ?? '—' }}
                  </a-descriptions-item>
                  <a-descriptions-item v-if="selectedReport.content.signal_summary?.acted_count != null" label="已执行信号">
                    {{ selectedReport.content.signal_summary.acted_count }}
                  </a-descriptions-item>
                  <a-descriptions-item v-if="selectedReport.content.signal_summary?.compliance_rate != null" label="信号执行率">
                    {{ (selectedReport.content.signal_summary.compliance_rate * 100).toFixed(0) }}%
                  </a-descriptions-item>
                </a-descriptions>
                <template v-if="selectedReport.content.signal_summary?.records?.length">
                  <div style="margin-top: 10px; font-size: 12px; color: rgba(0,0,0,.45); margin-bottom: 4px">信号明细</div>
                  <a-table
                    :data-source="selectedReport.content.signal_summary.records"
                    :columns="signalRecordCols"
                    size="small"
                    :pagination="false"
                    row-key="ts_code"
                  />
                </template>
              </a-collapse-panel>

              <!-- 持仓快照 -->
              <a-collapse-panel
                v-if="selectedReport.content?.holdings_snapshot?.length || selectedReport.content?.top_holdings?.length"
                key="holdings"
                header="持仓快照"
              >
                <template v-if="selectedReport.content.holdings_snapshot?.length">
                  <a-table
                    :data-source="selectedReport.content.holdings_snapshot"
                    :columns="holdingCols"
                    size="small"
                    :pagination="false"
                    row-key="ts_code"
                  />
                </template>
                <template v-else>
                  <span v-for="code in selectedReport.content.top_holdings" :key="code">
                    <a-tag>{{ code }}</a-tag>
                  </span>
                </template>
              </a-collapse-panel>

              <!-- 因子告警 -->
              <a-collapse-panel
                v-if="selectedReport.content?.factor_alerts?.length"
                key="factor"
                header="因子告警"
              >
                <div
                  v-for="fa in selectedReport.content.factor_alerts"
                  :key="fa.factor"
                  style="margin-bottom: 10px"
                >
                  <a-alert type="warning" show-icon>
                    <template #message>
                      <span style="font-weight: 600">
                        {{ strategyLabel(fa.strategy) }} · {{ factorLabel(fa.factor) }}
                      </span>
                      <a-tag color="red" style="margin-left: 8px; font-size: 12px">
                        {{ alertLabel(fa.alert) }}
                      </a-tag>
                    </template>
                    <template #description>
                      <div>{{ alertDesc(fa.alert) }}</div>
                      <div v-if="fa.ic_mean_3m != null" style="margin-top: 4px; font-size: 12px; color: rgba(0,0,0,.45)">
                        <TermLabel term="ic_mean_3m" label="近3月预测力均值" :show-icon="false" />：{{ fa.ic_mean_3m.toFixed(4) }}（{{ icDesc(fa.ic_mean_3m) }}）
                      </div>
                    </template>
                  </a-alert>
                </div>
              </a-collapse-panel>

            </a-collapse>
          </template>
          <EmptyState v-else title="请从左侧选择报告" description="点击报告行查看详情" />
        </a-card>
      </a-col>
    </a-row>

    <!-- 生成报告 Modal -->
    <a-modal
      v-model:open="generateOpen"
      title="生成自定义报告"
      :confirm-loading="generateLoading"
      @ok="submitGenerate"
    >
      <a-form layout="vertical">
        <a-form-item label="开始日期">
          <a-date-picker
            v-model:value="generateForm.period_start"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
        </a-form-item>
        <a-form-item label="结束日期">
          <a-date-picker
            v-model:value="generateForm.period_end"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>
