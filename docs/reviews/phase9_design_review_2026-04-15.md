# Phase 9 设计评审报告

**评审对象**：Phase 9 设计文档 `docs/design/phases/phase9_frontend.md` v1.0  
**评审依据**：`QuantPilot_SDD.md`（§11~§14）、`system_design.md`（§1.1, §9）、后端已交付 API 实现  
**评审日期**：2026-04-15  
**评审人**：Claude Code  
**状态**：已关闭（7 项问题全部修复，设计文档更新至 v1.1）

---

## 1. 总体评价

| 维度 | 评价 |
|------|------|
| **范围完整性** | 8 个页面视图 + 5 个公共组件 + Pinia stores + API 客户端 + 构建/部署，覆盖 system_design §9 Phase 9 全部分配模块 |
| **技术选型** | Vue 3 + Vite + Pinia + Ant Design Vue + ECharts 与 system_design §1.1 完全一致 |
| **后端接口对齐** | 存在 3 处与已实现后端 API 的不一致（端点名称错误、响应字段不匹配、数据源遗漏） |
| **Store 设计完整性** | usePositionStore 缺少 actions 定义；useSignalStore 有孤立 action 无对应 state |
| **DoD 覆盖** | StatusBadge.vue 在范围内但未入 DoD 交付清单 |

**结论：存在 3 个 P2 级缺陷（实现时必然导致运行错误），4 个 P3 级问题（建议在实现前修复）。**

---

## 2. 问题清单

### 2.1 P2 级（必须修复）

#### D9-P2-01：`reports.ts` 端点表（§3.3）写 "GET/POST /reports"，与后端实际端点不符

**位置**：§3.3 API 端点覆盖表 `reports.ts` 行

**问题**：

设计文档 §3.3 写：
```
reports.ts  → GET/POST /reports, GET /reports/{id}
```

但后端实际端点为：
- `GET  /api/v1/reports`          — 列表（`list_reports`）
- `GET  /api/v1/reports/{id}`     — 详情（`get_report`）
- `POST /api/v1/reports/generate` — 生成自定义报告（`generate_report`）

`POST /reports` 端点在后端**不存在**；实际端点是 `POST /reports/generate`。

设计文档 §6.5（ReportsView）正确描述了 "POST /reports/generate"，但与 §3.3 的表格矛盾。实现者若按 §3.3 写 `reports.ts`，将调用错误端点，收到 404 或 405。

**修正方案**：将 §3.3 报告行改为：

```
reports.ts  → GET /reports, GET /reports/{id}, POST /reports/generate
```

---

#### D9-P2-02：`NavChart.vue` props 期望 `{date, value}[]`，但 `GET /performance/history` 返回 `{date, nav}[]`，字段名不匹配

**位置**：§7.1 NavChart.vue props 定义 vs 后端 `PerformanceService.get_history()` 返回格式

**问题**：

§7.1 定义 NavChart 接受：
```typescript
navSeries: { date: string, value: number }[]
benchmarkSeries: { date: string, value: number }[]
```

但后端 `GET /performance/history` 实际返回：
```json
{
  "nav_series": [{"date": "2026-01-03", "nav": 1.0}, ...],
  "benchmark_series": [{"date": "2026-01-03", "close": 4000.0}, ...]
}
```

- `nav_series` 每个元素用 `nav` 字段，不是 `value`
- `benchmark_series` 每个元素用 `close` 字段，不是 `value`

设计文档未描述从 API 响应到 NavChart props 的字段映射转换逻辑。若实现者直接将 `nav_series` 传给 NavChart，`value` 将为 `undefined`，图表无数据渲染。

**修正方案**：二选一：

选项 A（推荐）：NavChart 直接接受 `nav` 字段，修改 §7.1 props 定义：
```typescript
navSeries: { date: string, nav: number }[]
benchmarkSeries: { date: string, close: number }[]
```

选项 B：在 DashboardView 和 BacktestView 中描述映射转换：
```typescript
const navSeries = data.nav_series.map(d => ({ date: d.date, value: d.nav }))
```

无论哪种选项，§7.1 必须明确 props 类型与后端 API 字段的对应关系。

---

#### D9-P2-03：`DashboardView` 声称"日盈亏/累计收益"来自 `GET /account`，但 `AccountSummary` 无这些字段

**位置**：§6.1 DashboardView 数据来源说明

**问题**：

§6.1 写：
```
资产概览（总资产/日盈亏/累计收益/仓位水平）：GET /account
```

