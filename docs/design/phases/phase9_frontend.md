# Phase 9：前端（Vue 3 仪表盘）

> **版本：** v1.2
> **日期：** 2026-04-16
> **依据文档：** QuantPilot_SDD.md §11~§14；system_design.md §1.1, §2.1, §3, §9

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-15 | Phase 9 设计文档初版 |
| v1.1 | 2026-04-15 | 设计评审修复（D9-P2-01~03、D9-P3-04~07）：reports.ts 端点修正；NavChart props 字段对齐后端（`nav`/`close`）；DashboardView 多 API 聚合说明 + 日盈亏降级；usePositionStore 补 actions 表；useSignalStore 补 `currentLineage` state；DoD 补 StatusBadge.vue；useBacktestStore 补 dict→array 转换逻辑 |
| v1.2 | 2026-04-16 | 代码评审 C-01~C-08 全部修复（见 docs/reviews/phase9_code_review_2026-04-16.md）；DashboardView 补净值曲线期间选择器（1M/3M/6M/1Y/全部）；seed_demo_data.py 补 IndexHistory HS300 30 日基准数据；API 层补齐 account_id 必填参数；useAuthStore 补 username 状态；useSignalStore 补 fetchHistory 动作；usePositionStore 补 addPosition 动作 |

---

## 1. 范围声明

### 1.1 本 Phase 纳入模块（system_design §9 Phase 9）

| 模块 | 路径 | 说明 |
|------|------|------|
| 项目骨架 | `frontend/` | Vite + Vue 3 + TypeScript 初始化 |
| API 客户端 | `frontend/src/api/` | Axios 封装 + 请求拦截（JWT 注入、401 刷新） |
| 路由 | `frontend/src/router/` | Vue Router 4，含路由守卫（未登录跳转 /login） |
| 状态管理 | `frontend/src/stores/` | Pinia stores（auth / signals / positions / market / backtest） |
| 登录页 | `LoginView` | 用户名+密码表单，登录成功写入 token |
| 总览仪表盘 | `DashboardView` | 市场状态、资产总览、净值曲线、今日信号摘要 |
| 信号列表页 | `SignalsView` | 当日信号 + 历史信号 + 信号详情（含评分构成、血缘） |
| 持仓管理页 | `PositionsView` | 持仓明细、交易录入、资金流水 3 个 Tab |
| 因子监控面板 | `FactorQualityView` | 因子 IC 历史表格 + IC 折线图 |
| 报告中心 | `ReportsView` | 报告列表 + 生成自定义报告 + 报告详情 |
| 回测入口 | `BacktestView` | 参数表单 + 任务状态轮询 + 结果净值曲线 |
| 设置页 | `SettingsView` | 用户配置编辑 + 配置历史回退 |
| 公共组件 | `frontend/src/components/` | AppLayout / NavChart / StatusBadge 等 |
| 单元测试 | `frontend/src/` | Vitest：Pinia stores + API 客户端 mock |

### 1.2 显式排除

- **多账户支持**：V1.0 单账户，前端不显示账户选择器。【降级说明】V1.5 时在账户 store 中扩展。
- **暗色模式 / 主题切换**：V1.5 功能，不实现。
- **国际化（i18n）**：V1.0 仅中文，不引入 vue-i18n。
- **E2E 浏览器自动化**：不引入 Playwright/Cypress；UI 正确性通过开发服务器手动验证 + 冒烟接口测试。
- **回测 WebSocket 进度推送**：后端已实现 `WS /backtest/{id}/progress`，V1.0 前端改用轮询（每 3 秒 GET status），WebSocket 客户端集成推迟至 V1.5。【降级说明】轮询频率对长时间回测（数百交易日）足够，无实时性要求。

---

## 2. 技术栈与项目结构

### 2.1 技术选型（system_design §1.1）

| 层次 | 技术 | 版本 |
|------|------|------|
| 框架 | Vue 3 + TypeScript | ^3.4 / ^5.4 |
| 构建工具 | Vite | ^5.2 |
| UI 组件 | Ant Design Vue | ^4.2 |
| 图表 | ECharts + vue-echarts | ^5.5 / ^7.0 |
| 状态管理 | Pinia | ^2.1 |
| 路由 | Vue Router | ^4.3 |
| HTTP 客户端 | Axios | ^1.7 |
| 测试 | Vitest + @vue/test-utils | ^1.6 / ^2.4 |
| 代码规范 | ESLint + Prettier | — |

