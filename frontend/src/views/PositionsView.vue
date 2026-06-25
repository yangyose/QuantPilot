<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { usePositionStore } from '@/stores/positions'
import type { PositionItem, TradeRecord } from '@/types/api'
import EmptyState from '@/components/EmptyState.vue'
import { fmtAmount, fmtPct, fmtDate } from '@/utils/format'
import { message } from 'ant-design-vue'

const store = usePositionStore()

const activeTab = ref('positions')
const syncLoading = ref(false)
const addTradeOpen = ref(false)
const fundModalOpen = ref(false)
const fundModalType = ref<'deposit' | 'withdraw'>('deposit')

// ── A 股交易成本费率（与回测引擎 SDD §10.5 口径一致；个人券商实际费率可在录入时手改）──
const COMMISSION_RATE = 0.00025   // 佣金 万2.5（双向）
const MIN_COMMISSION = 5          // 单笔最低佣金 5 元
const STAMP_TAX_RATE = 0.0005     // 印花税 千0.5（仅卖出，2023-08 起减半）
const TRANSFER_FEE_RATE = 0.00001 // 过户费 万0.1（双向，并入佣金列展示）

const round2 = (x: number): number => Math.round(x * 100) / 100

const tradeForm = ref({
  ts_code: '', trade_type: 'BUY' as 'BUY' | 'SELL',
  price: 0, shares: 0,
  commission: 0, stamp_tax: 0,
  trade_date: new Date().toISOString().slice(0, 10),
})

// 成交额（不含费用）
const tradeGross = computed(() => (tradeForm.value.price || 0) * (tradeForm.value.shares || 0))
// 预计实付（买）/ 实收（卖）= 成交额 ± 费用
const tradeNet = computed(() => {
  const fee = (tradeForm.value.commission || 0) + (tradeForm.value.stamp_tax || 0)
  return tradeForm.value.trade_type === 'BUY' ? tradeGross.value + fee : tradeGross.value - fee
})

// 价格 / 数量 / 方向变化时自动重算费用（用户随后可手动覆盖，直到下次改价量）
function recalcFees(): void {
  const gross = tradeGross.value
  tradeForm.value.commission = gross > 0
    ? round2(Math.max(gross * (COMMISSION_RATE + TRANSFER_FEE_RATE), MIN_COMMISSION))
    : 0
  tradeForm.value.stamp_tax = tradeForm.value.trade_type === 'SELL' ? round2(gross * STAMP_TAX_RATE) : 0
}
watch(
  () => [tradeForm.value.price, tradeForm.value.shares, tradeForm.value.trade_type],
  recalcFees,
)

// 持仓盈亏值 =（现价 − 成本价）× 股数；任一缺失返回 null
function pnlAmount(r: PositionItem): number | null {
  if (r.current_price == null || r.cost_price == null) return null
  return (r.current_price - r.cost_price) * r.shares
}
const fundForm = ref({ amount: 0, trade_date: new Date().toISOString().slice(0, 10), note: '' })
const cashflowStart = ref<string | undefined>()
const cashflowEnd = ref<string | undefined>()

const tradesIncludeVoided = ref(false)
const cashflowIncludeVoided = ref(false)

// 作废订正 Modal
const voidModalOpen = ref(false)
const voidTarget = ref<{ kind: 'trade' | 'cashflow'; id: number; label: string } | null>(null)
const voidNote = ref('')

onMounted(() => {
  store.fetchAccount()
  store.fetchPositions()
  store.fetchCashflows()
  store.fetchTrades()
})

function openVoid(kind: 'trade' | 'cashflow', id: number, label: string) {
  voidTarget.value = { kind, id, label }
  voidNote.value = ''
  voidModalOpen.value = true
}

async function submitVoid() {
  if (!voidTarget.value) return
  const { kind, id } = voidTarget.value
  try {
    if (kind === 'trade') {
      await store.voidTrade(id, voidNote.value || undefined)
    } else {
      await store.voidCashflow(id, voidNote.value || undefined)
    }
    message.success('已作废，持仓/现金已订正')
    voidModalOpen.value = false
  } catch (err: unknown) {
    const e = err as { response?: { data?: { msg?: string; detail?: string } } }
    const detail = e.response?.data?.msg || e.response?.data?.detail || '作废失败'
    message.error(detail, 6)
  }
}

