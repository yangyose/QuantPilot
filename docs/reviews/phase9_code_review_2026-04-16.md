# Phase 9 代码评审报告

> **评审日期：** 2026-04-16
> **评审范围：** `frontend/` 所有实现文件（api / stores / views / components / router / tests）
> **依据文档：** `docs/design/phases/phase9_frontend.md` v1.1
> **评审版本：** v1.0

---

## 评审概要

| 维度 | 结论 |
|------|------|
| 功能范围完整性 | 基本覆盖，存在 2 个端点实现缺口（POST/PATCH /positions）及 1 项 UI 功能缺失 |
| 前后台接口整合性 | 主要端点对齐正确；发现 1 处设计文档字段名残留错误（不影响实现） |
| 错误处理 | 整体框架合理（allSettled / message.error），局部不一致 |
| 画面配置 | 布局清晰，部分指标展示不完整 |
| 使用者友善度 | T+1 警告、空状态、声明条均已实现；血缘展示过于简化 |

发现问题 **8 条**（P2×3，P3×5）。**全部已关闭 ✓**（2026-04-16 修复并验证）

---

## 问题清单

### C-01 【P2】positions.ts 缺少 POST/PATCH /positions 实现，PositionsView 缺失手动录入持仓功能

**文件：** `frontend/src/api/positions.ts`、`frontend/src/stores/positions.ts`、`frontend/src/views/PositionsView.vue`

**问题描述：**

设计文档 §3.3 明确列出 `positions.ts` 覆盖端点：

> GET/POST /positions, PATCH /positions/{id}, GET /account, ...

但实现中 `positions.ts` 只实现了 GET /positions，缺少：
- `POST /api/v1/positions`（手动录入持仓）
- `PATCH /api/v1/positions/{id}`（持仓备注/阶段更新）

`usePositionStore` 同样没有对应的 `addPosition` / `updatePosition` action。

`PositionsView.vue` Tab 1（持仓明细）只有"同步盯市"按钮，设计文档 §6.3 明确要求的"手动录入持仓（Modal 表单）"未实现。

**影响：** 用户无法手动录入持仓（只能依赖盯市同步），持仓备注和 phase 字段无法通过前端修改，功能完整性缺口。

**修复建议：**

`positions.ts` 补充：
```typescript
export async function addPosition(body: {
  account_id: number; ts_code: string; shares: number;
  cost_price?: number; open_date?: string;
}): Promise<PositionItem> {
  const res = await client.post('/api/v1/positions', body)
  return res.data.data as PositionItem
}

export async function patchPosition(
  id: number, body: { note?: string; phase?: string }
): Promise<void> {
  await client.patch(`/api/v1/positions/${id}`, body)
}
```

`usePositionStore` 补充对应 action，`PositionsView` Tab 1 补充"手动录入持仓"按钮和 Modal。

---

### C-02 【P2】ReportsView 报告内容渲染为原始 JSON，非设计文档要求的 Markdown

**文件：** `frontend/src/views/ReportsView.vue`（第 111 行）

**问题描述：**

设计文档 §6.5 明确：

> 点击报告 → 右侧面板展示报告内容（**Markdown 渲染**）

当前实现：
```html
<pre style="white-space: pre-wrap; font-size: 13px; max-height: 500px; overflow-y: auto">
  {{ selectedReport.content ? JSON.stringify(selectedReport.content, null, 2) : '（无内容）' }}
</pre>
```

展示的是 JSON 对象序列化结果，而 `Report.summary` 字段才是面向用户阅读的文本摘要。报告的 `content` 字段是结构化 JSON（后端 `ReportService` 生成），直接序列化展示对用户意义不大。

**影响：** 报告内容可读性极差；`summary` 字段（文字摘要）未被展示。

**修复建议：** 优先展示 `selectedReport.summary`（纯文本），再以折叠方式提供 `content` 的 JSON 原始数据（供高级用户查看）：
```html
<div v-if="selectedReport.summary" style="margin-bottom: 12px; white-space: pre-wrap">
  {{ selectedReport.summary }}
</div>
<a-collapse ghost>
  <a-collapse-panel header="原始数据（JSON）">
    <pre style="font-size: 12px">{{ JSON.stringify(selectedReport.content, null, 2) }}</pre>
  </a-collapse-panel>
</a-collapse>
```

---

### C-03 【P2】BacktestView 绩效指标不完整，缺少胜率和盈亏比

**文件：** `frontend/src/views/BacktestView.vue`（第 131–147 行）

**问题描述：**

设计文档 §6.6：