### 2.2 目录结构

```
frontend/
├── src/
│   ├── api/
│   │   ├── client.ts          # Axios 实例 + 拦截器
│   │   ├── auth.ts
│   │   ├── signals.ts
│   │   ├── positions.ts
│   │   ├── market.ts
│   │   ├── performance.ts
│   │   ├── backtest.ts
│   │   ├── factorQuality.ts
│   │   ├── reports.ts
│   │   └── settings.ts
│   ├── stores/
│   │   ├── auth.ts            # useAuthStore
│   │   ├── signals.ts         # useSignalStore
│   │   ├── positions.ts       # usePositionStore
│   │   ├── market.ts          # useMarketStore
│   │   └── backtest.ts        # useBacktestStore
│   ├── views/
│   │   ├── LoginView.vue
│   │   ├── DashboardView.vue
│   │   ├── SignalsView.vue
│   │   ├── PositionsView.vue
│   │   ├── FactorQualityView.vue
│   │   ├── ReportsView.vue
│   │   ├── BacktestView.vue
│   │   └── SettingsView.vue
│   ├── components/
│   │   ├── AppLayout.vue      # 侧边栏 + 顶部导航 + 主内容区
│   │   ├── NavChart.vue       # ECharts 净值曲线（含基准对比）
│   │   ├── SignalCard.vue     # 信号卡片（含评分构成折叠展示）
│   │   ├── StatusBadge.vue    # 通用状态标签（市场状态/任务状态）
│   │   ├── EmptyState.vue     # 空状态引导（SDD §15.1）
│   │   └── DisclaimerBanner.vue  # 回测局限性声明展示
│   ├── router/
│   │   └── index.ts
│   ├── types/
│   │   └── api.ts             # 后端响应类型定义
│   ├── utils/
│   │   └── format.ts          # 数字/日期格式化工具
│   ├── App.vue
│   └── main.ts
├── __tests__/
│   ├── stores/
│   │   ├── auth.test.ts
│   │   └── signals.test.ts
│   └── api/
│       └── client.test.ts
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── .eslintrc.cjs
└── Dockerfile
```

---

## 3. API 客户端层

### 3.1 Axios 实例（`src/api/client.ts`）

```typescript
import axios from 'axios'
import { useAuthStore } from '@/stores/auth'

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
  timeout: 15000,
})

// 请求拦截：注入 Authorization
client.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

// 响应拦截：401 → 刷新 token 或跳转登录
client.interceptors.response.use(
  (res) => res,
  async (err) => {
    if (err.response?.status === 401) {
      const auth = useAuthStore()
      const refreshed = await auth.tryRefresh()
      if (refreshed) return client.request(err.config)
      auth.logout()
      window.location.href = '/login'
    }
    return Promise.reject(err)
  },
)

export default client
```

### 3.2 统一响应格式

后端统一格式 `{"code": N, "data": ..., "msg": "..."}` — API 客户端每个方法返回 `data` 字段，错误时 throw。

```typescript
// 示例：src/api/signals.ts
export async function getSignals(params?: SignalListParams) {
  const res = await client.get('/api/v1/signals', { params })
  return res.data.data as Signal[]
}
```

### 3.3 API 端点覆盖

| 模块文件 | 覆盖端点 |
|----------|---------|
| `auth.ts` | POST /auth/login, POST /auth/refresh |
| `signals.ts` | GET /signals, /signals/history, PATCH /signals/{id}/status, GET /signals/{id}/lineage |
| `market.ts` | GET /market/state, /market/state/history, /market/pool |
| `positions.ts` | GET/POST /positions, PATCH /positions/{id}, GET /account, POST /account/sync, POST /account/trades, POST /account/deposit, POST /account/withdraw, GET /account/cashflow |
| `performance.ts` | GET /performance/summary, /history, /attribution, /behavior |
| `backtest.ts` | POST /backtest/run, GET /backtest/{id}/status, /result |
| `factorQuality.ts` | GET /factor-quality, /factor-quality/history |
| `reports.ts` | GET /reports, GET /reports/{id}, POST /reports/generate |
| `settings.ts` | GET/PUT /settings, GET /settings/config-history, POST /settings/config-history/{id}/revert |

---

