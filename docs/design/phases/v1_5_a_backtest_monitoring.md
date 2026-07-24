# V1.5-A：回测引擎深化 + 监控增强 + 市场宽度 + 财务 PIT 修正

> 版本：v1.1（设计评审收口，2026-07-24）
> 状态：设计评审有条件通过 ✓，放行条件已消除（`docs/reviews/v1_5_a_backtest_monitoring_design_review_2026-07-24.md`）；待用户批准进 A4 实施
> 估算：~6.5-10 pd（roadmap §6 V1.5-A 行）
> 实施顺序（用户拍板 2026-07-24「先轻后重」）：**A4 → A2 → A3 → A1 → A5**
> 依据文档：
> - roadmap `v1_post_release_roadmap.md` §2.1（S6-GAP-02 + 滑点情景）+ §3（SDD-EXT-02s/03/07）+ §4.5（R13-P3-1~5）+ §6 主题表 V1.5-A 行
> - SDD `QuantPilot_SDD.md` §5.1（PIT 原则）/ §6.3（市场环境判定）/ §7.7（回测引擎 + §7.7.5 V1.0 已知局限）/ §16（版本路线图）
> - SDD 外部评审 `docs/reviews/SDD_review_outside_2026-04-22.md` §3.4（NH-NL）/ §5.1（涨停可行性）/ §5.2（业绩快报 PIT）
> - Phase 13 实施评审 `docs/reviews/phase13_implementation_review_2026-05-22.md` §8（P3 5 项 → V1.5-A）
> - Phase 8 回测设计 `docs/design/phases/phase8_backtest.md` §2.1（daily_positions 不持久化降级说明——本 phase 兑现）
> - 2GB 内存墙定案（memory `backtest-2gb-memory-wall`，2026-06-29）：生产 `backtest_enabled=false` 彻底禁用回测，回测统一走本地算力中心（5434 库 + `scripts/run_backtest_local.py`）——**本 phase 所有回测改动只作用于本地算力中心，零生产回测写路径**

---

## 修订历史

| 版本 | 日期 | 修订内容 |
|------|------|---------|
| v1.0 | 2026-07-24 | 初版。整合 roadmap V1.5-A 五条工作流：A1 回测引擎深化（S6-GAP-02 daily_positions 流式持久化 + 滑点情景对比 + SDD §7.7.5 V1.0 局限审计删除）/ A2 涨停可行性精细化（SDD-EXT-02s，校准 B3-1 全量跳过）/ A3 NH-NL 市场宽度（SDD-EXT-07）/ A4 监控增强（R13-P3-1~5）/ A5 业绩预告快报 PIT 数据层（SDD-EXT-03）。锁定实施顺序 A4→A2→A3→A1→A5（先轻后重）。启动核查见 §1.3 |
| v1.1 | 2026-07-24 | **设计评审收口**（第三方评审有条件通过 ✓，0 P1 / 2 P2 / 4 P3）。**放行条件（2 P2，均为 Scope 总览误标「无生产写」的内部矛盾）已消除**：① A3 `breadth_weak` 落地生产 Scorer 须持久化——`MarketStateRecord` 经 `MarketStateHistory` ORM → `get_current_state`（DB 读→`_orm_to_record`）流，若不落库生产 Scorer 读不回、弱势震荡压制永不生效；§4.2 补 `MarketStateHistory` 加 `breadth_weak` 列 + alembic ALTER + 映射，§1.2/§4.4/§9 改 A3「有生产写（既有表 ALTER，部署单列 C-1）」；② §1.2 A1「无生产写」→「有（`backtest_daily_position` 前向建表供回流展示/import）」对齐 §9。**P3 一并处理**：§4.3/§4.4 补回测侧 `_get_market_state` 返回 record/`(enum,breadth_weak)` 使 breadth_weak 流到回测 Scorer；§2.5 审计范围拆 §7.7.5（4 项全删）+ DISCLAIMER/banner（涨停/快报 caveat 留到 A2/A5 交付后删），A2/A5 DoD 同步改指 banner 非 §7.7.5；§1.2 括注分项精确和 7.6-9.6 vs 标称 6.5-10。R13-P3 编号（评审判定 roadmap 背书的跨文档工作项 ID、非一次性评审编号）保留留痕 |

---

## 1. 概述

### 1.1 背景

V1.5-A 是 V1.0 RC 发布 + V1.5-G 多用户化之后的**回测可信度 + 数据质量 + 监控收尾**主题批。它汇集四类来源、五条工作流，共同点是「都不阻断主路径、但都影响用户对系统输出的信任」：

- **回测可信度**（A1/A2）：回测是用户评估策略的核心工具。当前 daily_positions 不持久化（Phase 8 §2.1 降级）+ 涨停成交模型在 B3-1 被过度保守地一刀切，都让回测结果与实盘产生系统性偏差。
- **市场环境识别**（A3）：SDD §6.3 仅用沪深300趋势判定牛熊，在「权重股护盘、小盘股阴跌」的结构性行情会误判为上涨趋势而盲目加仓趋势策略，NH-NL 市场宽度是外部评审 P2 明确的辅助确认指标。
- **财务数据 PIT**（A5）：业绩快报/预告到正式财报之间 1-2 月的信息真空期，当前只按财报公告日更新估值，SDD 外部评审 P1 要求补业绩预告/快报数据层。
- **监控收尾**（A4）：Phase 13 实施评审遗留的 5 项 P3 建议性改进，三链归 V1.5-A。

