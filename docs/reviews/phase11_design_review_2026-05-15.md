# Phase 11 设计文档评审

> **评审对象：** `docs/design/phases/phase11_scoring_industrialization.md` v1.0（2026-05-15，951 行）
> **依据文档：** `docs/spec/QuantPilot_SDD.md` v1.4 §7-10 + `docs/design/sdd_7_10_revision_draft_2026-05-14.md` v1.3 + `docs/design/system_design.md` §9 Phase 11 行 + `docs/reviews/sdd_7_10_doc_sync_review_2026-05-14.md` + 既有 `engine/` / `services/` / `models/` / `schemas/` 实现
> **评审角色：** Claude（Opus 4.7）/ 实现可行性评审
> **评审日期：** 2026-05-15
> **结论：** **有条件通过** — 总体设计与 SDD v1.4 / Q1~Q11 决策对齐；但 4 项 P0 与既有代码契合度问题、8 项 P1 接口/范围问题在落地前必须显式回写，否则会在实施中卡顿或形成隐性降级。

---

## 0. 评审快照

| 维度 | 评级 | 说明 |
|---|---|---|
| 与 SDD v1.4 / Q1~Q11 决策对齐 | 🟢 通过 | 5 步管线 / lag 20 / WARMUP 272 / 1.5σ / top 5%/1%/30% / 双重失效 / 三层输出 / Hysteresis 全部转写正确 |
| 子任务划分（P11-A~P11-F）| 🟢 通过 | 6 个子任务覆盖管线 / ICIR / 信号 / 调度 / 配置 / 迁移，依赖关系 §14 已明确 |
| §1.4 推迟项继承 | 🟢 通过 | SDD-EXT-01 / FIN-MED-11/12 / S1-GAP-02 / V1.5-C 因子监控自动降权均列入吸收清单 |
| 模块路径与既有代码对应 | 🔴 不通过 | 多处错位：`services/scoring_service.py`（不存在）/ `schemas/signal.py`（实际 signals.py）/ `schemas/market.py`（实际 scoring.py）/ `factor_quality.py`（已存在却标"新增"）|
| factor_ic_history schema 处理 | 🔴 不通过 | 既有表已是 Phase 7 创建，列名 `calc_month/strategy_name/ic_mean_3m/ir_3m`；设计称"新表"且新 schema 全不同，迁移 0009 是 ALTER / DROP / NEW NAME 未定 |
| MarketSnapshot 扩展 | 🔴 不通过 | §3.0 中性化数据契约依赖 industry / market_cap，但 MarketSnapshot TypedDict 当前缺这两字段；设计 §1.1 未列入"模块变更"|
| BaseStrategy.score() 改造路径 | 🔴 不通过 | 现 score() 内部做 rank-pct 输出 0-100 分；新 5 步管线 Step 1 上移 Scorer 层后，各策略 score() 怎么改 / 是否废弃 / 4 个子类如何调整全部未说明 |
| 数值验收基线 z ≥ 1.8 合理性 | 🟡 偏紧 | 5 步管线下 top 1% 理论 z=2.33 / top 5% z=1.65；1.8 落在 ~3.5%，与 §10.4 "candidate 顶分 z ≥ 1.8" 与 §12.2 "顶分 z ≥ 1.8 且 composite_score ≥ 85" 的对应不一致（85 分 = Φ⁻¹(0.85)≈1.04σ）|
| TDD 覆盖与冒烟编号续接 | 🟢 通过 | API-85~89 续接 Phase 10 API-84；6 unit / 5 integration / 3 e2e 文件命名清晰 |
| 与 system_design §9 估算一致 | 🟡 待回写 | 设计 §14 列 12-18 pd，system_design §9 Phase 11 行仍未列 pd（前评审 P1-3 未闭环）|

**结论：** 4 P0 + 8 P1 + 5 P2。**P0 必须先回写设计文档**再启动实施，否则会在第一周的 ORM/迁移落地时撞上 schema 决策歧义。