## 4. 路由设计（`src/router/index.ts`）

```typescript
const routes = [
  { path: '/login', component: LoginView, meta: { public: true } },
  {
    path: '/',
    component: AppLayout,
    children: [
      { path: '',       redirect: '/dashboard' },
      { path: 'dashboard',     component: DashboardView },
      { path: 'signals',       component: SignalsView },
      { path: 'positions',     component: PositionsView },
      { path: 'factors',       component: FactorQualityView },
      { path: 'reports',       component: ReportsView },
      { path: 'backtest',      component: BacktestView },
      { path: 'settings',      component: SettingsView },
    ],
  },
]
```

**路由守卫**：所有非 `meta.public` 路由检查 `useAuthStore().isLoggedIn`，否则跳转 `/login`。

---

## 5. 状态管理（Pinia Stores）

### 5.1 useAuthStore

| 状态 | 类型 | 说明 |
|------|------|------|
| `token` | `string \| null` | JWT access token，持久化到 localStorage |
| `refreshToken` | `string \| null` | refresh token |
| `username` | `string \| null` | 当前登录用户名，login 时写入并持久化到 localStorage，用于 AppLayout 顶部展示 |

| 动作 | 说明 |
|------|------|
| `login(username, password)` | 调用 POST /auth/login，写入 token 和 username |
| `tryRefresh()` | 调用 POST /auth/refresh，成功返回 true |
| `logout()` | 清空 token / username，跳转 /login |

### 5.2 useSignalStore

| 状态 | 说明 |
|------|------|
| `signals` | 当日有效信号列表 |
| `history` | 历史信号（分页） |
| `loading` | 加载状态 |
| `currentLineage` | 当前查看的信号血缘（`null` 表示未加载） |

| 动作 | 说明 |
|------|------|
| `fetchSignals()` | 拉取当日信号 |
| `fetchHistory(params?)` | 拉取历史信号（支持 status / signal_type 过滤） |
| `updateStatus(id, status)` | PATCH 信号状态 |
| `fetchLineage(id)` | 拉取信号血缘，结果写入 `currentLineage` |

### 5.3 usePositionStore

| 状态 | 说明 |
|------|------|
| `account` | AccountSummary（总资产/现金/盈亏等） |
| `positions` | 持仓列表 |
| `cashflows` | 资金流水 |

| 动作 | 说明 |
|------|------|
| `fetchAccount()` | GET /account |
| `fetchPositions()` | GET /positions?account_id=1 |
| `fetchCashflows(params?)` | GET /account/cashflow?account_id=1（可选日期范围参数） |
| `syncAccount()` | POST /account/sync?account_id=1 |
| `recordTrade(body)` | POST /account/trades（body 含 account_id） |
| `addPosition(body)` | POST /positions（body 含 account_id），新增持仓后刷新列表 |
| `deposit(body)` | POST /account/deposit（body 含 account_id） |
| `withdraw(body)` | POST /account/withdraw（body 含 account_id） |

注：`account_id` 为后端必填参数，V1.0 单账户固定为 1（`store.account?.id ?? 1`）。

### 5.4 useMarketStore

| 状态 | 说明 |
|------|------|
| `currentState` | 当前市场状态（UPTREND/OSCILLATION/DOWNTREND） |
| `stateHistory` | 市场状态历史（用于图表） |

### 5.5 useBacktestStore

| 状态 | 说明 |
|------|------|
| `taskId` | 当前任务 ID |
| `status` | PENDING/RUNNING/SUCCESS/FAILED |
| `result` | BacktestResult（含 daily_nav、performance） |
| `pollTimer` | setInterval 句柄（轮询） |

| 动作 | 说明 |
|------|------|
| `submitRun(params)` | POST /backtest/run，启动轮询 |
| `startPolling()` | 每 3 秒 GET status；SUCCESS 时停止并拉取 result，FAILED 时停止并记录错误 |
| `stopPolling()` | 清除 timer |

`startPolling()` 拉取 result 后，将 `daily_nav`（API 返回 dict `{date_str: nav_value}`）转为 `{date, nav}[]` 数组供 NavChart 使用：

```typescript
// 在 startPolling() SUCCESS 分支内
const resultData = await getBacktestResult(taskId)
this.result = {
  performance: resultData.performance,
  disclaimer: resultData.disclaimer,
  // dict → array 转换，按日期升序排列
  navSeries: Object.entries(resultData.daily_nav)
    .map(([date, nav]) => ({ date, nav: nav as number }))
    .sort((a, b) => a.date.localeCompare(b.date)),
}
```