但后端 `AccountSummary` schema（Phase 6 交付）只有：
```python
id, name, account_type, broker, total_assets, cash, synced_at
```

- **`日盈亏`（daily_pnl）**：不在 AccountSummary，需从 `GET /performance/summary` 获取（但 performance 指标也没有 daily_pnl，SDD §12.1 基础指标亦不含此项）
- **`累计收益率`**：在 `GET /performance/summary` 的 `cumulative_return` 字段，不在 `/account`
- **`仓位水平`**：可由前端计算（`position_value = total_assets - cash`，`ratio = position_value / total_assets`），但设计未说明此派生逻辑

DashboardView 如按设计只调 `GET /account`，"日盈亏"和"累计收益"将永远显示为空。

**修正方案**：更新 §6.1 数据来源，明确多 API 聚合逻辑：

```
资产概览数据来源：
- 总资产 / 可用现金：GET /account → {total_assets, cash}
- 仓位水平：前端计算 (total_assets - cash) / total_assets
- 累计收益率：GET /performance/summary → {cumulative_return}
- 日盈亏：SDD §12.1 未定义此指标，V1.0 不展示或展示 N/A
```

同时对照 SDD §11.3（资产总览指标表）确认哪些指标在 V1.0 有数据支撑，哪些需降级处理。

---

### 2.2 P3 级（建议修复）

#### D9-P3-04：`usePositionStore`（§5.3）缺少 actions 定义，实现者无法推断调用端点

**位置**：§5.3 usePositionStore

**问题**：

`useSignalStore`（§5.2）和 `useBacktestStore`（§5.5）均提供完整的 state + actions 表，而 `usePositionStore` 只有 state 表，没有 actions。实现者需要自行推断：加载持仓调哪个端点、录入交易调哪个端点、入金/出金怎么触发。

这破坏了文档作为实现规格的完整性。

**修正建议**：补充 usePositionStore actions 表：

| 动作 | 说明 |
|------|------|
| `fetchAccount()` | GET /account |
| `fetchPositions()` | GET /positions |
| `fetchCashflows(params?)` | GET /account/cashflow |
| `syncAccount(account_id)` | POST /account/sync |
| `recordTrade(body)` | POST /account/trades |
| `deposit(body)` | POST /account/deposit |
| `withdraw(body)` | POST /account/withdraw |

---

#### D9-P3-05：`useSignalStore.fetchLineage(id)` 无对应 state 字段存储结果

**位置**：§5.2 useSignalStore

**问题**：

`useSignalStore` 中有 `fetchLineage(id)` 动作，但 state 表（`signals`、`history`、`loading`）中没有 `lineage` 或 `signalDetail` 字段。拉取的血缘数据无处存放，与 `fetchSignals → signals`、`updateStatus` 模式不一致。

SignalsView §6.2 中的"血缘信息"抽屉需要展示拉取结果，若无 state 字段只能用组件本地 ref，与 Pinia store 集中管理的设计意图矛盾。

**修正建议**：在 state 表中补入：

| 状态 | 说明 |
|------|------|
| `currentLineage` | 当前查看的信号血缘（`null` 表示未加载） |

或将 `fetchLineage` 从 store 中移除，改为在 SignalsView 组件本地使用 API 函数直接调用，并在 §6.2 说明此设计决策。

---

#### D9-P3-06：`StatusBadge.vue` 列入范围但未入 §11.1 交付清单（DoD 漏项）

**位置**：§11.1 实现层交付清单

**问题**：

`StatusBadge.vue` 在以下位置出现：
- §1.1 模块范围表：`AppLayout / NavChart / StatusBadge 等`
- §2.2 目录结构：`components/StatusBadge.vue`
- §7（公共组件节）：在 §7.1~§7.4 中未单独展开，但归属于"等"内

§11.1 DoD 逐条列出：AppLayout.vue、NavChart.vue、SignalCard.vue、EmptyState.vue、DisclaimerBanner.vue，唯独缺 `StatusBadge.vue`，导致该组件不在验收范围内，交付风险（可能被遗漏实现）。

**修正建议**：在 §11.1 DoD 中补入：
```
- [ ] frontend/src/components/StatusBadge.vue
```

同时建议在 §7 增加 StatusBadge 的独立说明（props 接受哪些状态值、对应的颜色/文案）。

---

#### D9-P3-07：`BacktestView` 的 `daily_nav` 为 dict 格式，NavChart 期望 array 格式，设计未描述转换