---

## 1. P0 阻断 — 必须先回写设计

### P0-1：ScoringService 实际位于 `services/strategy_service.py`，设计文档指向不存在的 `services/scoring_service.py`

**事实：**

```
ls services/scoring_service.py
→ No such file or directory

grep "class ScoringService" services/
→ services/strategy_service.py:27:class ScoringService:
```

设计 §1.1 + §3.4 + §6.3 + §10.1 多处提到 `services/scoring_service.py`，但 `ScoringService` 类目前定义在 `services/strategy_service.py`（约 350 行）。`api/deps.py:25` / `pipeline/daily_pipeline.py:228` 均从 `strategy_service` 导入。

**风险：**

- 如果 Phase 11 真的新建 `scoring_service.py` 并把 ScoringService 搬过去，所有调用方（deps、daily_pipeline、potentially tests）都要改 import 路径 —— 这是个机械但范围广的重构，需要在设计文档显式声明。
- 如果只是文档笔误、实际应"重写 strategy_service.py 中的 ScoringService"，则需要更新所有 §1.1 / §3.4 / §6.3 / §10.1 路径引用。

**修复（任选）：**

- 方案 A：在 §1.1 顶部加注："ScoringService 位于 `services/strategy_service.py`，Phase 11 保持文件路径不变，仅重写类内方法。"
- 方案 B：在 §1.1 顶部加注："Phase 11 将 ScoringService 从 strategy_service.py 拆出到独立的 scoring_service.py（文件重组），影响调用方 import 路径：deps.py / daily_pipeline.py / 单元测试。"（建议放入 §1.2 范围或 §13 风险表。）

### P0-2：factor_ic_history 表已存在（Phase 7），设计称"新表"且 schema 全不同

**事实：**

既有表（`models/business.py` 行 116~139）：

```python
class FactorIcHistory(Base):
    __tablename__ = "factor_ic_history"
    id, calc_month, strategy_name, factor_name,
    ic_value, ic_mean_3m, ic_std_3m, ir_3m, half_life_days,
    return_window, alert_status
    UNIQUE(calc_month, strategy_name, factor_name, return_window)
```

设计 §2.1 "新表 1"：

```
id, strategy, factor, state, trade_date,
ic_value, ic_mean_state, ic_std_state, icir,
sample_size, ic_ci_low, ic_ci_high, t_stat, half_life, created_at
UNIQUE(strategy, factor, state, trade_date)
```

差异：
- **键改名**：`calc_month` → `trade_date`；`strategy_name` → `strategy`；`factor_name` → `factor`
- **类型改**：`half_life_days NUMERIC(6,1)` → `half_life Integer`
- **新增列**：`state` / `sample_size` / `ic_ci_low` / `ic_ci_high` / `t_stat` / `created_at` 共 6 列
- **删除列**：`return_window` / `alert_status` / `ic_mean_3m` / `ic_std_3m` / `ir_3m`（"3m" 改"state" 是语义重写）
- **UNIQUE 约束变更**

**风险：**

- Phase 7 自 2026-05-01 起每月 `run_monthly` 写入此表（含 5y 真机验收中跑出的实际数据）。"新表"措辞与现实冲突。
- 0009 迁移究竟是：
  - (a) **ALTER TABLE** 加 6 列 + 改列名 + 改类型 + 重写 UNIQUE？需要 alembic 迁移可逆 + 旧数据迁移策略（calc_month→trade_date 同值 / ic_mean_3m→ic_mean_state 同列保留 vs 弃用）。
  - (b) **DROP + 重建** 此表？丢失 Phase 7~10 已积累的 IC 历史。
  - (c) **新表用新名**（如 `factor_ic_history_v2`）？需要在 §2.1 显式声明并修正"新表 1：factor_ic_history" 措辞。