`result.navSeries` 传给 NavChart 时不含 `benchmarkSeries`（回测结果不含 HS300 日序列）。

---

## 6. 页面设计

### 6.1 DashboardView（总览仪表盘）

**布局：** 视图根节点顶部接入 `DisclaimerBanner`（V1.0 整改 Batch 1 — B1-3，文案聚焦"不构成投资建议、不接受委托、不构成投顾服务"，与 SignalsView/ReportsView 同源），下方依次为市场状态栏 + 资产概览卡片行 + 净值曲线 + 当日信号摘要列表。

**数据来源（多 API 聚合）：**
- 市场状态：GET /market/state
- 总资产 / 可用现金：GET /account → `{total_assets, cash}`
- 仓位水平：前端计算 `(total_assets - cash) / total_assets`
- 累计收益率：GET /performance/summary → `cumulative_return`
- 日盈亏：SDD §12.1 基础指标未定义此项，V1.0 不展示（【降级说明】面板对应位置显示"N/A"）
- 净值曲线：GET /performance/history?limit=N → `nav_series`（字段 `nav`）+ `benchmark_series`（字段 `value`，后端已归一化）→ NavChart
- 当日信号摘要：GET /signals（取前 5 条）

**净值曲线期间选择器：** 净值曲线卡片右上角放置 Radio Group，选项映射到 `limit` 参数（交易日数）：

| 标签 | limit 值 |
|------|---------|
| 1个月 | 21 |
| 3个月 | 63（默认） |
| 6个月 | 126 |
| 1年 | 252 |
| 全部 | 9999 |

切换时以 `chartLoading` 独立加载状态控制 Spin，避免影响整页 loading。`benchmark_series` 数据点少于 2 时不绘制基准线（单点无法成线）。

**市场状态展示（SDD §6.2）：**

| 状态 | 颜色标签 | 说明文案 |
|------|----------|---------|
| UPTREND | 绿色 | 上涨趋势 |
| OSCILLATION | 蓝色 | 震荡市 |
| DOWNTREND | 红色 | 下跌趋势（降低仓位上限） |

### 6.2 SignalsView（信号列表）

视图根节点顶部接入 `DisclaimerBanner`（V1.0 整改 Batch 1 — B1-3，文案强调"信号为算法量化结果、不构成投资建议、是否依据信号交易由用户自行决策并承担全部后果"），位于 a-tabs 上方。

**两个 Tab：**
- **今日信号**：表格展示 ts_code/名称/类型(BUY/SELL)/评分/信号强度/建议仓位/状态(NEW/VIEWED/ACTED/EXPIRED)
- **历史信号**：加类型筛选 + 状态筛选 + 「查询」按钮；首次切换至本 Tab 时通过 `watch(activeTab)` 自动触发默认查询（避免空表格误导）

**信号详情抽屉（Drawer）：**
- 点击信号行展开右侧详情抽屉
- 评分构成：V1.0 用 `score_snapshot` 键值对文字展示各策略得分（V1.5 升级为水平条形图）
- 建议操作区：「录入交易」按钮（弹出交易录入表单，预填 ts_code + 建议价格）
- 状态更新：「标记已操作」（PATCH status=ACTED）
- 血缘信息：pipeline_run.started_at + score_snapshot 键值对（策略名 → 得分）

**T+1 提示**（SDD §11.5）：买入信号卡片顶部展示⚠️警告条。

### 6.3 PositionsView（持仓管理）

**三个 Tab：**

**Tab 1 — 持仓明细：**
- 持仓表格：代码/名称/数量/成本价/当前价/盈亏额/盈亏率/持仓天数/仓位占比
- 顶部：总资产/可用现金/浮动盈亏/仓位水平 4 张卡片
- 「同步盯市」按钮 → POST /account/sync
- 手动录入持仓（Modal 表单）

**Tab 2 — 交易记录：**
- 表格：日期/方向/代码/数量/价格/金额/状态
- 「录入交易」按钮（Modal：BUY/SELL + 代码 + 价格 + 数量）

**Tab 3 — 资金流水：**
- 表格：日期/类型/金额/备注
- 日期范围筛选
- 「入金」/「出金」快捷操作