**关键约束（2GB 内存墙定案）**：生产已 `backtest_enabled=false` 彻底禁用回测（`POST /backtest/run` → 503），任何真实回测在本地算力中心（`docker-compose.backtest-local.yml` 的 5434 库 + `scripts/run_backtest_local.py`）跑、结果经 `POST /backtest/import` 幂等回流生产 Web 展示。**A1/A2 的引擎改动只作用于本地算力中心**，不重开生产回测、不触碰生产回测护栏（7 天窗口 + 并发护栏）。

### 1.2 Scope 总览

| 子批 | 主题 | pd | 段落 | 生产写 | 实施序 |
|------|------|-----|------|--------|--------|
| **A4** | 监控增强 R13-P3-1~5（WS 探测 / SecretFilter / factor_monitor_params / TushareAdapter 埋点 / Grafana）| 1.1 | §5 | 部分（Grafana provisioning 随监控栈；配置项 config_key 无迁移）| 1 |
| **A2** | 涨停成交可行性精细化（SDD-EXT-02s，校准 B3-1）| 0.5-1 | §3 | 无（本地引擎纯函数）| 2 |
| **A3** | NH-NL 市场宽度指标（SDD-EXT-07）| 1-1.5 | §4 | **有**（`market_state_history` 新增 `breadth_weak` 列 ALTER + 迁移，供生产 Scorer 经 `get_current_state` 读回；部署单列 C-1）| 3 |
| **A1** | 回测引擎深化：daily_positions 流式持久化（S6-GAP-02）+ 滑点情景对比 + SDD §7.7.5 局限审计 | 3 | §2 | **有**（`backtest_daily_position` 前向建表供回流展示 / import 写；回测 run 本身不写生产＝本地 5434）| 4 |
| **A5** | 业绩预告/快报 PIT 数据层（SDD-EXT-03）| 2-3 | §6 | 有（新表 alembic + 生产 5y 回填，需 C-1 单独确认 + pg_dump）| 5 |

**合计 ~6.5-10 pd**（标称沿用 roadmap §6 权威登记；分项精确和 = 7.6-9.6 pd，下界差异承 roadmap §6 区间，收尾时或回写 roadmap §6 收敛）。「先轻后重」＝先做零/低生产写、快速见效的 A4/A2，再做含生产表 ALTER 的 A3（**注**：A3 实含生产既有表迁移，部署风险高于纯 Engine 层，部署单列 C-1）与需重构 BacktestEngine 输出路径 + 生产建表的 A1，最后做需新数据层 + 生产回填、风险最高的 A5。

### 1.3 启动核查（CLAUDE.md §5.1）

| 核查项 | 结论 |
|--------|------|
| 读 system_design §9 本 phase 行 | V1.5-A 是 RC 后 V1.5 主题，**不在 V1.0 §9 Phase 表内**；权威登记在 roadmap §2.1/§3/§4.5/§6 V1.5-A 行。沿用 V1.5-G 先例（`v1_5_<letter>_<topic>.md` 文件 + roadmap §6 登记，不占 §9 Phase 编号）✓ |
| 模块去向决定 | A1-A5 全部纳入本 phase；无推迟子项。A5 内 SDD-EXT-02f/06f/09f 完整版（Level-2 / 边际 VaR / 因子拥挤度）本就归 V2.0（roadmap §5），非本 phase scope ✓ |
| grep `R\d+-P[2-3]-\d+` 跨 system_design + roadmap + reviews | R13-P3-1~5 三链完整（链 A：phase13 实施评审 §8 P3 行；链 B：本设计文档 §5 展开；链 C：roadmap §4.5 + §6 V1.5-A 主题表）。已核 phase14/15 评审 + phase15 设计文档均确认 R13-P3 归属 V1.5-A、不属它 phase ✓ |
| 孤儿检查（system_design §3/§5 模块 + §6 端点）| 新增模块：`models/market.py::FinancialForecast`（A5 新表）、`engine/market_state.py` 扩展 NH-NL（A3，非新文件）、`data/adapters/tushare.py` 新增 `fetch_forecast_express`（A5）。新增端点：无（A1/A5 复用既有 `/backtest/*`、`/market/state`；A5 数据经既有 ingest 管线消费）。全部回写 system_design §3/§6（收尾） ✓ |
| 推迟模块引言注明 + §9 更新 + 三链 | 本 phase 不新增推迟项。SDD §7.7.5 V1.0 回测 4 项 P0 局限中被 A1/A2 实际修复的条目，收尾时按 SDD v1.0-r4 约定同步删除 + 前端 `BacktestLimitationsBanner` 对应条目移除（见 §2.5）✓ |
| C-5 范围变更回写顺序 | 本 phase 无新范围（全部 roadmap 已登记项）。A3 NH-NL 触及 SDD §6.3 市场环境判定 + A5 触及 SDD §5.1 PIT / §4.2 数据字段——收尾时回写 SDD 对应节 + system_design §5（市场状态）/§3（数据模型）✓ |