不同选项对 Phase 7 `FactorMonitorService.run_monthly`、`/factor-quality/*` 现有端点（如已存在）、`ic_value / ir_3m` 已有持久化数据的连续性影响完全不同。

**修复：**

在 §2.1 顶部加一段"既有 factor_ic_history 处理策略"，说明：
- (a) ALTER 加列 + 同义列保留双写一段时间 + Phase 12 末删除旧列；或
- (b) 用新表名（推荐 `factor_ic_history_state` 或 `factor_ic_window_state`），旧表保留作 Phase 7 baseline 不再写入；或
- (c) DROP 重建，明确接受 5y 真机基线 IC 数据丢失的影响（不推荐）。

并把 §2.1 "新表 1" 标题改为"新表 1（或 ALTER）：factor_ic_history"。

### P0-3：MarketSnapshot 缺 industry / market_cap，设计未列入扩展

**事实：**

既有 `engine/strategies/base.py` MarketSnapshot：

```python
class MarketSnapshot(TypedDict):
    trade_date: date
    adj_prices: pd.DataFrame
    daily_quotes: pd.DataFrame
    financials: pd.DataFrame
    pe_pb_history: pd.DataFrame
    index_adj_prices: pd.DataFrame
```

设计 §3.0 数据契约要求中性化输入：

```
industry: dict[ts_code, industry_code]
market_cap: pd.Series[ts_code -> total_mv]
```

但 §1.1 P11-A "模块" 表只列 "BaseStrategy 输出契约调整 | StrategyScore 新增 `factor_values: dict[str, float]`"——这与中性化需求**对不上**：中性化需要的是**股票属性**（industry / market_cap）而不是 StrategyScore 的因子值（已经有 raw_factors 了）。

**风险：**

实施时会发现 ScoringService 取不到 industry / market_cap，必须临时改 MarketSnapshot 或绕过 snapshot 直接查 DB —— 后者会破坏 Engine 层"纯函数无 IO"约束（§6 代码规范）。

**修复：**

- §1.1 P11-A 模块表新增行：
  ```
  | MarketSnapshot TypedDict 扩展 | engine/strategies/base.py |
    新增 industry: dict[str, str] / market_cap: pd.Series（可选 beta: pd.Series）
  ```
- §3.0 数据契约说明这两字段由 ScoringService 在 `_build_neutralize_snapshot()` 中加载（来源：`StockInfo.industry` ORM + `DailyBasic.total_mv` 取 trade_date PIT 最近行）
- §1.1 "BaseStrategy 输出契约调整 | StrategyScore 新增 `factor_values`" 行**删除或说明用途**——若是给 ICIR 计算用单因子值，应明确（否则与 StrategyScore.raw_factors 字段功能重复）

### P0-4：BaseStrategy.score() 的 rank-pct 在策略内部，5 步管线 Step 1 上移后子类如何改造未说明

**事实：**

既有 `engine/strategies/base.py:55~105` `BaseStrategy.score()` 流程：

```python
raw = self.compute_raw_factors(...)
normalized = raw.rank(pct=True) * 100     # <-- Step 1 (rank-pct) 在策略内
composite = (normalized * weights).sum(axis=1, skipna=False)
return list[StrategyScore(ts_code, raw_factors, score=composite, reason)]
```

4 个具体策略（trend / momentum / mean_reversion / value）继承此 score()，未重写。

Phase 11 5 步管线把 Step 1（横截面归一化 / Winsorize / Z-score）上移到 Scorer 层。设计 §1.1 仅说 "策略内部仍输出 raw_factors"——但**没说**：
- BaseStrategy.score() 是否废弃？是否替换为 `compute_strategy_z()` 输出策略级 z 值？
- 各策略的 `_build_reason` 是否仍用 final_score 文本（"PE 处于 18% 分位"，依赖 0-100 分）？
- 4 个子类的 weights（策略内因子权重）在 5 步管线下还用不用？（SDD §7.2 表注：冷启动默认值；§7.4 ICIR 加权后稳态期由数据驱动 —— 但稳态期 4 子类 weights 是被忽略 / 重新计算？）