**位置**：§6.6 BacktestView 结果展示区 + §5.5 useBacktestStore

**问题**：

后端 `GET /backtest/{id}/result` 返回：
```json
{
  "daily_nav": {"2023-01-03": 1.0, "2023-01-04": 1.0},
  "performance": {...},
  "disclaimer": "..."
}
```

`daily_nav` 是一个 JSON 对象（`{date_str: nav_value}` 字典），而 NavChart 期望 `{date, value/nav}[]` 数组（见 D9-P2-02 修正后格式）。

§5.5 useBacktestStore 的 `result` 状态只注了"含 daily_nav、performance"，未说明是否需要在 store 中做格式转换（dict → array），这将导致 `BacktestView` 实现者各自处理，行为不一致。

与 D9-P2-02 类似但来源不同：两处 NavChart 调用（DashboardView 和 BacktestView）的数据源格式均需转换，且转换逻辑各不相同（`performance/history` 是数组需字段重命名；`backtest/result` 是 dict 需转成数组）。

**修正建议**：在 §5.5 useBacktestStore 中说明 `startPolling()` 内拉取 result 后的处理：

```typescript
// startPolling 内 SUCCESS 后
const resultData = await getBacktestResult(taskId)
this.result = {
  performance: resultData.performance,
  disclaimer: resultData.disclaimer,
  // 将 dict 转为 {date, nav}[] 数组，供 NavChart 使用
  navSeries: Object.entries(resultData.daily_nav)
    .map(([date, nav]) => ({ date, nav }))
    .sort((a, b) => a.date.localeCompare(b.date)),
}
```

并在 §6.6 说明 NavChart 此处传 `navSeries`（无基准对比线，因回测结果暂不含 HS300 日序列）。

---

## 3. 范围与设计一致性核查

### 3.1 system_design §9 Phase 9 模块覆盖

| system_design §9 分配 | 设计文档覆盖 | 状态 |
|----------------------|------------|------|
| Vue 3 仪表盘（DashboardView） | §6.1 ✓ | ✓ |
| 信号列表（SignalsView） | §6.2 ✓ | ✓ |
| 持仓管理（PositionsView） | §6.3 ✓ | ✓ |
| 因子监控面板（FactorQualityView） | §6.4 ✓ | ✓ |
| 报告中心（ReportsView） | §6.5 ✓ | ✓ |
| 回测入口（BacktestView） | §6.6 ✓ | ✓ |
| 设置页（SettingsView，含配置历史） | §6.7 ✓ | ✓ |
| Pinia stores | §5.1~5.5 ✓ | ✓ |
| API 客户端 | §3.1~3.3 ✓ | ✓（有 D9-P2-01 待修）|

无孤儿模块，无孤儿端点（Phase 9 仅消费后端 API，不新增后端端点）。

### 3.2 SDD 引用章节核查

| 引用 | 设计内容 | 是否在 SDD §11~§14 范围内 |
|------|----------|--------------------------|
| SDD §11.2 交易录入 | SignalsView 录入交易 Modal / PositionsView Tab2 | ✓ |
| SDD §11.3 资产总览 | DashboardView 资产卡片（有 D9-P2-03 待修） | 部分 ✓ |
| SDD §11.4 资金流水 | PositionsView Tab3 | ✓ |
| SDD §11.5 T+1 提示 | SignalsView 买入信号顶部警告条（`t1_warning` 字段存在于 Signal schema） | ✓ |
| SDD §12.1 基础绩效指标 | PerformanceView 通过 summary/history API 展示 | ✓ |
| SDD §13（通知） | Phase 9 不含通知，推迟 Phase 10 ✓（§1.2 未明确说明但 Phase 10 文档中） | ✓ |
| SDD §14（设置与配置） | SettingsView §6.7 ✓ | ✓ |

### 3.3 降级说明完整性

| 降级项 | 位置 | 说明完整 |
|--------|------|----------|
| 多账户不展示 | §1.2 | ✓ |
| 暗色模式推迟 V1.5 | §1.2 | ✓ |
| i18n 推迟 V1.5 | §1.2 | ✓ |
| WebSocket 改轮询 | §1.2 + §5.5 + §6.6 | ✓ |
| adj_prices 降级导致 daily_nav 全为 1.0 | §6.6 | ✓ |

---

## 4. DoD 核查汇总