**启动核查清单（收尾逐项勾选）**：
- [ ] grep `system_design §9` 无 V1.5-A 专行（确认沿用 roadmap 登记先例，不误建 §9 Phase 行）
- [ ] grep `R\d+-P[2-3]-\d+` 跨 system_design + roadmap + reviews/ 确认 R13-P3-1~5 消费闭环（本 phase 收尾从 roadmap §4.5 移除 5 行 + §6 主题表 V1.5-A 行标记已交付项）
- [ ] SDD §7.7.5 局限条目审计：逐条判定 A1/A2 是否实际修复，修复的删 SDD 正文 + 前端 banner

---

## 2. A1 — 回测引擎深化（S6-GAP-02 + 滑点情景 + 局限审计）

> roadmap §2.1：S6-GAP-02「BacktestEngine 内存累积 O(N×T)（V1.0 单组合可承受，V1.5 多组合并行需流式写 DB）」修复条件＝「改为流式持久化 daily_positions」；+ 滑点敏感性多情景对比（SDD §16）。

### 2.1 现状与问题

`BacktestEngine.run`（`engine/backtest/engine.py`）主循环第 k 步（L500-507）对每个交易日的每只持仓 append 一条 dict 到 `position_snapshots` list，循环结束 L520 一次性 `pd.DataFrame(position_snapshots)`。持仓数 N × 交易日数 T → **内存 O(N×T)**。单组合、短窗口可承受，但：

1. **Phase 8 §2.1 降级未兑现**：daily_positions 只在内存构造后随 `BacktestResult` 返回，**从不持久化**——用户看不到历史每日持仓明细，回测结果页无法回溯任一交易日的持仓构成。
2. **多组合并行阻塞**：多组合并行回测（roadmap 未来项）时 M 个组合 × O(N×T) 内存并发 → 撑爆；必须改为「算一日、落一日、不在内存累积」。

**关键约束（CLAUDE.md §6）**：BacktestEngine 严格无 IO。流式持久化**不能在引擎内直接写库**——必须走**回调 sink 模式**（类比现有 `progress_cb`）：引擎每算完一日调 `position_sink(trade_date, snapshots)`，由 `BacktestService`（含 IO 的编排层）提供 sink 实现批量写 DB。

### 2.2 设计：流式 sink + 新持久化表

**新表 `backtest_daily_position`**（alembic 新迁移，与回测两表 `backtest_task` / `backtest_result` 同族，本地算力中心 + 生产回流两侧都建）：

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BigInt PK | |
| `task_id` | UUID FK→backtest_task | 幂等键组成，本地 UUID 与生产永不撞号（同 import 端点约定）|
| `trade_date` | Date | |
| `ts_code` | String | |
| `shares` | Integer | |
| `cost_price` | Numeric(10,3) | WAC 成本价 |
| `market_value` | Numeric(15,2) | |

索引：`(task_id, trade_date)`；唯一约束 `(task_id, trade_date, ts_code)`（幂等 upsert）。

**引擎签名扩展**（`BacktestEngine.run`）：

```python
def run(
    self, config, data,
    progress_cb: Callable[[str, int, float], None] | None = None,
    position_sink: Callable[[date, list[dict]], None] | None = None,  # 新增
) -> BacktestResult:
```

主循环第 k 步改为：
- `position_sink is not None` → 每日调 `position_sink(trade_date, day_snapshots)`，**不再** append 到 `position_snapshots` 全量 list；`BacktestResult.daily_positions` 返回空 DataFrame（明细已流式落库，结果页从 DB 查）。
- `position_sink is None`（单测 / mock 回测 / 旧契约）→ 保留 L520 内存累积行为（向后兼容既有测试断言 `result.daily_positions`）。

**Service 侧 sink 实现**（`BacktestService`，含 IO）：`asyncio.to_thread` 包 `engine.run` 时，sink 用一个**有界缓冲 + 批量 flush** 的闭包（每积攒 `_SINK_BATCH=500` 行或每 N 日 flush 一次），经 `AsyncSessionLocal` upsert（`on_conflict_do_update`，遵 CLAUDE.md §3 asyncpg 32767 占位符分批 + `_BATCH_SIZE`）。注意 sink 在 to_thread 子线程调用 → 用 `asyncio.run_coroutine_threadsafe(coro, loop)` 把写库 coroutine 投递回主 loop（预捕获 `loop = asyncio.get_running_loop()`，CLAUDE.md §2「线程回调中的 event loop」），或用同步 psycopg 直写。**【设计待定：sink 落库线程模型——`run_coroutine_threadsafe` 回投主 loop vs sink 内用独立同步连接；实施期二选一并压测本地 5434 内存峰值，目标 30 日回测 position 累积内存 → O(batch) 常量】**

**结果读取**：`GET /backtest/{id}/result` 的 daily_positions 从 `backtest_daily_position` 按 task_id 分页查（不再从 `BacktestResult` 内存 DataFrame）。`POST /backtest/import` 回流时一并接收并 upsert daily_positions（本地算力中心跑完 → 回流生产）。

