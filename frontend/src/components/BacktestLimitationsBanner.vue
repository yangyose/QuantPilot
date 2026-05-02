<script setup lang="ts">
import { ref } from 'vue'

const expanded = ref(true)
</script>

<template>
  <a-alert
    type="error"
    show-icon
    style="margin-bottom: 16px"
  >
    <template #message>
      <strong>V1.0 回测引擎已知局限（必读）</strong>
    </template>
    <template #description>
      <div v-if="expanded">
        <p style="margin: 0 0 8px 0">
          以下局限由 V1.0 评审（2026-04-27）确认，将在 V1.5 整改批次修复。
          回测净值、Sharpe 等指标与实盘可达成收益<strong>无系统性对应关系</strong>，
          仅作策略<strong>相对排序</strong>参考，不可作为绝对收益预期。
        </p>
        <ol style="margin: 0; padding-left: 20px">
          <li>
            <strong>T+1 撮合违反：</strong>当日信号当日 close 撮合，实盘 A 股需次日开盘成交，
            可能高估收益、低估实际滑点。
          </li>
          <li>
            <strong>涨停/停牌/已退市股未排除：</strong>quotes_t 仅含 close，
            limit_up/is_suspended/avg_amount 字段失效，涨停日仍可成交、停牌日仍可交易。
          </li>
          <li>
            <strong>PE/PB 历史分位与指数收益降级：</strong>回测中 pe_pb_history、
            index_adj_prices 给空 DataFrame，ValueStrategy 失效，Momentum 相对强度退化。
          </li>
          <li>
            <strong>RiskChecker 不参与：</strong>主流程不调用风控模块，
            集中度/行业/回撤限制全部失效，与实盘存在系统性差异。
          </li>
        </ol>
        <a-button
          size="small"
          type="link"
          style="padding: 4px 0; margin-top: 4px"
          @click="expanded = false"
        >
          收起
        </a-button>
      </div>
      <a-button
        v-else
        size="small"
        type="link"
        style="padding: 0"
        @click="expanded = true"
      >
        展开 4 项局限详情
      </a-button>
    </template>
  </a-alert>
</template>