**风险：**

实施周第一次写 `Scorer.aggregate` 时会撞上"strategy_z_matrix 怎么来"——若各策略 score() 仍输出 0-100，ScoringService 要先 reverse rank-pct 再做 winsorize/zscore；若各策略只输出 raw_factors，4 个子类的 `_build_reason` 逻辑要重写（依赖 normalized score）。

**修复：**

在 §3.0 / §3.1 之间加一节 "**3.1' BaseStrategy 改造**"：

- 选项 A（推荐）：BaseStrategy.score() 仍保留输出 0-100 分（用于 L1 explanation），同时新增 `compute_strategy_factors(...) -> dict[ts_code, dict[factor_name, raw_value]]` 给 FactorPipeline 用；ScoringService 在 5 步管线后自行计算 strategy_z（不依赖 BaseStrategy 内部 weights）。
- 选项 B：废弃 BaseStrategy.score()，新增 `compute_strategy_z(snapshot) -> pd.Series[ts_code]`，每个子类实现内部按新规范走（更激进，4 子类改动大）。

明确选项后，§1.1 BaseStrategy 行表述需对齐。

---

## 2. P1 主要问题

### P1-1：多处文件路径与既有结构对不上

| 设计文档 | 既有实际位置 |
|---|---|
| `services/scoring_service.py` | `services/strategy_service.py`（同 P0-1） |
| `schemas/signal.py` | `schemas/signals.py`（复数）|
| `schemas/market.py`（指 CandidatePoolItem）| `schemas/scoring.py`（`PoolStockItem`）|
| `schemas/factor_quality.py`（新增）| **已存在**（`FactorIcHistoryItem`），不是新增 |
| `frontend/src/api/signals.ts` / `market.ts` / `factor_quality.ts`（新增） | 需在前端代码中核对 |

**修复：** §9.2 表逐项核对纠正；前端 ts 类型类似处理。

### P1-2：CompositeScore dataclass 缺标量 score 字段，影响 candidate_pool 旧列写入

**事实：**

既有 CompositeScore（`engine/scorer.py:30~40`）：

```python
@dataclass(frozen=True)
class CompositeScore:
    ts_code, composite_score, trend_score, momentum_score,
    reversion_score, value_score, market_state, score_breakdown, explanation
```

Phase 11 新版（§3.3）：

```python
@dataclass(frozen=True)
class CompositeScore:
    ts_code, market_state, composite_z, composite_pct_in_market,
    composite_score, score_breakdown_raw, score_breakdown_residual,
    weights_source, hysteresis_status, explanation
```

少了 `trend_score / momentum_score / reversion_score / value_score` 4 个标量字段——但 §2.1 candidate_pool 表"原列保留"，意味着写入时仍需这 4 列值。

**风险：** ScoringService.write_candidate_pool 实施时必须从 score_breakdown_raw JSONB 提取 4 个策略的某种"分值"反向写入旧 4 列，但 score_breakdown_raw 现在是 `{strategy: {z_raw, weight, contribution}}` ——是 z 不是 0-100 分；映射规则没说。

**修复：** §3.3 CompositeScore dataclass 补回 4 个标量字段（值 = strategy_score_0_100 = Φ(z_raw)×100，旧字段兼容），或 §2.1 明确旧 4 列在 Phase 11 上线后填什么。

### P1-3：FactorMonitorService 既有 `run_monthly()` 与新增 `apply_monthly_rebalance()` 关系不清

**事实：** `services/factor_monitor_service.py:48` `async def run_monthly(calc_month, return_window=20, notifier=None) -> int`，已在 Phase 7 完整实现并被 MonthlyScheduler 调用（5y 真机已写入数据）。

设计 §4.2 新增 `apply_monthly_rebalance(month_end_date) -> dict[str, list[StrategyWeightsHistory]]`——名字不同、签名不同、行为不同（新版包括"决策新一月权重 + 写 strategy_weights_history"）。