> 成功后展示：绩效指标卡片组（累计收益/最大回撤/夏普/**胜率**等）

后端 `BacktestResultRaw.performance` 包含 `win_rate` 和 `profit_loss_ratio` 两个字段（`types/api.ts` 中 `PerformanceSummary` 有定义），但 `BacktestView` 只展示了前三项：

```html
<a-statistic title="累计收益率" :value="fmtPct(perf['cumulative_return'])" />
<a-statistic title="最大回撤" :value="fmtPct(perf['max_drawdown'])" />
<a-statistic title="夏普比率" :value="..." />
```

胜率（`win_rate`）和盈亏比（`profit_loss_ratio`）未展示，影响用户对策略质量的判断。

**修复建议：** 在现有3列后追加2列（win_rate 可能为 null 时显示 N/A）：
```html
<a-col :span="8">
  <a-statistic title="胜率"
    :value="perf['win_rate'] != null ? fmtPct(perf['win_rate'] as number) : 'N/A'"
    style="background: #fff; padding: 12px; border-radius: 8px" />
</a-col>
<a-col :span="8">
  <a-statistic title="盈亏比"
    :value="perf['profit_loss_ratio'] != null
      ? (perf['profit_loss_ratio'] as number).toFixed(3) : 'N/A'"
    style="background: #fff; padding: 12px; border-radius: 8px" />
</a-col>
```

---

### C-04 【P3】设计文档 §7.1 BenchmarkPoint 字段名残留错误（`close` → `value`）

**文件：** `docs/design/phases/phase9_frontend.md`（§7.1）

**问题描述：**

设计文档 §7.1（D9-P2-02 修复版）写为：
```typescript
benchmarkSeries?: { date: string, close: number }[]
```

但后端 `performance_service._get_benchmark_series` 实际返回：
```python
{"date": str(r.trade_date), "value": round(float(r.close) / base_close, 6)}
```

字段名为 `value`（已归一化为相对首日倍数），而非 `close`。

**实现代码是正确的**：`types/api.ts` 的 `BenchmarkPoint.value` 和 `NavChart.vue` 的 `p.value` 均与后端实际一致。错误仅存在于设计文档（D9-P2-02 引入了错误的字段名）。

**修复建议：** 更新设计文档 §7.1：

```typescript
// 修改前
benchmarkSeries?: { date: string, close: number }[]
// 修改后
benchmarkSeries?: { date: string, value: number }[]  // 后端已归一化（相对首日倍数）
```

同步更新 §7.1 注释："后端已归一化为相对首日收盘价的倍数，直接使用 `value` 字段渲染"。

---

### C-05 【P3】PositionsView.submitTrade 错误信息不提取后端详情

**文件：** `frontend/src/views/PositionsView.vue`（第 44–50 行）

**问题描述：**

`PositionsView.submitTrade` 的 catch 块：
```typescript
} catch {
  message.error('交易录入失败')
}
```

而 `SignalsView.submitTrade` 同场景有完善的后端消息提取：
```typescript
} catch (err: unknown) {
  const e = err as { response?: { data?: { msg?: string; detail?: string } } }
  const detail = e.response?.data?.msg || e.response?.data?.detail || '交易录入失败'
  message.error(detail, 6)
}
```

当后端返回参数校验错误（如 SELL 超过持仓量）时，`PositionsView` 用户只看到"交易录入失败"，无法知道具体原因。

**修复建议：** 将 `PositionsView.submitTrade` 的 catch 块改为与 `SignalsView` 一致的错误提取方式。

---

### C-06 【P3】信号血缘 Drawer 展示过简，score_snapshot 评分构成未渲染

**文件：** `frontend/src/views/SignalsView.vue`（第 188–195 行）

**问题描述：**

设计文档 §6.2 要求：
> 血缘信息：来源 pipeline_run、score_snapshot 摘要

当前实现：
```html
<template v-if="signalStore.currentLineage">
  <a-divider>信号血缘</a-divider>
  <a-descriptions :column="1" size="small">
    <a-descriptions-item label="Pipeline 运行">
      {{ fmtDate((signalStore.currentLineage.pipeline_run?.['started_at'] ?? null) as string | null) }}
    </a-descriptions-item>
  </a-descriptions>
</template>
```

只展示了 `pipeline_run.started_at` 一个字段。`score_snapshot`（各策略评分构成）完全未渲染，用户无法查看信号的评分来源，血缘功能实际意义大打折扣。

**修复建议：** 补充 `score_snapshot` 展示。`score_snapshot` 为 `Record<string, unknown>`，可以遍历键值对展示各策略得分：

```html
<template v-if="signalStore.currentLineage?.score_snapshot">
  <a-descriptions :column="1" size="small" style="margin-top: 8px">
    <a-descriptions-item
      v-for="(val, key) in signalStore.currentLineage.score_snapshot"
      :key="key"
      :label="String(key)"
    >
      {{ typeof val === 'number' ? (val as number).toFixed(2) : String(val) }}
    </a-descriptions-item>
  </a-descriptions>
</template>
```

设计文档提到"水平条形图"，在 V1.0 可以用简单的文本展示代替，V1.5 再升级为图表。

---

### C-07 【P3】历史信号 Tab 首次切换需手动点击「查询」，体验不流畅

**文件：** `frontend/src/views/SignalsView.vue`

**问题描述：**

用户切换至"历史信号" Tab 时，`signalStore.history` 初始为空，页面显示空表格。用户必须手动点击「查询」按钮才会触发 `loadHistory()`。

首次进入 Tab 时无任何数据，用户容易误认为没有历史信号（或功能异常）。

**修复建议：** 监听 `activeTab` 变化，首次切换至 `'history'` 时自动触发一次默认查询：
```typescript
import { watch } from 'vue'

watch(activeTab, (tab) => {
  if (tab === 'history' && signalStore.history.length === 0) {
    loadHistory()
  }
})
```

---

### C-08 【P3】AppLayout 顶部未显示当前登录用户名

**文件：** `frontend/src/components/AppLayout.vue`（第 74–77 行）

**问题描述：**

设计文档 §7.2：

> 底部：当前登录用户 + 「退出」按钮

当前实现顶部只有一个「退出」按钮，无用户名显示：

```html
<a-layout-header style="...">
  <a-button type="link" @click="logout">
    <template #icon><LogoutOutlined /></template>
    退出
  </a-button>
</a-layout-header>
```

用户无法直观确认当前登录身份，多设备使用场景下体验不佳。

注：`useAuthStore` 存储了 token，但未存储 username；需要从 token 解码或在 login 时额外保存 username。

**修复建议：** 方案一：login 时将 username 写入 store（最简单）；方案二：从 JWT token payload 解码 sub 字段作为用户名展示。在 header 区域添加用户名文字。

---

## 问题汇总表

| 编号 | 优先级 | 文件 | 问题描述 | 状态 |
|------|--------|------|---------|------|
| C-01 | P2 | positions.ts / PositionsView.vue | POST/PATCH /positions 端点缺失，手动录入持仓功能缺失 | **已修复** |
| C-02 | P2 | ReportsView.vue | 报告内容渲染为原始 JSON，summary 字段未展示 | **已修复** |
| C-03 | P2 | BacktestView.vue | 绩效指标缺少 win_rate / profit_loss_ratio | **已修复** |
| C-04 | P3 | phase9_frontend.md §7.1 | BenchmarkPoint 字段名残留错误（`close` → `value`） | **已修复** |
| C-05 | P3 | PositionsView.vue | submitTrade 错误信息不提取后端详情 | **已修复** |
| C-06 | P3 | SignalsView.vue | 信号血缘 score_snapshot 未渲染 | **已修复** |
| C-07 | P3 | SignalsView.vue | 历史信号 Tab 首次切换不自动加载 | **已修复** |
| C-08 | P3 | AppLayout.vue | 顶部未显示当前登录用户名 | **已修复** |

---

## 亮点记录

以下实现值得肯定，留档供参考：

1. **`client.ts` 401 防循环刷新**：使用 `_retry` 标志防止 refresh 本身 401 时的死循环，实现比设计文档示例更健壮
2. **`DashboardView` Promise.allSettled**：多 API 并发加载，任一失败不阻塞其他区域渲染
3. **`BacktestStore` 超时保护**：5 分钟 deadline + 计时检查，防止轮询永不停止
4. **`FactorQualityView` 告警行高亮**：`row-class-name` 加红底 + Tooltip 解释，告警可见性好
5. **`DisclaimerBanner` 折叠设计**：默认收起 80 字，减少视觉噪音，展开查看全文，UX 友好
6. **`types/api.ts` 注释完整**：每个 interface 都有后端字段对齐注释，可读性强
7. **`StatusBadge.vue` 统一复用**：市场状态、回测状态、信号状态共用一个组件，CONFIG 映射表扩展方便
8. **Vitest 单元测试覆盖核心 Store**：`auth` / `signals` / `client` 拦截器均有 mock 测试，loading 状态转换有专项验证

---

## 整体评估

Phase 9 整体质量良好，技术栈选型合理，前后台接口对齐精度较高（主要端点 URL、参数、响应字段均正确）。
P2 问题集中在功能完整性缺口（持仓手动录入）和展示完整性（报告 Markdown 渲染、回测指标）。
P3 问题主要为细节体验优化。修复 C-01~C-03 后可达到设计文档规定的交付标准。
