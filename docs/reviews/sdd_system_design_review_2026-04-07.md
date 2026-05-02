# QuantPilot SDD & 系统设计文档评审报告

> **评审日期：** 2026-04-07
> **评审范围：** `docs/spec/QuantPilot_SDD.md`（v1.0-r1）和 `docs/design/system_design.md`（v1.3）
> **评审类型：** 逻辑完整性 + 文档整合性全面评审

---

## 目录

1. [重大缺陷（阻断级）](#一重大缺陷阻断级)
2. [设计缺失（高优先级）](#二设计缺失高优先级)
3. [逻辑不一致](#三逻辑不一致)
4. [细节缺漏（中优先级）](#四细节缺漏中优先级)
5. [整体完整性评价](#五整体完整性评价)
6. [建议优先修复清单](#六建议优先修复清单)

---

## 一、重大缺陷（阻断级）

### [TERM-01] 前复权/后复权英文术语双文档对立

这是两份文档最明显的不一致。

| 文档 | 前复权 → 英文方法名 | 后复权 → 英文方法名 |
|------|------------------|------------------|
| **SDD 附录A** | "Backward Adjusted" | "Forward Adjusted" |
| **system_design.md §5.2 方法名** | `forward_adjusted()` | `backward_adjusted()` |

SDD 附录A：`前复权 = Backward Adjusted`，`后复权 = Forward Adjusted`。  
system_design.md §5.2 代码注释：`backward_adjusted()` = "后复权（以上市首日为基准向前累乘）"，`forward_adjusted()` = "前复权（以最新价为基准向历史调整）"。

两个文档的英文术语方向完全相反。开发者实现时若两处混读，极有可能在回测和展示时使用错误的复权序列，后果严重（回测结果失真）。

**建议：** 统一以中文名（前复权 / 后复权）为锚点。方法名改为明确带中文语义的命名，例如：
- `qian_fu_quan()` / `forward_adjusted_for_display()` → 前复权，以最新价为基准，用于展示
- `hou_fu_quan()` / `backward_adjusted_for_backtest()` → 后复权，以上市首日为基准，用于回测

或在 SDD 附录A 中修订英文对照，与方法名保持一致。修订任选其一，关键是两文档须对齐。

---

### [LOGIC-01] 加仓条件中"非下跌趋势"约束范围两文档不一致

**SDD §10.1** 定义加仓满足"任一"条件可提示加仓：
- **条件A**：当前持仓盈利（持仓浮盈 > 0）——无市场状态限制
- **条件B**：当前价格与首次建仓成本价偏离 ≤ ±10%，**且** 市场状态为上涨趋势或震荡市

**system_design.md §5.9** `SignalGenerator` 注释：
```
加仓条件（SDD §10.1）：评分>阈值 且（持仓盈利 OR 当前价偏离成本≤±10%）且市场非下跌趋势
```

system_design.md 将"非下跌趋势"条件扩展到了条件A，导致：
- 下跌趋势中即使持仓盈利，也无法加仓（system_design.md 语义）
- 下跌趋势中持仓盈利，**可以**加仓（SDD 原意）

**建议：** system_design.md §5.9 的加仓条件注释改为：

```
加仓条件（SDD §10.1）：评分>阈值 且（
    持仓盈利（浮盈>0）
    OR（当前价偏离成本≤±10% AND 市场状态非下跌趋势）
）
```

---

### [DESIGN-01] 回测结果无持久化存储设计

`/backtest/run` 是异步接口，`/backtest/{id}/status` 和 `/backtest/{id}/result` 按 ID 查询进度和结果，这暗示回测任务需要持久化存储。但 system_design.md §4（数据模型）中**没有任何** `backtest_task` / `backtest_result` 相关表的定义。

具体缺失：
- 回测任务 ID 如何生成？
- 回测任务的状态（PENDING / RUNNING / SUCCESS / FAILED）存在哪里？
- `BacktestResult`（daily_nav、daily_positions、signal_history、performance）持久化到哪张表？
- 历史回测记录如何查询？

`pipeline_run` 表仅用于日级流水线，不能复用。

**建议：** 在 system_design.md §4 中新增 `backtest_task` 和 `backtest_result` 表定义。

---

### [DESIGN-02] 持仓每日盯市（Mark-to-Market）机制完全缺失

SDD §11.3 要求仪表盘展示"当日盈亏"和"总资产"，这需要每日用最新收盘价更新：
- `position.current_price`
- `position.market_value`
- `position.pnl_pct`
- `account.total_assets`

但 system_design.md 中：
- `DailyPipeline` 伪代码无此步骤
- 无任何 Service 方法承担此职责
- 无任何 APScheduler Job 负责收盘后触发盯市

这是一个完整的操作闭环缺失，导致持仓展示数据永远停留在上次手动录入时的状态。

**建议：** 在 `DailyPipeline.run()` CP3 之后，或作为独立步骤，添加 `account_service.mark_to_market(trade_date)` 调用，遍历所有持仓用当日收盘价更新持仓数据。

---

### [DESIGN-03] 净值曲线（NAV History）无每日快照存储方案

`GET /performance/history` 返回"净值曲线历史（含基准对比）"，这要求系统能提供一个按日期索引的组合净值时间序列。但 system_design.md 中：
- 无 `daily_portfolio_value` 或类似快照表
- 无文档说明此接口是实时重算还是从快照读取

若每次 API 调用都从 `trade_record` 实时重算历史 NAV，当持仓记录量大时性能无法满足 §15.5 的 API P95 ≤500ms 要求；若需持久化，存储方案未设计。

**建议：** 明确选择一种方案并在 system_design.md 中记录：
- **方案A（推荐）：** 在 `DailyPipeline` 盯市步骤后写入 `daily_portfolio_value` 表（account_id, trade_date, total_value, cash, position_value）
- **方案B：** API 层按需计算，并缓存到 Redis（需定义缓存失效策略）

---

## 二、设计缺失（高优先级）

### [DESIGN-04] PE/PB 每日更新机制未定义

SDD §4.2：PE(TTM) 和 PB "每日随价格更新"。但：
- `financial_data` 表按报告期（季度）存储 `pe_ttm / pb`
- `daily_quote` 表无这两个字段
- PE_TTM = 当日市值 / TTM净利润，分子每日变化，分母按季报

从哪里取每日 PE_TTM、如何计算、存储在哪里，两份文档均未定义。这影响 Phase 4 价值策略的实现——`fetch_financial_data()` 返回的是每日动态 PE 还是季度静态 PE，目前不明确。

---

### [DESIGN-05] `risk_engine.check()` 返回值与下游处理断链

`DailyPipeline` 伪代码：
```python
self.risk_engine.check(signals, positions, account)
# 返回值未被接收
```

SDD §10.2 说"系统标记告警并建议减仓，但不强制操作"，但这个"告警"如何：
- 从 `risk_engine.check()` 传递出来？（返回值？异常？修改 signals in-place？）
- 进入通知链路（`notifier.send_with_fallback()`）？
- 呈现给用户？

架构上完全断链，没有任何衔接文档。

---

### [DESIGN-06] 信号状态转换责任方未定义

SDD §9.4 定义了信号生命周期：`NEW → VIEWED → ACTED → EXPIRED / SUPERSEDED`，但：

| 状态 | 触发条件 | 责任方 |
|------|----------|--------|
| VIEWED | 用户查看信号详情时？通过 `PATCH /signals/{id}/status` 显式标记？ | 未定义 |
| ACTED | 录入 `trade_record.signal_id` 时自动触发？还是单独 API 调用？ | 未定义 |
| SUPERSEDED | 新信号生成时自动处理旧信号？由哪个 Service 负责？ | 未定义 |

这三种状态转换规则在 system_design.md 中完全缺失，会导致不同开发者做出不同实现。

---

### [DESIGN-07] 周报调度流程未设计

SDD §12.5 要求每周末自动生成周报。`scheduler.py` 注释中有"日级 + 月级 + 周报"，`ReportService` 有 `generate_weekly()` 方法，但：
- **无周报触发的 APScheduler Job 配置**（只有 DailyPipeline 和 MonthlyScheduler 有伪代码）
- 周报触发时的数据加载流程、与 MonthlyScheduler 的关系均未说明

Phase 7 设计文档编写时需特别补充此内容。

---

### [DESIGN-08] CP1 数据快照版本号生成方式未定义

`DailyPipeline` CP1：
```python
raw_data = await self.data_service.ingest(trade_date)
await self.pipeline_repo.mark_cp1(run.id, snapshot_version=raw_data.version)
```

`raw_data.version` 是 CP1 幂等保障的核心，但：
- 版本号如何生成？（时间戳？哈希？UUID？自增 ID？）
- 重跑时如何判断"同一数据版本"？
- 如果数据源在 CP1 和 CP2 之间推送了修正，版本号是否变化？

这直接影响 CP1 检查点的有效性。

---

### [DESIGN-09] `position.phase` 字段语义未定义

`position` 表有：
```sql
phase VARCHAR(10)  -- 'BUILD'/'HOLD'/'REDUCE'
```

但 SDD 中无对应概念定义，system_design.md 中也未说明：
- 何时 phase 为 BUILD（建仓中）？
- 何时变为 HOLD？
- 何时变为 REDUCE（减仓中）？
- 由哪个 Service 在什么时机更新此字段？

---

### [DESIGN-10] 因子半衰期计算算法未定义

SDD §7.4：因子半衰期 = "IC 从峰值衰减到一半所需时间"。`factor_ic_history` 表存储 `half_life_days`，`FactorMonitorEngine` 也列出此输出字段，但两份文档均未定义计算方法。

因子半衰期的通常算法是对 IC 自相关函数（ACF）进行指数拟合，或计算 IC 序列的 1阶自相关系数后推导，但这需要足够长的 IC 历史序列（至少 12+ 个月）。V1.0 初期数据不足时如何处理？是返回 NULL 还是报错？均无说明。

---

### [DESIGN-11] 分红处理机制完全缺失

SDD §11.4 明确要求记录"分红（股息收入）（同步更新持仓成本价）"，`fund_flow` 表有 `DIVIDEND` 类型。但：
- 分红数据从哪个 Tushare 接口获取？`DataSourceAdapter` 中无 `fetch_dividends()` 方法
- 何时触发分红记录写入？（日级 Pipeline 中？独立调度？）
- 分红如何调整 `position.cost_price`（除权日降低成本价 vs 分红日增加现金）？

整个分红处理链路在两份文档中完全缺失。

---

## 三、逻辑不一致

### [SCHEMA-01] `user_watchlist` 表注释与代码逻辑矛盾

`user_watchlist` 表 SQL 注释：
```sql
-- WHITELIST：持仓保护同等逻辑，降低候选池进入阈值（具体阈值通过 user_config 配置）
```

但 system_design.md §5.4 `CandidatePoolManager` 实际代码：
```python
whitelist_codes = await self.watchlist_repo.get_whitelist_codes()
pool_codes |= whitelist_codes  # 集合并集，直接强制纳入，无阈值
```

SDD §5.4 也明确写"用户白名单**强制纳入**（仍需满足停牌过滤）"。

SQL 注释与实际实现逻辑和 SDD 规范均相反，应修正注释为"强制纳入候选池（不经评分阈值过滤）"。

---

### [DESIGN-12] `signal_type` 的 EXIT / HOLD 类型在 SDD 中无定义

`signal` 表 `signal_type` 定义了四种值：`'BUY'/'SELL'/'HOLD'/'EXIT'`。

SDD §9 只明确定义了：
- §9.1 BUY 信号（触发条件、输出内容）
- §9.2 SELL 信号（触发条件、输出内容）
- §9.5 提到"持有状态"（持仓股在买卖阈值之间，**不产生任何信号**）

HOLD 和 EXIT 信号类型的触发条件、语义、与 SELL 的区别（EXIT = 清仓？SELL = 减仓？）在 SDD 中均无定义。若不实际使用，应从 schema 中移除；若有用，应在 SDD §9 中补充定义。

---

### [SCOPE-01] L1/L2/L3 用户分层与单管理员实现不匹配

SDD §2 和 §14 将 L1/L2/L3 用户分层作为核心设计：
- L1 用户只能使用默认参数
- L2 用户可调整策略参数
- L3 用户可自定义权重

`user_config.user_level` 字段、`GET /settings` 的"按 user_level 过滤可见项"均依赖此设计。

但 CLAUDE.md 明确写"单管理员用户"，整个认证系统只有一个 admin。V1.0 中用户层级配置的"可见性过滤"逻辑在单用户模式下如何运作，两份文档均未明确说明是简化跳过还是需要实现。应在 system_design.md §9 Phase 6 中显式注明处理策略。

---

## 四、细节缺漏（中优先级）

### [MINOR-01] 买入阈值边界条件：`>80` 还是 `≥80`？

SDD §9.1：综合评分 **>** 买入阈值（默认 80 分）使用严格大于。  
若用户配置阈值为 80，恰好 80 分不触发买入信号，这是否为预期行为？  
建议在 SDD 中明确说明 `>` 还是 `≥`，并在附录B参数表中注明。

### [MINOR-02] signal_strength 与买入阈值的耦合关系未说明

signal_strength 仅对 ≥80 分的买入信号有意义（STRONG ≥90，MODERATE 80-89）。若用户将买入阈值提高到 90，则所有买入信号都是 STRONG，MODERATE 无意义。若阈值低于 80，则 signal_strength 的含义需要重定义。建议文档中明确 signal_strength 的计算是基于绝对分数还是相对买入阈值。

### [MINOR-03] `index_history` 表缺 `amount`（成交额）字段

SDD §4.3 要求"指数的日线 OHLCV"，`index_history` 表提供了 OHLCV + pct_chg，满足 SDD 要求。但指数成交额（amount）在一些市场情绪指标计算中有用，而 `daily_quote` 表中个股有 `amount` 字段，指数表没有。若未来有需求，需新增字段和 Tushare 采集逻辑。

### [MINOR-04] API 端点未标注鉴权要求

system_design.md §6 API 端点表中，没有任何一列标注"是否需要 JWT 认证"。虽然推测除 `/auth/login` 和 `/auth/refresh` 外所有接口都需要认证，但 `/health` 等运维接口的鉴权策略也应明确。

---

## 五、整体完整性评价

| 维度 | 评价 | 说明 |
|------|------|------|
| SDD 内部一致性 | **良好** | 主要问题：附录A复权术语（TERM-01）、加仓条件表述（LOGIC-01） |
| system_design 内部一致性 | **良好** | 主要问题：注释与代码逻辑不符（SCHEMA-01）、risk_engine 断链（DESIGN-05） |
| 两文档交叉一致性 | **存在重大分歧** | TERM-01（复权术语对立）、LOGIC-01（加仓条件范围不同）是直接矛盾 |
| 操作闭环完整性 | **存在多处断链** | 回测存储、持仓盯市、净值曲线、周报调度、分红处理等均未形成完整链条 |
| Phase 规划与文档对齐 | **较好** | system_design §9 与 CLAUDE.md Phase 分配基本一致 |
| 量化严谨性规范覆盖 | **较好** | PIT、幸存者偏差、后复权回测等核心规范均有体现 |

---

## 六、建议优先修复清单

| 优先级 | 编号 | 位置 | 修复内容 |
|--------|------|------|---------|
| **P0** | TERM-01 | SDD 附录A + system_design §5.2 | 统一前/后复权英文术语，二选一修订 |
| **P0** | LOGIC-01 | system_design §5.9 | 修正加仓条件：条件A（持仓盈利）不应附加"非下跌趋势"限制 |
| **P1** | DESIGN-01 | system_design §4 | 新增 `backtest_task` / `backtest_result` 表定义 |
| **P1** | DESIGN-02 | system_design §2.2 + §5 | 在 DailyPipeline 或 AccountService 中明确每日盯市步骤 |
| **P1** | DESIGN-03 | system_design §4 | 明确净值曲线存储策略（新表 or 实时计算+缓存） |
| **P2** | DESIGN-05 | system_design §5 + §2.2 | 定义 `risk_engine.check()` 返回类型及 Pipeline 中的处理方式 |
| **P2** | DESIGN-06 | system_design §5 | 补充信号状态转换规则（VIEWED/ACTED/SUPERSEDED 的触发时机与责任方） |
| **P2** | DESIGN-12 | SDD §9 + system_design §4.2 | 定义 EXIT/HOLD 信号语义，或从 schema 中移除未使用类型 |
| **P2** | SCOPE-01 | system_design §9 Phase 6 | 注明 V1.0 中 user_level 的简化处理策略 |
| **P3** | SCHEMA-01 | system_design §4.2 | 修正 `user_watchlist` 表 WHITELIST 注释（强制纳入，而非降低阈值） |
| **P3** | DESIGN-07 | system_design §2 | 补充周报调度流程设计（APScheduler Job + WeeklyReport 触发链路） |
| **P3** | DESIGN-08 | system_design §2.2 | 定义 CP1 `data_snapshot_version` 的生成算法 |
| **P3** | DESIGN-04 | SDD §4.2 + system_design §5.1 | 明确 PE/PB 每日更新的计算与存储机制 |
| **P4** | DESIGN-11 | SDD §11.4 + system_design §5.1 | 补充分红处理链路（数据获取、触发时机、成本价调整规则） |
| **P4** | DESIGN-09 | system_design §4.3 | 定义 `position.phase` 字段的取值逻辑和更新责任方 |
| **P4** | DESIGN-10 | system_design §5.5 | 补充因子半衰期计算算法，并说明数据不足时的降级处理 |

---

> **文档说明：** 本报告为专家审阅意见，供开发组在启动后续 Phase 设计文档前参考。优先修复 P0/P1 级问题，P2/P3 可在对应 Phase 启动前完成修复，P4 可延至相关 Phase 实现前处理。