**问题：** 没说替换还是并存。两个月度 Job 并存会有 IC 双写（旧 factor_ic_history 列 vs 新列）；MonthlyScheduler `_monthly_job` 是改 dispatch 还是 append？

**修复：** §4 顶部加 "迁移策略"段：Phase 11 将 `run_monthly` 改造为 `apply_monthly_rebalance`（重写 + 改名）；MonthlyScheduler.\_monthly\_job 改 dispatch 到新方法；旧方法保留一段窗口（或删除）。

### P1-4：IC_daily 计算位置未指定

SDD §7.4 表写 IC_daily(s,f,t) **更新频率：每日**。设计 §4.1 `rolling_icir_state(trade_date, ...)` 只计算窗口 ICIR，没说每日 IC 何处写入。

**可能落点：**
- DailyPipeline 新增 CP（如 CP2.5）每日写 factor_ic_history 单点行；
- 或月末 Job 内部一次性补齐当月每日 IC（"批后回算"）；
- 或运行时只算 ICIR 不持久化 IC_daily（与 SDD 表"更新频率：每日"矛盾）。

**修复：** §4 新增子节 "4.0 IC_daily 持久化路径"。如果是月末批后回算，需在 §6 DailyPipeline 不加新 CP（与 SDD 表的"每日"措辞统一为"概念上每日，月末批后写入"）；如果新增 CP 则需补 §6.3 CP 编号 + DoD。

### P1-5：FactorMonitorService session 注入方式不一致

既有 `__init__(self, session, engine)` 把 session 作为构造器依赖；设计方法签名 `rolling_icir_state(self, session, ...)` 把 session 作为参数。

**问题：** 二者并存会有 self.\_session 与方法参数 session 谁优先的歧义。

**修复：** §4 统一为"FactorMonitorService 改为无状态构造（不存 session）+ 所有方法接收 session 参数"，并显式更新 `__init__` 签名为 `__init__(self, engine, repo)`。

### P1-6：State 切换"即时换权"实施细节自相矛盾

§1.1 P11-D 表："State 切换即时换权 | `services/market_state_service.py` + `pipeline/daily_pipeline.py`"——意味着两处都改。

§6.2 实施段："`pipeline/daily_pipeline.py::_cp2_scoring` 已通过 `ScoringService.get_active_weights(trade_date, market_state)` 实时取权重，state 切换时自然换权（无须额外代码）。"——意味着不改 daily_pipeline.py，只在 market_state_service 加日志 hook。

**修复：** §6.2 改为：MarketStateService 不必加 hook（保持现状）；§1.1 P11-D 表把 "State 切换即时换权" 行的"实施位置"列改为只含 "通过 ScoringService.get_active_weights" 说明，删除 market_state_service.py / daily_pipeline.py 引用。

### P1-7：Phase 11 / Phase 12 边界不清——`lineage` 端点字段扩展归谁

§1.2 显式排除："**完整因子级溯源 UI**（L1/L2/L3 分层视图）| Phase 12"。

但 §9.1 表："`GET /signals/{id}/lineage` 响应新增 `score_breakdown_raw` / `score_breakdown_residual` / `factor_winsorized` / `factor_neutralized` / `factor_orthogonal`（前端渲染 Phase 12 完成）"。

且 §11 API-87："带鉴权 200，响应含 `score_breakdown_raw` / `score_breakdown_residual`"——属于 Phase 11 冒烟范围。

**问题：** 后端字段扩展（schema + 端点响应字段）在 Phase 11 完成是合理的；但 Phase 11 §11 冒烟同时要求 API-87 PASS——意味着 LineageService.get_lineage 必须输出这些新字段，这涉及 LineageService 改造（不是单纯前端渲染）。Phase 11 与 Phase 12 在 LineageService 的责任边界未划清。