### 6.4 FactorQualityView（因子监控面板）

**布局：** 左侧因子 IC 历史表格 + 右侧 IC 折线图（ECharts）

**数据来源：**
- GET /factor-quality → 最新一批因子 IC 数据（表格）
- GET /factor-quality/history → 历史 IC 时序（折线图）

**告警展示：** `is_degraded=True` 时行标红 + Tooltip 说明"因子 IC 连续 3 月为负，建议审查策略权重"。

### 6.5 ReportsView（报告中心）

**布局：** 视图根节点顶部接入 `DisclaimerBanner`（V1.0 整改 Batch 1 — B1-3，文案聚焦"报告内容仅作决策辅助参考、不构成投资建议、不可作为绝对收益预期"），下方为报告列表 + 报告详情面板。

**操作：**
- 报告列表（按 generated_at 降序）：周报/月报/自定义报告 Tab
- 点击报告 → 右侧面板优先展示 `summary`（可读文字摘要），折叠展示 `content` 原始 JSON（供高级用户参考）。注：`content` 为后端生成的结构化 JSON，非 Markdown 格式；【降级说明】V1.0 不引入 Markdown 渲染库，直接展示 summary 文本
- 「生成自定义报告」按钮 → 弹出日期范围选择器 → POST /reports/generate

### 6.6 BacktestView（回测入口）

**两区域：**

**左侧参数表单：**
- 开始日期 / 结束日期（DatePicker）
- 初始资金（InputNumber，默认 1,000,000）
- 佣金率 / 印花税率 / 滑点（可折叠的高级参数，默认值来自用户配置）
- 「提交回测」按钮

**右侧结果区：**
- 状态卡片（PENDING → 转圈 / RUNNING → 进度文案 / SUCCESS/FAILED）
- **轮询方式**：提交后每 3 秒 GET /backtest/{id}/status，SUCCESS 后拉取 result
- 成功后展示：绩效指标卡片组（累计收益/最大回撤/夏普比率/胜率/盈亏比，共5格；null 值展示"N/A"）+ 净值曲线（NavChart，传 `result.navSeries`，无基准对比线）
- `DisclaimerBanner`：展示后端返回的 `disclaimer` 文本（SDD §7.7.4）
- `daily_nav` dict→array 转换在 useBacktestStore.startPolling() 内完成（见 §5.5）

**V1.0 整改 Batch 1 — B1-2：** 视图根节点顶部固定接入 `BacktestLimitationsBanner`（红色 a-alert，默认展开），列出 V1.0 4 项 P0 局限（T+1 撮合违反 / quotes 字段缺失 / pe_pb/index 空 DF / 不调 RiskChecker）。该 banner 与提交按钮**位置无关**，确保用户在填写参数前已读取局限，并与 SDD §7.7.4 / `engine/backtest/report.py` DISCLAIMER 措辞同步（修复条件：V1.5 回测引擎重构完成后该 banner 由设计评审决定是否撤销/缩减）。

**【降级说明】** V1.0 回测结果的 `daily_nav` 可能全为 1.0（adj_prices 降级为空数据），界面展示"历史价格数据暂不可用，净值曲线仅供结构验证"提示。

### 6.7 SettingsView（设置页）

**两个 Tab：**

**Tab 1 — 配置编辑：**
- 从 GET /settings 加载配置键值对
- 按配置分组展示（交易参数 / 风控参数 / 通知参数）
- 表格内联编辑 + 「保存」→ PUT /settings

**Tab 2 — 配置历史：**
- 表格：变更时间/配置键/变更前→变更后/备注
- 「回退」按钮 → POST /settings/config-history/{id}/revert
- 回退后刷新 Tab 1 配置

### 6.8 LoginView（登录页，V1.0 整改 Batch 2 — B2-4 新增章节）

**两区域：**
- 表单区：用户名 + 密码 + 登录按钮（`@press-enter` 支持回车提交，401 → message.error）
- **合规脚注（B2-4 新增）：** a-card 底部加 `.login-footer` 区块，文案声明"本系统为个人量化交易决策辅助工具，不提供投资建议、不接受委托、不构成投顾服务"，确保用户在进入系统前已建立投顾边界认知（与 SignalsView/DashboardView/ReportsView 顶部 DisclaimerBanner 形成多层合规链条）。