### 2.3 设计：滑点敏感性多情景对比

> roadmap §2.1：「BacktestConfig 支持 slippage_scenarios 列表，输出对比报告」。

`BacktestConfig`（`schemas/backtest.py`）新增可选字段：

```python
slippage_scenarios: list[float] | None = Field(default=None)  # 如 [0.001, 0.002, 0.005]
```

- `slippage_scenarios` 为 None → 单情景（现状，用 `slippage_rate`）。
- 非空 → BacktestService 对每个滑点值**复用同一 BacktestDataBundle**（数据只加载一次，避免 N×内存）串行跑 N 次引擎（每次覆盖 `config.slippage_rate`），产出对比报告：各情景的 cumulative_return / max_drawdown / sharpe / 换手成本占比。

**内存注意**：多情景**必须复用 bundle**（bundle 是内存大头），串行跑；情景数上界护栏（如 ≤5）防滥用。对比报告结构 `{scenario: slippage_rate, performance: {...}}` 列表，前端回测结果页新增滑点敏感性表格/曲线（本地算力中心跑 + 回流）。

### 2.4 A2 联动：涨停成交精细化在引擎内落地

A2（§3）改的是 `_execute_signals` 的涨停跳过逻辑，属 A1 引擎同文件。实施序 A2 在 A1 之前（先轻后重），故 A2 先独立交付、A1 阶段引擎重构时保持 A2 已落地的涨停规则不回退。

### 2.5 SDD §7.7.5 V1.0 回测局限审计（SDD v1.0-r4 约定）

SDD §7.7.5 列 V1.0 回测 4 项 P0 缺陷（T+1 撮合违反 / quotes_t 字段缺失 / pe_pb_history 与 index_adj_prices 空 DF / 不调 RiskChecker）。**实证：这 4 项在 V1.0 收尾期（Batch 3 B3-1~9 + 2GB 内存墙会话 commit 5f29f71/925244b/825e6a3）已实际修复**（T+1 撮合队列 B3-2 / daily_quotes 全字段 B3-1 / pe_pb_history + index_adj_prices 真实加载 B3-3 / RiskChecker 集成 B3-4）。

**收尾动作**：逐条核对 SDD §7.7.5 每条局限对应代码是否真修复（跑本地 5434 引擎实证），已修复的：
1. 从 SDD §7.7.5 正文删除该条（SDD 修订历史记 V1.5-A 删除）；
2. 前端 `BacktestLimitationsBanner.vue`（B1-2）对应条目移除；
3. `engine/backtest/report.py::DISCLAIMER`（B1-1）同步收敛。

**审计范围须拆两处（勿混挂）**：
1. **SDD §7.7.5**（恰 4 项：T+1 / quotes 字段 / pe_pb+index 空 DF / RiskChecker）——全部 ✅ 已修复，A1 收尾时 4 项全删（SDD 修订历史记 V1.5-A 删除）。
2. **`DISCLAIMER`（B1-1）+ 前端 `BacktestLimitationsBanner`（B1-2）合规文案**（v1.0-r4 合规链）——涨停一刀切 / 财务快报缺失等 caveat 在此，**不在 §7.7.5**。这些留到对应子批交付后删：涨停 caveat 待 A2 交付后删、财务快报 caveat 待 A5 交付后删。

**【设计待定：逐条修复判定表——实施 A1/A2/A5 收尾时填「局限条目 / 所属处（§7.7.5 vs DISCLAIMER/banner）/ 对应 commit / 是否真修复 / 删或留」，避免误删未真修复项，亦避免去 §7.7.5 找涨停/快报条目扑空】**

### 2.6 A1 DoD

- [ ] `backtest_daily_position` 表 + alembic 迁移（本地 5434 + 生产回流两侧 upgrade head）
- [ ] `BacktestEngine.run` 加 `position_sink` 回调；sink 非空时不内存累积、`daily_positions` 空 DF；sink 空时保留旧行为（既有测试不破）
- [ ] `BacktestService` sink 实现（批量 upsert，本地 5434 内存峰值实测 O(batch) 常量，30 日回测对比改造前内存曲线）
- [ ] `GET /backtest/{id}/result` daily_positions 从新表分页查；`POST /backtest/import` 接收并 upsert daily_positions
- [ ] `BacktestConfig.slippage_scenarios` + 多情景串行跑（复用 bundle）+ 对比报告 + 前端展示
- [ ] SDD §7.7.5 逐条审计表填写 + 已修复局限删除（SDD 正文 + banner + DISCLAIMER）
- [ ] 单测：sink 回调被逐日调用且参数正确 / 多情景对比结构 / 幂等 upsert；INT：本地 5434 真引擎跑出 daily_positions 落库 + 内存峰值断言

---

## 3. A2 — 涨停成交可行性精细化（SDD-EXT-02s）

> SDD 外部评审 §5.1 P0（简化版归 V1.5-A，完整版 SDD-EXT-02f 需 Level-2 归 V2.0）。

### 3.1 现状