async function correctTrade(record: { id: number; ts_code: string; trade_type: 'BUY' | 'SELL'; price: number; shares: number; trade_date: string }) {
  // 订正 = 作废原成交 + 预填重新录入。作废失败（如超卖）则不打开录入框。
  try {
    await store.voidTrade(record.id, '订正：重新录入')
  } catch (err: unknown) {
    const e = err as { response?: { data?: { msg?: string; detail?: string } } }
    message.error(e.response?.data?.msg || e.response?.data?.detail || '作废失败', 6)
    return
  }
  message.success('原成交已作废，请修改后重新录入')
  tradeForm.value = {
    ts_code: record.ts_code,
    trade_type: record.trade_type,
    price: record.price,
    shares: record.shares,
    commission: 0,
    stamp_tax: 0,
    trade_date: record.trade_date,
  }
  recalcFees()  // 按预填价/量重算费用
  addTradeOpen.value = true
}

function openAddTrade(): void {
  tradeForm.value = {
    ts_code: '', trade_type: 'BUY',
    price: 0, shares: 0, commission: 0, stamp_tax: 0,
    trade_date: new Date().toISOString().slice(0, 10),
  }
  addTradeOpen.value = true
}

// 「显示已作废」勾选用 watch 驱动刷新（比 @change 更可靠，不依赖事件/v-model 时序）
watch(tradesIncludeVoided, (v) => {
  store.fetchTrades(v)
})
watch(cashflowIncludeVoided, (v) => {
  store.fetchCashflows({
    start_date: cashflowStart.value,
    end_date: cashflowEnd.value,
    include_voided: v,
  })
})

async function syncAccount() {
  syncLoading.value = true
  try {
    await store.syncAccount()
    message.success('盯市同步成功')
  } catch {
    message.error('同步失败')
  } finally {
    syncLoading.value = false
  }
}

async function submitTrade() {
  try {
    await store.recordTrade({ ...tradeForm.value, account_id: store.account?.id ?? 1 })
    message.success('交易录入成功')
    addTradeOpen.value = false
  } catch (err: unknown) {
    const e = err as { response?: { data?: { msg?: string; detail?: string } } }
    const detail = e.response?.data?.msg || e.response?.data?.detail || '交易录入失败'
    message.error(detail, 6)
  }
}

async function submitFund() {
  try {
    const fundBody = {
      account_id: store.account?.id ?? 1,
      amount: fundForm.value.amount,
      trade_date: fundForm.value.trade_date,
      note: fundForm.value.note,
    }
    if (fundModalType.value === 'deposit') {
      await store.deposit(fundBody)
      message.success('入金成功')
    } else {
      await store.withdraw(fundBody)
      message.success('出金成功')
    }
    fundModalOpen.value = false
    fundForm.value = { amount: 0, trade_date: new Date().toISOString().slice(0, 10), note: '' }
  } catch {
    message.error('操作失败')
  }
}

async function queryCashflows() {
  await store.fetchCashflows({
    start_date: cashflowStart.value,
    end_date: cashflowEnd.value,
    include_voided: cashflowIncludeVoided.value,
  })
}

function openFundModal(type: 'deposit' | 'withdraw') {
  fundModalType.value = type
  fundForm.value = { amount: 0, trade_date: new Date().toISOString().slice(0, 10), note: '' }
  fundModalOpen.value = true
}

// 数值排序：null 永远排在后面（升降序皆然由 antd 处理方向）
const numSorter = (key: keyof PositionItem) => (a: PositionItem, b: PositionItem) =>
  ((a[key] as number) ?? -Infinity) - ((b[key] as number) ?? -Infinity)