| DoD 项 | 验收内容 | 状态 |
|--------|----------|------|
| package.json | Vue3 + Vite + TS + Ant Design Vue + ECharts + Pinia + Axios | 待实现 |
| vite.config.ts | alias @/ + dev proxy | 待实现 |
| src/api/client.ts | Axios + JWT 拦截 + 401 刷新 | 待实现 |
| src/api/ 各模块 | 9 个模块（auth/signals/positions/market/performance/backtest/factorQuality/reports/settings）| 待实现；reports.ts 须引用 D9-P2-01 修正端点 |
| src/router/index.ts | 8 路由 + 守卫 | 待实现 |
| src/stores/ | 5 个 store | 待实现；usePositionStore 须补 actions（D9-P3-04）|
| LoginView.vue | 登录表单 | 待实现 |
| DashboardView.vue | 市场状态 + 资产卡片 + NavChart + 信号摘要 | 待实现；D9-P2-03 须多 API 聚合 |
| SignalsView.vue | 今日/历史 Tab + 详情 Drawer + 录入 Modal | 待实现 |
| PositionsView.vue | 持仓/交易/资金流水 Tab | 待实现 |
| FactorQualityView.vue | 表格 + IC 折线图 | 待实现 |
| ReportsView.vue | 列表 + 详情 + 生成 | 待实现 |
| BacktestView.vue | 参数表单 + 轮询 + 结果图表 + 声明 | 待实现；D9-P3-07 须转换 dict→array |
| SettingsView.vue | 配置编辑 + 历史回退 Tab | 待实现 |
| AppLayout.vue | 侧边栏 + 顶导 | 待实现 |
| NavChart.vue | ECharts 净值曲线 | 待实现；D9-P2-02 须修正 props 类型 |
| SignalCard.vue | 信号卡片 | 待实现 |
| **StatusBadge.vue** | 状态标签 | 待实现；**须补入 DoD（D9-P3-06）** |
| EmptyState.vue | 空状态引导 | 待实现 |
| DisclaimerBanner.vue | 回测声明 | 待实现 |
| Dockerfile | 多阶段构建 + Nginx | 待实现 |
| nginx.conf | SPA 路由 + API 代理 | 待实现 |
| __tests__/stores/auth.test.ts | login/refresh/logout 逻辑 | 待实现 |
| __tests__/stores/signals.test.ts | fetchSignals/updateStatus mock | 待实现 |
| __tests__/api/client.test.ts | 拦截器 header 注入 | 待实现 |
| npm run build 0 error | TS 编译 + Vite 打包 | 待实现 |
| npm run test:unit 全部通过 | Vitest | 待实现 |
| 8 页面手动验证无 console error | 开发服务器 | 待实现 |
| docker compose up frontend → 200 | 容器验证 | 待实现 |

---

## 5. 评审总结

| 编号 | 级别 | 位置 | 标题 | 状态 |
|------|------|------|------|------|
| D9-P2-01 | **P2** | §3.3 reports.ts 端点表 | "POST /reports" 不存在，实际端点为 `POST /reports/generate`，与 §6.5 矛盾 | **已修复** |
| D9-P2-02 | **P2** | §7.1 NavChart.vue props | props 用 `value` 字段，后端 nav_series 返回 `nav` / benchmark_series 返回 `close`，字段不匹配，设计未描述转换 | **已修复** |
| D9-P2-03 | **P2** | §6.1 DashboardView 数据来源 | "日盈亏/累计收益"标注来自 `GET /account`，但 AccountSummary 不含这两项，需从 `/performance/summary` 补充或降级展示 | **已修复** |
| D9-P3-04 | P3 | §5.3 usePositionStore | 缺少 actions 表，持仓/交易/资金流水操作无规格可循 | **已修复** |
| D9-P3-05 | P3 | §5.2 useSignalStore | `fetchLineage(id)` 无对应 state 字段存储结果，血缘数据去向不明 | **已修复** |
| D9-P3-06 | P3 | §11.1 DoD 交付清单 | `StatusBadge.vue` 在范围内但未出现在 DoD，有漏交风险 | **已修复** |
| D9-P3-07 | P3 | §6.6 BacktestView + §5.5 useBacktestStore | `daily_nav` 从 API 获取为 dict，NavChart 需 array，设计未描述 dict→array 转换逻辑 | **已修复** |

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-15 | 初版设计评审，共 7 项问题（P2×3、P3×4） |
| v1.1 | 2026-04-15 | 7 项问题全部修复，评审关闭 |