`_execute_signals`（`engine/backtest/engine.py` L887-893）B3-1 现逻辑：**BUY 信号若成交日 `limit_up==True` 则一律跳过**（SELL 仍允许）。这是对 SDD 原始 V1.0「只排除一字涨停、其余按收盘价成交」（评审批为过于乐观）的**过度反向修正**——把所有涨停一刀切，导致流动性充分、盘中反复打开的涨停股（实际可成交的动量入场）被全部拒绝，系统性低估动量策略回测收益。

### 3.2 设计：SDD-EXT-02s 无量一字板精确判定

`daily_quote` 已有字段 `limit_up`（bool）+ `turnover_rate`（Numeric 8,6，单位：比例还是百分比需实施核对）。落地 SDD-EXT-02s 简化规则：

> **BUY 不可成交** ⟺ 成交日 `limit_up == True` **AND** `turnover_rate < 0.01`（无量一字板特征）。
> 涨停但有量（`turnover_rate ≥ 0.01`，盘中打开过）→ **可成交**（改回可买）。

相对 B3-1：更宽松（放行有量涨停）；相对 SDD 原始 V1.0：更严（一字板不可成交）——落到现实中值。方向影响：动量策略回测收益相对当前会**小幅上升**（放行了合理入场），但模型更贴近实盘。

**符号与阈值**：
- `turnover_rate` 单位实施期必须核实（`fetch_daily_quotes` adapter 内单位换算）——Tushare `daily_basic.turnover_rate` 是**百分比**（如 1.5 = 1.5%），若入库已 ×0.01 成比例则阈值用 0.01，若原样存百分比则阈值用 1.0。**【设计待定：turnover_rate 入库单位——实施首步查 daily_quote 实际值域（`SELECT max/min` 本地 5434），据此定阈值常量 `_LIMIT_UP_ILLIQUID_TURNOVER`，写死注释单位】**
- `turnover_rate` 为 NULL（数据缺失）→ 保守视为无量（跳过 BUY），并 `logger.warning`（不静默）。

**SELL 侧对称性**：SDD-EXT-02s 只规定 BUY。跌停无量板卖不出（对称约束）暂不在本 phase scope（SDD 未定义简化版 SELL 规则）→ 保留现状 SELL 总允许，§10 推迟项登记「跌停无量板 SELL 约束」归 V2.0 SDD-EXT-02f 一并。

### 3.3 A2 DoD

- [ ] `_execute_signals` BUY 涨停跳过改为 `limit_up AND turnover_rate < _LIMIT_UP_ILLIQUID_TURNOVER`
- [ ] `turnover_rate` 入库单位核实 + 阈值常量注释单位 + NULL 保守降级 + `logger.warning`
- [ ] 单测：涨停无量→跳过 / 涨停有量→成交 / 非涨停→成交 / turnover NULL→跳过（4 场景纯函数）
- [ ] 涨停 caveat 按 §2.5 审计——注意涨停一刀切 caveat 在 **`DISCLAIMER`/`BacktestLimitationsBanner`**（非 §7.7.5 四项内），A2 交付后删该 caveat

---

## 4. A3 — NH-NL 市场宽度指标（SDD-EXT-07）

> SDD 外部评审 §3.4 P2：仅用 HS300 均线判牛熊，在结构性行情（权重护盘、小盘阴跌）误判上涨趋势。引入 NH-NL 市场宽度作辅助确认。

### 4.1 定义

- **NH-NL 差值** = (创 60 日新高家数 − 创 60 日新低家数) / 当日可投资宇宙标的数。
- 阈值：> 10% 健康；< −10% 危险；本 phase 消费 **0% 分界**（外部评审 §3.4 判定逻辑）。

### 4.2 设计：MarketStateEngine 扩展弱势震荡降级

现 `MarketStateEngine.determine_raw_state`（纯函数，L93）仅凭 ADX + MA20/MA60/close 判定 UPTREND/DOWNTREND/OSCILLATION。NH-NL 是**横截面宽度**（需全市场当日新高新低统计），无法在单指数 OHLCV 内算——须由 Service 层预算好 NH-NL 值传入。

**降级规则**（外部评审 §3.4）：

> 原判定为 UPTREND（ADX > 阈值 AND MA20 > MA60 AND close > MA20）时，
> - NH-NL 差值 > 0% → 确认 UPTREND；
> - NH-NL 差值 ≤ 0% → 降级为「弱势震荡」（压制趋势策略权重）。

「弱势震荡」的承载：**不新增枚举值**（避免 MarketStateEnum 三态契约破裂 + 全链路 config_matrix/scorer 权重表改动）。改为在 `MarketStateRecord` 加布尔字段 `breadth_weak: bool`（默认 False），UPTREND 且 NH-NL≤0 时置 True，market_state 仍报 UPTREND 但 `breadth_weak=True`。下游 Scorer 权重查找时 `breadth_weak` → 趋势策略权重按系数压制（复用 OSCILLATION 态权重或乘惩罚系数）。**【设计待定：弱势震荡权重承载——(a) breadth_weak 时 scorer 查 OSCILLATION 权重行 vs (b) UPTREND 权重 × 趋势惩罚系数；实施期定，需与 phase11 config_matrix 权重表结构对齐，避免引入第 4 套权重】**