**登录后跳转：** 调 `getSetupStatus()`，未完成 onboarding → `/onboarding`，否则 → `/dashboard`。

---

## 7. 公共组件

### 7.1 NavChart.vue

ECharts 折线图，props 类型与后端 API 字段对齐：

```typescript
// props
navSeries: { date: string, nav: number }[]          // 来自 performance/history.nav_series
benchmarkSeries?: { date: string, value: number }[] // 来自 performance/history.benchmark_series（可选）
                                                    // 后端已归一化为相对首日收盘价的倍数，直接使用 value 字段渲染
```

渲染：
- Y 轴：净值（相对 1.0）；若含 `benchmarkSeries`，则归一化为相对首日收盘价的倍数
- 两条折线：策略净值（蓝，宽 2px）+ 基准沪深 300（ECharts 自动配色，宽 1.5px，可选）
- Tooltip：悬停显示日期 + 净值 + 超额收益（仅有基准时计算）

**回测结果来源特殊处理（D9-P3-07 相关）：** BacktestView 传入的 `navSeries` 已在 store 中由 dict 转为 `{date, nav}[]` 数组（见 §5.5），无 `benchmarkSeries`（回测结果不含 HS300 日序列）。

注：`benchmarkSeries` 的 `value` 字段已由后端归一化为相对首日收盘价的倍数（非原始价格），`close` 字段名为设计文档历史版本残留错误，代码实现使用 `value`。

### 7.2 AppLayout.vue

侧边栏导航，含菜单项：
- 总览 / 信号 / 持仓 / 因子监控 / 报告 / 回测 / 设置
- 顶部 Sider：系统标题 "QuantPilot"（折叠时显示 "QP"）
- 顶部 Header：右侧显示 `useAuthStore().username`（login 时写入 localStorage，刷新后不丢失）+ 「退出」按钮

### 7.3 EmptyState.vue

无数据时展示引导文案（SDD §15.1 易用性），接受 `title` 和 `description` props。

### 7.4 StatusBadge.vue

通用状态标签，用于市场状态（MarketStateEnum）和任务状态（BacktestTask.status）展示。

| prop `status` 值 | 颜色 | 文案 |
|-----------------|------|------|
| `UPTREND` | 绿色 | 上涨趋势 |
| `OSCILLATION` | 蓝色 | 震荡市 |
| `DOWNTREND` | 红色 | 下跌趋势 |
| `PENDING` | 默认灰 | 等待中 |
| `RUNNING` | 蓝色处理中 | 运行中 |
| `SUCCESS` | 绿色 | 成功 |
| `FAILED` | 红色 | 失败 |

### 7.5 DisclaimerBanner.vue

展示回测声明文本（黄色警告条），点击展开查看全文。SignalsView / DashboardView / ReportsView 的顶部固定免责声明也复用此组件（V1.0 整改 Batch 1 — B1-3，传入针对各视图的不同文案，详见 §6.1/6.2/6.5）。

### 7.6 BacktestLimitationsBanner.vue

V1.0 整改 Batch 1 — B1-2 新增。回测视图专用红色局限说明 banner（a-alert type=error，show-icon），列出 V1.0 4 项 P0 回测引擎局限：T+1 撮合违反、quotes 字段缺失（涨停/停牌/退市未排除）、pe_pb_history/index_adj_prices 空 DF 降级、RiskChecker 不参与。组件无 props，所有文案硬编码，确保所有用户看到的局限说明完全一致。默认展开，可手动收起；与 SDD §7.7.4 / `engine/backtest/report.py:DISCLAIMER` 内容同源同步。仅在 BacktestView 接入；V1.5 回测引擎重构完成后由设计评审决定是否撤销或缩减。

---

## 8. 测试策略

### 8.1 单元测试（Vitest）

**测试文件：**
- `__tests__/stores/auth.test.ts`：login 成功写 token、401 刷新逻辑、logout 清空状态
- `__tests__/stores/signals.test.ts`：fetchSignals mock axios、updateStatus 调用正确端点
- `__tests__/api/client.test.ts`：请求拦截注入 Authorization header、响应拦截 401 触发 refresh

**无需 DB**，全部 mock axios。

### 8.2 构建验证

```bash
cd frontend
npm run build   # vite build，输出 dist/
```

构建成功（0 error）为交付门槛，不满足不得进入 Phase 10。