**修复：** §1.2 排除项改写为 "完整因子级溯源**前端**分层视图（SignalCard / SignalLineageView）"；并在 §1.1 P11-C 模块表追加："LineageService.get_lineage 后端字段扩展 | `services/lineage_service.py` | 响应 dict 新增 5 个 JSONB 字段（前端渲染 Phase 12）"。

### P1-8：§10.4 验收基线 z ≥ 1.8 与 composite_score ≥ 85 的对应关系内部不一致

**事实：**

Phase 11 §10.4 / §12.2 写 "candidate 顶分 **z ≥ 1.8**（top 1%）且**对应 composite_score ≥ 85**"。

**数学：** 5 步管线（含 Step 4b 后 Var(composite_z)≈1，composite_z ~ N(0,1)）下：
- z = 1.8 对应分位 Φ(1.8) = 0.9641 → top 3.59%（不是 top 1%）
- z = 2.33 对应分位 Φ(2.33) = 0.99 → top 1%
- z = 1.04 对应分位 Φ(1.04) = 0.85 → composite_score = 85

所以 "z ≥ 1.8（top 1%）" 矛盾，应为 "z ≥ 2.33（top 1%）" 或 "z ≥ 1.8（top 3.6%）"。
"对应 composite_score ≥ 85" 不需要 z ≥ 1.8，z ≥ 1.04 就够；二者并列时 z 是更紧约束。

**风险：** 验收时若用 z ≥ 1.8 测出 top 5% 范围（z=1.65~2.33），既不是 top 1% 也不是 top 5%，会引发"是否通过"的争论。

**修复：**

- 改为 "顶分 z ≥ 2.0（约 top 2.3%）且 composite_score ≥ 85"，或
- 改为 "top 1% 候选 composite_z ≥ 2.33 且 composite_score ≥ 99 候选 ≥ 30 只"（与 §10.4 "top 1% STRONG ≈ 30~35 只" 一致）

总之需让 z 阈值 / 百分位 / 分数三者数学自洽。

---

## 3. P2 次要问题

### P2-1：§7.1 "新增 11 个常量" 数目对不上

设计 §7.1 列：`ScoringPipelineConfig` 6 字段 + `FactorMonitorConfig` 6 字段 + `SignalPctConfig` 5 字段 = **17 字段**。若按修订草案 §3.2 列的 11 个新增配置项算（NEUTRALIZE_INDUSTRY / NEUTRALIZE_MARKET_CAP / NEUTRALIZE_BETA / ICIR_WARMUP_DAYS / STATE_MIN_SAMPLES / ICIR_LAG_DAYS / HYSTERESIS_ENABLED / SHORT_TERM_FAILURE_SIGMA / BUY_PCT_THRESHOLD / SELL_PCT_THRESHOLD / STRONG_PCT_THRESHOLD = 11），则把 winsorize_lower/upper_pct、ic_window_days、ic_bootstrap_iterations、half_life_window_days、enable_absolute_threshold_override 也算上 = 17 项（≠11）。

**修复：** §7.1 "新增 11 个常量" 改为 "新增 17 项配置（其中 11 项来自修订草案 §3.2 锁定值，另 6 项为 dataclass 内部辅助字段）"。

### P2-2：UNIQUE(strategy, factor, state, trade_date) 字段顺序优化

设计 §2.1 factor_ic_history UNIQUE 列序 `(strategy, factor, state, trade_date)`，附加索引 `(trade_date DESC, strategy)`。

若主要查询模式是 `WHERE strategy=? AND factor=? AND state=? ORDER BY trade_date DESC`（取某 (s,f,state) 时序），当前顺序最优。但如果"按月查所有 (s,f,state) 当月行"（apply_monthly_rebalance 内部），需 `WHERE trade_date=?` 走附加索引。

**修复：** 在 §2.1 表后补一句"主查询模式：(s,f,state) 时序回看为主，月度批量查询走附加索引"——方便未来 DBA 验证。