**breadth_weak 必须持久化（生产链路完整性 · 放行条件）**：`MarketStateRecord` 是 engine 层 dataclass，经 `MarketStateHistory` ORM（`models/business.py`）→ `repository.upsert_market_state` 落库，生产 Scorer 经 `MarketStateService.get_current_state`（**从 DB 取行 → `_orm_to_record`**）读当前态。故 `breadth_weak` 若只加在 dataclass 不落库，`get_current_state` 读回即丢 → 生产 Scorer 拿不到、弱势震荡压制**永不生效**。落地要求：
- `MarketStateHistory` 新增 `breadth_weak: Mapped[bool]`（默认 False）列 + **alembic 迁移**（生产既有表 ALTER，前向非破坏；部署单列 C-1，见 §9）；
- `repository.upsert_market_state` 写入 + `_orm_to_record` 读回均映射 `breadth_weak`；
- ORM `__table_args__` 与迁移一致（CLAUDE.md §4.8）。

### 4.3 NH-NL 数据来源

创 60 日新高/新低：需当日全宇宙每只股票近 60 交易日 high/low 极值对比当日 close。数据源 `daily_quote`（已有 high/low/close）。计算位置：
- **生产每日管线**：`DataService` / market_state 计算前预算当日 NH-NL（一次全宇宙 60 日窗口 rolling max/min，pandas 向量化，O(1) 已在库数据）。
- **回测**：`BacktestEngine._get_market_state`（`engine.py:675`）现**只返回 `MarketStateEnum`**（注释「抽出 `.market_state` 供 Scorer 使用」），breadth_weak 是 record 上与 enum 并列的独立 bool → 若不改签名，回测 Scorer 只拿到 enum、breadth_weak 在回测链路丢失。落地要求：`_get_market_state` 改为返回 `MarketStateRecord`（或 `(enum, breadth_weak)` 元组），回测 Scorer 权重查找消费 breadth_weak，与生产**同一压制路径**（上文 (a)/(b) 决策后两侧对齐）；NH-NL 从 `data.daily_quotes`（bundle 已含全字段 MultiIndex）按 trade_date 回看 60 日算传入 identify。**【设计待定：回测 NH-NL 性能——bundle daily_quotes 全量在内存，逐日 60 日 rolling 是否成回测热点；实施期 profile，必要时预算全期 NH-NL 时序一次】**

### 4.4 A3 DoD

- [ ] `MarketStateRecord.breadth_weak: bool` 字段 + `MarketStateEngine.identify` 接受可选 `nh_nl_series` 参数
- [ ] UPTREND 且 NH-NL≤0 → breadth_weak=True（纯函数单测：NH-NL>0 确认 / ≤0 降级 / 非 UPTREND 不受影响）
- [ ] **持久化链路**：`MarketStateHistory` 新增 `breadth_weak` 列 + alembic 迁移（生产 ALTER，C-1）+ `upsert_market_state` 写入 + `_orm_to_record` 读回映射（否则生产 Scorer 读不到，压制失效）
- [ ] 回测侧 `_get_market_state` 改返回 record/`(enum, breadth_weak)`，回测 Scorer 与生产同一压制路径消费 breadth_weak
- [ ] NH-NL 计算：生产管线 + 回测两路径（60 日新高新低 / 宇宙数，向量化）
- [ ] Scorer 消费 breadth_weak 压制趋势权重（与 phase11 权重表对齐）
- [ ] 回写 SDD §6.3 市场环境判定新增 NH-NL 逻辑 + system_design §5 市场状态模块 + §3 数据模型（MarketStateHistory 加列）

---

## 5. A4 — 监控增强（R13-P3-1~5）

> Phase 13 实施评审 §8 P3 5 项，roadmap §4.5 三链完整。低风险小项，实施序第一（先轻）。

| 编号 | 描述 | 落点 | 估算 |
|------|------|------|------|
| R13-P3-1 | 冒烟 API-101 改 Upgrade header 探测 WS 端点（区分「路由存在」vs「未注册」）| `tests/smoke/test_api_live.py` API-101 | 0.1 pd |
| R13-P3-2 | `SecretFilter.filter` 扫描 `record.__dict__` 覆盖 structured logging extra 字段 | `core/logging` SecretFilter | 0.2 pd |
| R13-P3-3 | `factor_monitor_params` config_key 收纳 `PERSISTENT_DECAY_THRESHOLD/MONTHS` | `core/config_defaults` + FactorMonitorService | 0.3 pd |
| R13-P3-4 | `TushareAdapter._call` 内统一 `TUSHARE_CALLS` Counter 埋点（覆盖全部 13 接口）| `data/adapters/tushare.py` + MetricsRegistry | 0.3 pd |
| R13-P3-5 | Grafana Overview 补 3 panel（健康告警 alert_type / APScheduler 失败 job_id / DataQualityMetric trend）| `infra/grafana/provisioning` | 0.2 pd |

