<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { usePositionStore } from '@/stores/positions'
import EmptyState from '@/components/EmptyState.vue'
import { fmtAmount, fmtPct, fmtDate } from '@/utils/format'
import { message } from 'ant-design-vue'

const store = usePositionStore()

const activeTab = ref('positions')
const syncLoading = ref(false)
const addTradeOpen = ref(false)
const addPositionOpen = ref(false)
const fundModalOpen = ref(false)
const fundModalType = ref<'deposit' | 'withdraw'>('deposit')

const addPositionForm = ref({
  ts_code: '', shares: 100, cost_price: 0,
  open_date: new Date().toISOString().slice(0, 10),
  phase: 'BUILD' as 'BUILD' | 'HOLD' | 'REDUCE',
})

const tradeForm = ref({
  ts_code: '', trade_type: 'BUY' as 'BUY' | 'SELL',
  price: 0, shares: 0,
  trade_date: new Date().toISOString().slice(0, 10),
})
const fundForm = ref({ amount: 0, trade_date: new Date().toISOString().slice(0, 10), note: '' })
const cashflowStart = ref<string | undefined>()
const cashflowEnd = ref<string | undefined>()

onMounted(() => {
  store.fetchAccount()
  store.fetchPositions()
  store.fetchCashflows()
})

async function submitAddPosition() {
  try {
    await store.addPosition({
      account_id: store.account?.id ?? 1,
      ...addPositionForm.value,
    })
    message.success('持仓录入成功')
    addPositionOpen.value = false
    addPositionForm.value = {
      ts_code: '', shares: 100, cost_price: 0,
      open_date: new Date().toISOString().slice(0, 10),
      phase: 'BUILD',
    }
  } catch (err: unknown) {
    const e = err as { response?: { data?: { msg?: string; detail?: string } } }
    const detail = e.response?.data?.msg || e.response?.data?.detail || '持仓录入失败'
    message.error(detail, 6)
  }
}

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
  await store.fetchCashflows({ start_date: cashflowStart.value, end_date: cashflowEnd.value })
}

function openFundModal(type: 'deposit' | 'withdraw') {
  fundModalType.value = type
  fundForm.value = { amount: 0, trade_date: new Date().toISOString().slice(0, 10), note: '' }
  fundModalOpen.value = true
}

const positionColumns = [
  { title: '代码', dataIndex: 'ts_code', key: 'ts_code' },
  { title: '数量（股）', dataIndex: 'shares', key: 'shares' },
  { title: '成本价', dataIndex: 'cost_price', key: 'cost_price',
    customRender: ({ value }: { value: number | null }) => fmtAmount(value) },
  { title: '当前价', dataIndex: 'current_price', key: 'current_price',
    customRender: ({ value }: { value: number | null }) => fmtAmount(value) },
  { title: '市值', dataIndex: 'market_value', key: 'market_value',
    customRender: ({ value }: { value: number | null }) => fmtAmount(value) },
  { title: '盈亏率', dataIndex: 'pnl_pct', key: 'pnl_pct',
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
]
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
          <a-button @click="addPositionOpen = true">手动录入持仓</a-button>
        </a-space>
        <a-table
          :columns="positionColumns"
          :data-source="store.positions"
          :loading="store.loading"
          row-key="id"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'pnl_pct'">
              <span :style="{ color: (record.pnl_pct ?? 0) >= 0 ? '#52c41a' : '#ff4d4f' }">
                {{ fmtPct(record.pnl_pct) }}
              </span>
            </template>
          </template>
        </a-table>
        <EmptyState v-if="store.positions.length === 0 && !store.loading" title="暂无持仓" />
      </a-tab-pane>

      <!-- 交易录入 Tab -->
      <a-tab-pane key="trades" tab="交易录入">
        <a-button type="primary" style="margin-bottom: 12px" @click="addTradeOpen = true">录入交易</a-button>
        <a-table
          :columns="cashflowColumns"
          :data-source="store.cashflows.filter(f => f.flow_type === 'TRADE')"
          row-key="id"
          size="small"
        />
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
        </a-space>
        <a-table
          :columns="cashflowColumns"
          :data-source="store.cashflows"
          :loading="store.loading"
          row-key="id"
          size="small"
        />
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
        <a-form-item label="交易日期">
          <a-date-picker v-model:value="tradeForm.trade_date" value-format="YYYY-MM-DD" style="width: 100%" />
        </a-form-item>
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

    <!-- 手动录入持仓 Modal -->
    <a-modal v-model:open="addPositionOpen" title="录入持仓" @ok="submitAddPosition">
      <a-form layout="vertical">
        <a-form-item label="股票代码">
          <a-input v-model:value="addPositionForm.ts_code" placeholder="如 600519.SH" />
        </a-form-item>
        <a-form-item label="持仓数量（股）">
          <a-input-number v-model:value="addPositionForm.shares" :min="100" :step="100" style="width: 100%" />
        </a-form-item>
        <a-form-item label="成本价（元）">
          <a-input-number v-model:value="addPositionForm.cost_price" :min="0" :precision="2" style="width: 100%" />
        </a-form-item>
        <a-form-item label="建仓日期">
          <a-date-picker v-model:value="addPositionForm.open_date" value-format="YYYY-MM-DD" style="width: 100%" />
        </a-form-item>
        <a-form-item label="阶段">
          <a-radio-group v-model:value="addPositionForm.phase">
            <a-radio value="BUILD">建仓</a-radio>
            <a-radio value="HOLD">持有</a-radio>
            <a-radio value="REDUCE">减仓</a-radio>
          </a-radio-group>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>