### 8.3 开发服务器手动验证

```bash
npm run dev
# 访问 http://localhost:5173
```

逐页手动检查：登录 → 总览 → 信号 → 持仓 → 因子 → 报告 → 回测 → 设置，确认无 JS console error。

### 8.4 冒烟测试

在 `tests/smoke/test_api_live.py` 中**不新增**前端 HTTP 测试（前端为 Nginx 静态文件，与后端 API 测试框架分离）。Docker 容器启动后验证 `http://localhost:80` 返回 200（HTML）即可，由 Phase 10 的全链路收尾测试统一覆盖。

---

## 9. 环境变量

```env
# frontend/.env.development
VITE_API_BASE_URL=http://localhost:8000

# frontend/.env.production
VITE_API_BASE_URL=http://backend:8000  # Docker 内部网络
```

---

## 10. Dockerfile 更新

```dockerfile
# Stage 1: 构建
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Nginx 服务
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
# SPA 路由：所有 404 回退到 index.html（Vue Router history 模式）
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

`nginx.conf`（关键配置）：
```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;
    }
    location /api/ {
        proxy_pass http://backend:8000;
    }
}
```

---

## 11. 交付清单（DoD）

### 11.1 实现层

- [x] `frontend/package.json`（Vue 3 + Vite + TS + Ant Design Vue + ECharts + Pinia + Axios）
- [x] `frontend/vite.config.ts`（alias `@/` → `src/`；dev proxy `/api` → backend）
- [x] `frontend/src/api/client.ts`（Axios 实例 + JWT 拦截 + 401 刷新）
- [x] `frontend/src/api/` 各模块（auth / signals / positions / market / performance / backtest / factorQuality / reports / settings）
- [x] `frontend/src/router/index.ts`（8 条路由 + 路由守卫）
- [x] `frontend/src/stores/`（useAuthStore / useSignalStore / usePositionStore / useMarketStore / useBacktestStore）
- [x] `frontend/src/views/LoginView.vue`
- [x] `frontend/src/views/DashboardView.vue`（市场状态 + 资产卡片 + NavChart + 信号摘要 + 期间选择器）
- [x] `frontend/src/views/SignalsView.vue`（今日/历史 Tab + 详情 Drawer + 录入交易 Modal + 历史自动加载 + 血缘 score_snapshot）
- [x] `frontend/src/views/PositionsView.vue`（持仓/交易/资金流水 Tab + 手动录入持仓 Modal）
- [x] `frontend/src/views/FactorQualityView.vue`（表格 + IC 折线图）
- [x] `frontend/src/views/ReportsView.vue`（报告列表 + 详情 + 生成）
- [x] `frontend/src/views/BacktestView.vue`（参数表单 + 轮询状态 + 结果图表 + 声明）
- [x] `frontend/src/views/SettingsView.vue`（配置编辑 + 历史回退 Tab）
- [x] `frontend/src/components/AppLayout.vue`（侧边栏 + 顶导）
- [x] `frontend/src/components/NavChart.vue`（ECharts 净值曲线）
- [x] `frontend/src/components/SignalCard.vue`
- [x] `frontend/src/components/StatusBadge.vue`
- [x] `frontend/src/components/EmptyState.vue`
- [x] `frontend/src/components/DisclaimerBanner.vue`
- [x] `frontend/Dockerfile`（多阶段构建 + Nginx）
- [x] `frontend/nginx.conf`（SPA 路由 + API 反向代理）

### 11.2 测试层

- [x] `frontend/__tests__/stores/auth.test.ts`（login / refresh / logout 逻辑）
- [x] `frontend/__tests__/stores/signals.test.ts`（fetchSignals / updateStatus mock 测试）
- [x] `frontend/__tests__/api/client.test.ts`（拦截器 header 注入）
- [x] `npm run build` 输出 0 error

### 11.3 质量门禁

- [x] `npm run build` 成功（TypeScript 编译 + Vite 打包无错误）
- [x] `npm run test:unit` 全部通过（Vitest 单元测试，16 passed）
- [ ] 开发服务器手动验证：8 个页面全部渲染无 JS console error（需在本地 `npm run dev` 后人工核查）
- [ ] Docker 容器 `docker compose up frontend` 后 `http://localhost:80` 返回 200（Phase 10 全链路收尾时统一验证）