**要点**：
- **R13-P3-1**：现 API-101 只探路由存在（GET 返回非 404）。改为发带 `Upgrade: websocket` header 的握手探测，区分「WS 端点已注册并升级」vs「路由存在但非 WS」。
- **R13-P3-2**：现 SecretFilter 只扫 `record.msg` / `record.args`。structured logging 的 `logger.info(..., extra={...})` 字段落在 `record.__dict__` 其他键 → 密钥可能从 extra 泄漏。扩展 filter 遍历 `record.__dict__`（跳过标准 LogRecord 属性）应用 5 类正则。
- **R13-P3-3**：`PERSISTENT_DECAY_THRESHOLD`（0.05）/`MONTHS`（3）当前硬编码在 FactorMonitorService.check_persistent_decay。收纳进 `factor_monitor_params` config_key（可运行时调），与 phase13 既有 config 一致走 ConfigService。
- **R13-P3-4**：`TushareAdapter._call` 是所有 13 个 Tushare 接口的统一异步包装入口 → 在此一处 `TUSHARE_CALLS.labels(interface=...).inc()`，覆盖全接口（现只部分接口埋点）。
- **R13-P3-5**：Grafana Overview 仪表盘 provisioning JSON 补 3 panel（数据已有 Counter/Gauge，只加可视化）。

### 5.1 A4 DoD

- [ ] R13-P3-1~5 逐项交付
- [ ] SecretFilter record.__dict__ extra 字段脱敏单测（extra 含密钥 → 被 mask）
- [ ] factor_monitor_params 收纳后 check_persistent_decay 读 config 而非硬编码（单测）
- [ ] TushareAdapter._call TUSHARE_CALLS 埋点单测（mock 调用 → Counter inc）
- [ ] 冒烟 API-101 Upgrade header 探测真机 PASS
- [ ] Grafana provisioning 3 panel（监控栈 profile 起后目视 / provisioning JSON 校验）
- [ ] 收尾从 roadmap §4.5 移除 5 行

---

## 6. A5 — 业绩预告/快报 PIT 数据层（SDD-EXT-03）

> SDD 外部评审 §5.2 P1：业绩快报（次年 2 月底）到正式年报（3-4 月）1-2 月信息真空期，当前只按财报公告日更新估值。**本子批最重、唯一含生产写（新表 + 5y 回填），需 C-1 单独确认 + pg_dump 前置。**

### 6.1 设计：新表 + data_priority 优先级

**新表 `financial_forecast`**（业绩预告/快报，alembic 迁移）：

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BigInt PK | |
| `ts_code` | String | |
| `report_period` | Date | 报告期（如 20251231）|
| `pre_announce_date` | Date | 业绩预告/快报发布日（PIT 关键）|
| `est_net_profit` | Numeric | 预告净利润（中值；预告给区间取中值，快报给确值）|
| `est_net_profit_yoy` | Numeric | 同比增速 |
| `data_priority` | SmallInt | 正式财报(3) > 业绩快报(2) > 业绩预告(1) |
| `source_type` | String | 'forecast' / 'express' |

唯一约束 `(ts_code, report_period, source_type)`；索引 `(ts_code, pre_announce_date)`。

### 6.2 Tushare 接口接入

`TushareAdapter` 新增 `fetch_forecast_express`（走既有 `_call` 异步包装 + Semaphore，CLAUDE.md §4.3）：
- `pro.forecast(period=..., ...)` → 业绩预告（净利润区间 → 中值，type 预增/预减等）。
- `pro.express(period=..., ...)` → 业绩快报（营收/净利润确值）。
- 两者 `ann_date` = 发布日 → 落 `pre_announce_date`（PIT）。data_priority：forecast=1 / express=2。

**quirks（实施期核对，CLAUDE.md §4.3 同类）**：
- `forecast` / `express` 是否需 `period + ts_code` 组合分批（类比 `fina_indicator` 50 只/批 + sleep），还是 period-only 全市场可用——实施首步小样本验证，避免 period-only 静默吞异常填 NULL。
- 单位换算（净利润元/万元）在 adapter 内完成。

### 6.3 PIT 优先级消费

估值/评分链路取财务数据时，同一 `(ts_code, report_period)` 若既有正式财报（`financial_data`，priority 3）又有快报/预告（`financial_forecast`，priority 2/1）：**PIT 时点下取 data_priority 最高的已发布数据**。信息真空期（快报已发、正式财报未发）→ 用快报修正估值。

**消费点**：`ScoringService._build_market_snapshot`（实盘）+ `BacktestEngine._get_financials_at`（回测 PIT 切片）。**【设计待定：forecast/express 与 financial_data 的字段映射——est_net_profit 如何并入 ValueStrategy 的 pe_ttm/pb 修正路径；SDD-EXT-03 原文「优先修正估值指标」需明确修正哪些因子（EP / 净利润增速），实施期定字段级映射，避免只建表不真消费（C-4 禁占位）】**

### 6.4 生产回填（C-1 红线）

新表建后需 5y 回填 forecast/express 历史（类比 `ingest_history` 财务回填）。**生产写 → 必须 C-1 单独确认 + pg_dump 前置 + 本地 5434/5433 先验证回填脚本**。回填走既有 refill 框架扩展（`refill_history.py` 加 forecast/express 表）或独立 `backfill_forecast.py`。

### 6.5 A5 DoD