### P2-3：SignalGenerator.generate 参数命名前后不一

§5.1 描述：`composite_pct_in_market <= params.buy_pct_threshold`，但代码段顶部仍写 `composite_scores: pd.DataFrame`（变量名继承 V1.0-r5）。

§5.3 SignalService 段又用 `candidates = await self._repo.get_candidate_pool_with_orthogonal(...)`，下面 `self._generator.generate(candidates=candidates, ...)`——参数名 `candidates`。

**修复：** SignalGenerator.generate 入参重命名 `composite_scores` → `candidates`，§5.1 与 §5.3 命名一致。

### P2-4：`_max_strategy_z_drop_1d` 数据源说法过简

§5.2 描述："从 SignalScoreSnapshot 取昨日和今日 strategy_z_orthogonal_normalized 列"——但 signal_score_snapshot 新增列名是 `factor_orthogonal`（JSONB），未明确是从 JSONB 字段解出哪个 key、是否每个策略一行还是合并一行、空值时怎么 fallback。

**修复：** §5.2 加示例 JSON：
```json
{
  "trend": {"z_raw": 1.4, "z_orthogonal_normalized": 1.2},
  "momentum": {"z_raw": 0.9, "z_orthogonal_normalized": 0.6},
  ...
}
```
并说明 `_max_strategy_z_drop_1d` 取 top_contributor 策略（按 contribution 排序的第一个）的 z_orthogonal_normalized 跨日差。

### P2-5：估算与 system_design §9 仍未同步

设计 §14 列 "12-18 pd（参考 system_design §9 估算）"——但 system_design §9 Phase 11 行**没有** pd 估算（详见前置 `sdd_7_10_doc_sync_review_2026-05-14.md` P1-3，仍未闭环）。

**修复：** 把 "12-18 pd" 同步回写到 system_design §9 Phase 11 行末尾（或 §9 表后注解）；Phase 11 设计 §14 改为 "（详见 §14 子任务分解）" 而非循环引用 system_design。

---

## 4. 总体设计亮点（确认通过项）

| 项 | 评价 |
|---|---|
| §0~§1 范围声明 + 推迟项 + SDD 裁决表 + 1.4 继承清单 + 1.5 待定项收敛 | 完整严谨，按 CLAUDE.md §5 启动核查规则到位 |
| §2.1 数据回填策略（旧列保留 / 新列 NULL）| 明智的兼容策略 |
| §3.3 三层输出 dataclass + explanation 文本规则 | 严格对齐 SDD §7.6 / §9.3 |
| §3.3 single_strategy_mode 跳过 Step 4/5 | 与 Q11 锁定决策一致 |
| §4.3 HysteresisStateMachine 4 状态转换 + first_month 处理 | 数学清晰可单元测试 |
| §4.4 R1~R4 因子下线规则 | 与 SDD §7.4 表一一对应 |
| §5.2 双重失效止损 4 trigger_reason | 与 SDD §9.2 / Q9 一致 |
| §8.1 BacktestEngine 共内核约束（不嵌简化版 Scorer） | 与 SDD §7.7.1 一致 |
| §10.2 集成测试 5 个场景（含 hysteresis / state_change_reweight）| 覆盖关键路径 |
| §13 风险表 6 项（性能 / 共线退化 / industry 缺失等）| 实施前预判到位 |
| §14 子任务依赖图 | 串行依赖清晰，可作为实施排期基线 |

---

## 5. 修订动作建议（按优先级）

### 启动实施前必做（≤2 小时）

| # | 动作 | 涉及章节 |
|---|---|---|
| 1 | P0-1：澄清 ScoringService 是文件搬迁还是原地重写，修正 §1.1 / §3.4 / §6.3 / §10.1 路径 | §1.1 顶部加注 |
| 2 | P0-2：factor_ic_history 处理策略明确 ALTER / DROP / 新表名（推荐 ALTER 加列 + 同义列保留） | §2.1 顶部 |
| 3 | P0-3：MarketSnapshot 扩展 industry / market_cap 写入 §1.1 模块表 + §3.0 数据契约 | §1.1 / §3.0 |
| 4 | P0-4：BaseStrategy.score() 改造路径明确（推荐选项 A 保留 + 新增 compute_strategy_factors）| §3 新增 §3.1' |