const positionColumns = [
  { title: '代码', dataIndex: 'ts_code', key: 'ts_code',
    sorter: (a: PositionItem, b: PositionItem) => a.ts_code.localeCompare(b.ts_code) },
  { title: '名称', dataIndex: 'name', key: 'name',
    customRender: ({ value }: { value: string | null }) => value ?? '—' },
  { title: '数量（股）', dataIndex: 'shares', key: 'shares' },
  { title: '成本价', dataIndex: 'cost_price', key: 'cost_price',
    customRender: ({ value }: { value: number | null }) => fmtAmount(value) },
  { title: '当前价', dataIndex: 'current_price', key: 'current_price',
    customRender: ({ value }: { value: number | null }) => fmtAmount(value) },
  { title: '市值', dataIndex: 'market_value', key: 'market_value',
    sorter: numSorter('market_value'),
    customRender: ({ value }: { value: number | null }) => fmtAmount(value) },
  { title: '盈亏值', key: 'pnl_amount',
    sorter: (a: PositionItem, b: PositionItem) =>
      (pnlAmount(a) ?? -Infinity) - (pnlAmount(b) ?? -Infinity) },
  { title: '盈亏率', dataIndex: 'pnl_pct', key: 'pnl_pct',
    sorter: numSorter('pnl_pct'),
    customRender: ({ value }: { value: number | null }) => fmtPct(value) },
  { title: '阶段', dataIndex: 'phase', key: 'phase',
    customRender: ({ value }: { value: string | null }) => value ?? '—' },
]

const cashflowColumns = [
  { title: '日期', dataIndex: 'trade_date', key: 'trade_date',
    customRender: ({ value }: { value: string }) => fmtDate(value) },
  { title: '类型', dataIndex: 'flow_type', key: 'flow_type' },
  { title: '金额', dataIndex: 'amount', key: 'amount',
    customRender: ({ value }: { value: number }) => fmtAmount(value) },
  { title: '备注', dataIndex: 'note', key: 'note',
    customRender: ({ value }: { value: string | null }) => value ?? '—' },
  { title: '操作', key: 'action', width: 90 },
]

const tradeColumns = [
  { title: '日期', dataIndex: 'trade_date', key: 'trade_date',
    customRender: ({ value }: { value: string }) => fmtDate(value) },
  { title: '代码', dataIndex: 'ts_code', key: 'ts_code' },
  { title: '名称', dataIndex: 'name', key: 'name',
    customRender: ({ value }: { value: string | null }) => value ?? '—' },
  { title: '方向', dataIndex: 'trade_type', key: 'trade_type' },
  { title: '价格', dataIndex: 'price', key: 'price',
    customRender: ({ value }: { value: number | null }) => fmtAmount(value) },
  { title: '数量', dataIndex: 'shares', key: 'shares' },
  { title: '成交额', dataIndex: 'amount', key: 'amount',
    customRender: ({ value }: { value: number | null }) => fmtAmount(value) },
  { title: '费用', key: 'fee' },        // 佣金 + 印花税
  { title: '实际金额', key: 'net' },    // 买:成交额+费用 / 卖:成交额−费用
  { title: '操作', key: 'action', width: 150 },
]

// 单笔成交费用合计 / 实际现金影响
function tradeFee(r: TradeRecord): number {
  return (r.commission ?? 0) + (r.stamp_tax ?? 0)
}
function tradeNetOf(r: TradeRecord): number | null {
  if (r.amount == null) return null
  return r.trade_type === 'BUY' ? r.amount + tradeFee(r) : r.amount - tradeFee(r)
}

// 成交/流水分页配置（可选 20/50/100，显示总数）
const listPagination = {
  pageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: ['20', '50', '100'],
  showTotal: (t: number) => `共 ${t} 条`,
}

// 已作废行整行置灰
function voidedRowClass(record: { is_voided?: boolean }): string {
  return record.is_voided ? 'voided-row' : ''
}

// 资金流水中交易费用类型不可单独作废（须经成交作废联动）
const FEE_FLOW_TYPES = ['BUY_FEE', 'SELL_PROCEEDS']
</script>