- [ ] `financial_forecast` 表 + alembic 迁移
- [ ] `TushareAdapter.fetch_forecast_express`（_call 包装 + 单位换算 + quirks 核对）
- [ ] data_priority PIT 优先级消费（ScoringService + BacktestEngine 两路径真消费，非占位）
- [ ] 5y 回填脚本（本地先验证 → C-1 确认 + pg_dump → 生产回填 → 行数实证）
- [ ] 回写 SDD §4.2（新增子表 4.2.1）+ §5.1（快报优先规则）+ system_design §3 数据模型
- [ ] 单测：data_priority 排序 / PIT 时点取最高优先级；INT：forecast+财报共存时估值修正生效
- [ ] 财务快报 caveat 按 §2.5 审计——在 **`DISCLAIMER`/`BacktestLimitationsBanner`**（非 §7.7.5），A5 交付后删

---

## 7. 测试计划

| 层 | 覆盖 |
|----|------|
| 单测 | A1 sink 逐日回调 + 多情景对比结构 + 幂等 upsert；A2 涨停 4 场景纯函数；A3 breadth_weak 判定纯函数 + NH-NL 向量化；A4 SecretFilter extra / config 收纳 / TUSHARE_CALLS 埋点；A5 data_priority PIT 排序 |
| 集成 | A1 本地 5434 真引擎 daily_positions 落库 + 内存峰值断言（精确 `== batch` 上界，CLAUDE.md 禁宽松）；A3 NH-NL 端到端市场状态降级；A5 forecast+财报共存估值修正 |
| e2e | A1 `GET /backtest/{id}/result` daily_positions 分页 + import 回流；滑点情景对比端点 |
| 冒烟 | A4 API-101 Upgrade 探测；A1 回测结果 daily_positions 字段（对生产回流数据，非生产跑回测）；新增端点逐行对照本文档场景 |

**红线**：集成测试只跑 5433 测试库 / 本地 5434 算力库，**永不对生产 5432 跑 pytest integration**（conftest downgrade base 灭表，C-1）。A1 回测改动**永不 `POST /backtest/run` 打生产**（2GB OOM，运维红线）。

---

## 8. DoD（收尾门槛）

- [ ] A1-A5 各子批 DoD 全勾（§2.6 / §3.3 / §4.4 / §5.1 / §6.5）
- [ ] `uv run ruff check src/ tests/` 输出 0 error
- [ ] `uv run pytest tests/unit/ tests/e2e/ tests/integration/ -q` 全绿（integration 对 5433）
- [ ] 前端 `vue-tsc` 0 error（滑点对比表 + banner 条目移除 + daily_positions 展示）
- [ ] 新增/改动端点冒烟逐行对照 §7（不只核数量）
- [ ] 文档回写：SDD（§4.2/§5.1/§6.3/§7.7.5）+ system_design（§3/§5/§6/§9 无 V1.5-A 专行确认）+ roadmap（§2.1/§3/§4.5 移除已交付项 + §6 V1.5-A 行标记）
- [ ] 收尾核查 CLAUDE.md §5.2：新经验写入项目/通用 CLAUDE.md

---

## 9. 迁移与部署

| 迁移 | 表 | 侧 | 生产写 |
|------|----|----|--------|
| A3 | `market_state_history` **ALTER**（加 `breadth_weak` 列）| 5433 测试 + 生产 | 生产 alembic upgrade（既有表前向 ALTER，非破坏；**部署单列 C-1**）|
| A1 | `backtest_daily_position`（新表）| 本地 5434 + 生产回流 | 生产 alembic upgrade（前向建表，非破坏）|
| A5 | `financial_forecast`（新表）| 本地 5433/5434 + 生产 | 生产 alembic upgrade + **5y 回填（C-1 + pg_dump）** |

**部署顺序**：A4（配置项无迁移，Grafana provisioning 随监控栈）→ A2（纯引擎，本地）→ A3（Engine + 管线 + **生产 `market_state_history` ALTER 迁移**，部署单列 C-1——A3 非纯 Engine 层，含生产既有表迁移）→ A1（本地为主 + 生产回流建表）→ A5（生产新表 + 回填，风险最高，最后且需 C-1）。

**生产 env 新增**（若有）双写 `.env.prod` + `docker-compose.prod.yml` environment 白名单（CLAUDE.md §11 运维红线，printenv 验证容器拿到值再验行为）。A5 若引入 forecast 采集开关按此办。

---

## 10. 推迟项（三链落点见 roadmap）

| 推迟项 | 理由（四类之一）| 三链落点 |
|--------|------|---------|
| SDD-EXT-02f 涨停完整版（封板时间 + 封单强度）| 物理资源约束（Tushare 标准套餐无 Level-2 字段）| roadmap §5 V2.0（已在，本 phase 不动）|
| 跌停无量板 SELL 对称约束 | 验收标准未定义（SDD 未定义简化版 SELL 规则）| roadmap §5 与 SDD-EXT-02f 一并 V2.0（本 phase §3.2 登记）|
| SDD-EXT-06f 边际 VaR / 09f 因子拥挤度 | 跨 phase 大重构（协方差矩阵估计）| roadmap §5 V2.0（已在）|

本 phase 不新增「伪推迟」；A1-A5 全部本 phase 内交付。SDD §7.7.5 局限的删除是**兑现**（非推迟），随 A1/A2/A5 交付逐条删。