### 启动后同步处理（实施周内）

| # | 动作 | 涉及章节 |
|---|---|---|
| 5 | P1-1：文件路径逐项纠正（signals.py / scoring.py / factor_quality 已存在）| §9.2 |
| 6 | P1-2：CompositeScore 补 4 标量字段（与旧 candidate_pool 列对接）| §3.3 |
| 7 | P1-3：run_monthly vs apply_monthly_rebalance 迁移策略 | §4 顶部 |
| 8 | P1-4：IC_daily 持久化路径 | §4 新增 §4.0 |
| 9 | P1-5：FactorMonitorService session 注入方式统一 | §4 |
| 10 | P1-6：State 切换换权实施位置二选一 | §1.1 + §6.2 |
| 11 | P1-7：LineageService 后端字段扩展明确归 Phase 11 | §1.1 / §1.2 / §11 |
| 12 | P1-8：验收基线 z 阈值与百分位 / 分数自洽 | §10.4 / §12.2 |

### Phase 11 收尾前补齐

| # | 动作 | 涉及章节 |
|---|---|---|
| 13 | P2-1~P2-5 文档微调 | 各节 |
| 14 | system_design §9 Phase 11 行加 12-18 pd 估算（同时解决前置评审 P1-3）| system_design §9 |

---

## 6. 评审结论

**整体评级：有条件通过**

- ✅ **设计骨架与 SDD v1.4 / Q1~Q11 决策对齐**：5 步管线、ICIR 加权、分位阈值、双重失效、Hysteresis、三层输出、单策略回测跳过等核心决策完整且数学一致。
- ✅ **子任务划分与 TDD 测试策略充分**：P11-A~F 6 个子任务串行依赖明确，14 个测试文件覆盖单元 / 集成 / E2E / 冒烟 / 跨制度回归。
- ✅ **风险预判 6 项 + 推迟项 5 项**：超出常规 phase 设计文档的稳健度。
- 🔴 **4 项 P0 与既有代码契合度问题**：scoring_service.py 不存在 / factor_ic_history 已存在却被标新表 / MarketSnapshot 缺字段 / BaseStrategy.score() 改造路径模糊。这 4 项**必须先回写设计**再进入实施，否则会在第一周的迁移落地时撞上不可绕过的歧义。
- 🟡 **8 项 P1 接口/范围/验收基线问题**：可与实施并行迭代修订，但需在 Phase 11 收尾前清零，否则形成隐性降级。

**建议下一步：**

1. **回写设计文档 v1.1**（≤2 小时）：处理 4 项 P0，让设计文档与既有代码结构对齐
2. **启动 P11-F 迁移 0009**：作为最早期可独立交付的子任务，先做 schema 实施回压 §2.1 schema 决策的正确性
3. **同步回写 system_design §9 Phase 11 行加 pd 估算**（解决前置评审 P1-3）
4. **Phase 11 实施周中**：每周对照本评审 §5 修订动作清单逐项闭环

**不阻断 Phase 11 启动**——前提是 P0 在第一周内回写完成。

---

## 7. 签署

| 项 | 值 |
|---|---|
| 评审人 | Claude（Opus 4.7）/ 实现可行性评审 |
| 评审日期 | 2026-05-15 |
| 评审依据 | SDD v1.4 §7-10 + 修订草案 v1.3 + system_design §9 + 既有 backend/src 实现 |
| 评审输出 | 4 P0 + 8 P1 + 5 P2 + 14 修订动作 |
| 阻断 Phase 11 启动 | ❌ 否（前提：4 项 P0 在第一周内回写完成）|
