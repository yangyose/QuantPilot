<script setup lang="ts">
// V1.5-A A1c（2026-07-27）：V1.0 评审识别的 4 项 P0 回测缺陷（T+1 撮合 / 涨停停牌退市
// 未排除 / PE-PB+指数空 DF / 不调 RiskChecker）已在 V1.0 整改 Batch 3 + V1.5-A A2 全部
// 修复，本横幅由「缺陷清单（error）」收敛为「通用回测局限提示（warning）」。
</script>

<template>
  <a-alert
    type="warning"
    show-icon
    style="margin-bottom: 16px"
  >
    <template #message>
      <strong>回测结果说明</strong>
    </template>
    <template #description>
      <p style="margin: 0">
        回测采用 T+1 次日开盘撮合、排除无量一字板涨停 / 停牌 / 退市股、PE/PB 与指数按
        时点（PIT）切片、集成 RiskChecker（集中度 / 行业 / 回撤限制），与实盘共用同一
        评分 / 风控 / 仓位实例。但回测为历史数据模拟，净值、Sharpe 等指标与实盘可达成
        收益<strong>无系统性对应关系</strong>，仅作策略<strong>相对排序</strong>参考，
        不可作为绝对收益预期，不构成任何投资建议。
      </p>
    </template>
  </a-alert>
</template>