<template>
  <div>
    <!-- 账户概览卡片 -->
    <a-row :gutter="16" style="margin-bottom: 16px">
      <a-col :xs="24" :sm="12" :md="6">
        <a-statistic title="总资产" :value="fmtAmount(store.account?.total_assets)"
          style="background: #fff; padding: 16px; border-radius: 8px" />
      </a-col>
      <a-col :xs="24" :sm="12" :md="6">
        <a-statistic title="可用现金" :value="fmtAmount(store.account?.cash)"
          style="background: #fff; padding: 16px; border-radius: 8px" />
      </a-col>
      <a-col :xs="24" :sm="12" :md="6">
        <a-statistic title="同步时间" :value="store.account?.synced_at?.slice(0, 19) ?? '—'"
          style="background: #fff; padding: 16px; border-radius: 8px" />
      </a-col>
      <a-col :xs="24" :sm="12" :md="6">
        <a-statistic title="持仓数量" :value="store.positions.length"
          style="background: #fff; padding: 16px; border-radius: 8px" />
      </a-col>
    </a-row>

    <a-tabs v-model:active-key="activeTab">
      <!-- 持仓明细 Tab -->
      <a-tab-pane key="positions" tab="持仓明细">
        <a-space style="margin-bottom: 12px">
          <a-button type="primary" :loading="syncLoading" @click="syncAccount">同步盯市</a-button>
          <span style="color: #999; font-size: 12px">
            持仓由成交流水自动派生；建仓 / 导入已有持仓请到「交易明细」录入开仓买入。
          </span>
        </a-space>
        <a-table
          :columns="positionColumns"
          :data-source="store.positions"
          :loading="store.loading"
          :pagination="false"
          row-key="id"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'pnl_amount'">
              <span :style="{ color: (pnlAmount(record) ?? 0) >= 0 ? '#52c41a' : '#ff4d4f' }">
                {{ pnlAmount(record) == null ? '—' : fmtAmount(pnlAmount(record)) }}
              </span>
            </template>
            <template v-else-if="column.key === 'pnl_pct'">
              <span :style="{ color: (record.pnl_pct ?? 0) >= 0 ? '#52c41a' : '#ff4d4f' }">
                {{ fmtPct(record.pnl_pct) }}
              </span>
            </template>
          </template>
        </a-table>
        <EmptyState v-if="store.positions.length === 0 && !store.loading" title="暂无持仓" />
      </a-tab-pane>

      <!-- 交易录入 Tab -->
      <a-tab-pane key="trades" tab="交易明细">
        <a-space style="margin-bottom: 12px">
          <a-button type="primary" @click="openAddTrade">录入交易</a-button>
          <a-checkbox v-model:checked="tradesIncludeVoided">
            显示已作废
          </a-checkbox>
        </a-space>
        <a-table
          :columns="tradeColumns"
          :data-source="store.trades"
          :loading="store.loading"
          :row-class-name="voidedRowClass"
          :pagination="listPagination"
          row-key="id"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'trade_type'">
              <a-tag :color="record.trade_type === 'BUY' ? 'red' : 'green'">
                {{ record.trade_type === 'BUY' ? '买入' : '卖出' }}
              </a-tag>
            </template>
            <template v-else-if="column.key === 'fee'">
              {{ fmtAmount(tradeFee(record)) }}
            </template>
            <template v-else-if="column.key === 'net'">
              {{ tradeNetOf(record) == null ? '—' : fmtAmount(tradeNetOf(record)) }}
            </template>
            <template v-else-if="column.key === 'action'">
              <span v-if="record.is_voided" style="color: #999">已作废</span>
              <a-space v-else>
                <a @click="correctTrade(record)">订正</a>
                <a style="color: #ff4d4f" @click="openVoid('trade', record.id, `${record.ts_code} ${record.trade_type} ${record.shares}股`)">撤销</a>
              </a-space>
            </template>
          </template>
        </a-table>
        <EmptyState v-if="store.trades.length === 0" title="暂无成交记录" />
      </a-tab-pane>

      <!-- 资金流水 Tab -->
      <a-tab-pane key="cashflow" tab="资金流水">
        <a-space style="margin-bottom: 12px">
          <a-date-picker
            v-model:value="cashflowStart"
            placeholder="开始日期"
            value-format="YYYY-MM-DD"
            style="width: 150px"
          />
          <a-date-picker
            v-model:value="cashflowEnd"
            placeholder="结束日期"
            value-format="YYYY-MM-DD"
            style="width: 150px"
          />
          <a-button type="primary" @click="queryCashflows">查询</a-button>
          <a-button @click="openFundModal('deposit')">入金</a-button>
          <a-button @click="openFundModal('withdraw')">出金</a-button>
          <a-checkbox v-model:checked="cashflowIncludeVoided">
            显示已作废
          </a-checkbox>
        </a-space>
        <a-table
          :columns="cashflowColumns"
          :data-source="store.cashflows"
          :loading="store.loading"
          :row-class-name="voidedRowClass"
          :pagination="listPagination"
          row-key="id"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'action'">
              <span v-if="record.is_voided" style="color: #999">已作废</span>
              <a-tooltip v-else-if="FEE_FLOW_TYPES.includes(record.flow_type)"
                title="交易费用随成交作废，请在交易明细中撤销对应成交">
                <span style="color: #ccc">—</span>
              </a-tooltip>
              <a v-else style="color: #ff4d4f"
                @click="openVoid('cashflow', record.id, `${record.flow_type} ${fmtAmount(record.amount)}`)">撤销</a>
            </template>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>

    <!-- 录入交易 Modal -->
    <a-modal v-model:open="addTradeOpen" title="录入交易" @ok="submitTrade">
      <a-form layout="vertical">
        <a-form-item label="股票代码">
          <a-input v-model:value="tradeForm.ts_code" />
        </a-form-item>
        <a-form-item label="交易方向">
          <a-radio-group v-model:value="tradeForm.trade_type">
            <a-radio value="BUY">买入</a-radio>
            <a-radio value="SELL">卖出</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="成交价格">
          <a-input-number v-model:value="tradeForm.price" :min="0" :precision="2" style="width: 100%" />
        </a-form-item>
        <a-form-item label="成交数量">
          <a-input-number v-model:value="tradeForm.shares" :min="100" :step="100" style="width: 100%" />
        </a-form-item>
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="佣金（含过户费，可改）">
              <a-input-number v-model:value="tradeForm.commission" :min="0" :precision="2" style="width: 100%" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="印花税（仅卖出，可改）">
              <a-input-number v-model:value="tradeForm.stamp_tax" :min="0" :precision="2" style="width: 100%" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item label="交易日期">
          <a-date-picker v-model:value="tradeForm.trade_date" value-format="YYYY-MM-DD" style="width: 100%" />
        </a-form-item>
        <a-descriptions :column="1" size="small" bordered>
          <a-descriptions-item label="成交额">{{ fmtAmount(tradeGross) }}</a-descriptions-item>
          <a-descriptions-item label="费用合计">
            {{ fmtAmount((tradeForm.commission || 0) + (tradeForm.stamp_tax || 0)) }}
          </a-descriptions-item>
          <a-descriptions-item :label="tradeForm.trade_type === 'BUY' ? '预计实付' : '预计实收'">
            <strong>{{ fmtAmount(tradeNet) }}</strong>
          </a-descriptions-item>
        </a-descriptions>
        <div style="color: #999; font-size: 12px; margin-top: 8px">
          费用按 A 股标准费率（佣金万2.5+过户费万0.1，最低 5 元；印花税千0.5 仅卖出）自动估算，
          可按你的券商实际费率手动修改。
        </div>
      </a-form>
    </a-modal>

    <!-- 入金/出金 Modal -->
    <a-modal
      v-model:open="fundModalOpen"
      :title="fundModalType === 'deposit' ? '入金' : '出金'"
      @ok="submitFund"
      @cancel="fundModalOpen = false"
    >
      <a-form layout="vertical">
        <a-form-item label="金额">
          <a-input-number v-model:value="fundForm.amount" :min="0" :precision="2" style="width: 100%" />
        </a-form-item>
        <a-form-item label="日期">
          <a-date-picker v-model:value="fundForm.trade_date" value-format="YYYY-MM-DD" style="width: 100%" />
        </a-form-item>
        <a-form-item label="备注">
          <a-input v-model:value="fundForm.note" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 作废订正 Modal -->
    <a-modal v-model:open="voidModalOpen" title="确认作废" ok-text="确认作废"
      :ok-button-props="{ danger: true }" @ok="submitVoid">
      <a-alert type="warning" show-icon style="margin-bottom: 12px"
        :message="`将作废：${voidTarget?.label ?? ''}`"
        description="作废保留审计痕迹，并按剩余有效记录自动订正持仓与现金。" />
      <a-form layout="vertical">
        <a-form-item label="作废原因（可选）">
          <a-input v-model:value="voidNote" placeholder="如：价格录入错误 / 股数填错" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
:deep(.voided-row) {
  color: #bbb;
  text-decoration: line-through;
}
</style>
