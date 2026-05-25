# Phase 14 设计 v1.1 短复审报告（§5 方案 A 函数签名锁定）

- 复审日期：2026-05-25
- 复审范围：**仅 §5（§14-3 BacktestEngine 真 5 步管线接入，采方案 A）函数签名级核查**
- 依据：v1.0 评审报告 §9.1 "v1.1 通过后启动 TDD 的依赖闭环验证"第 2 项 "engine/scoring/pipeline.py 函数签名草稿写入 §5.2 + 与 ScoringService.score_universe:399-… 既有 5 步代码对齐验证"
- 复审基线：commit `9c33949`（Phase 14 v1.1 推送后）
- 复审产出：**v1.2 微修订建议（不阻断 TDD，但建议合入后再启动 §14-3）**

---

## 0. 复审结论

**结论：v1.1 §5 方案 A 在架构方向上正确，但函数签名层面存在 3 项"已经存在的轮子"被设计为"新建"的误判，导致工作量虚高 + 实施期会发现冗余抽象。建议出 v1.2 微修订（~0.2 pd），把 §5.2 从"新增 engine 层 + Scorer 新方法"改为"直接复用既有 `Scorer.aggregate`"，工作量真实降低到原估算的 ~60%。**

| 等级 | 项数 | 备注 |
|------|------|------|
| C-1（critical，必须 v1.2 改）| 3 项 | 误判"新建"——既有 `engine/factor_pipeline.py::FactorPipeline` + `engine/orthogonalizer.py` + `engine/scorer.py::Scorer.aggregate` 已是完整 5 步管线 |
| C-2（实施期会遗漏的细节，建议 v1.2 补）| 4 项 | `MarketSnapshot` 字段补完 / `WINSORIZE_MIN_SAMPLES` 常量定义点 / `float_mkt_cap` 列加载缺失 / `s.score` → `s.compute_strategy_factors` 切换 |
| C-3（措辞优化，可不改）| 1 项 | §14-3 估算 1-1.5 pd 可调降为 0.6-1 pd |

---

## 1. 既有代码事实核查（基准 commit `9c33949`）

### 1.1 engine 层 5 步管线已完整存在

| 5 步管线步骤 | 文件:行 | 函数 | 是否纯函数 |
|--------------|---------|------|-----------|
| Step 1 Winsorize | `engine/factor_pipeline.py:54` | `FactorPipeline.winsorize(values)` | ✅ |
| Step 2 行业+市值中性化 | `engine/factor_pipeline.py:73` | `FactorPipeline.neutralize(values, industry, market_cap, beta)` | ✅ |
| Step 3 Z-score | `engine/factor_pipeline.py:176` | `FactorPipeline.zscore(values)` | ✅ |
| Step 4a Gram-Schmidt | `engine/orthogonalizer.py` | `Orthogonalizer.compute(strategy_z_matrix, order)` | ✅ |
| Step 4b 残差再标准化 + Hysteresis | `engine/scorer.py:255` | `Scorer.aggregate` 内联 | ✅ |
| Step 5 三层输出 | `engine/scorer.py:282-393` | `Scorer.aggregate` 内联（composite_z / composite_pct_in_market / composite_score）| ✅ |

**整合入口**：`engine/scorer.py:120` `Scorer.aggregate(market_state, strategy_factors, snapshot, weights_runtime, weights_source, orthogonalize_order, hysteresis_status, single_strategy_mode=False) -> list[CompositeScore]`

该方法**已经满足方案 A 的全部需求**：
- 纯函数（无 IO，所有依赖通过参数注入）
- snapshot 含 `industry` / `market_cap` / `beta` 字段（MarketSnapshot TypedDict Phase 11 §3.0 P0-3 已扩展）
- 输出 `list[CompositeScore]`，每行含 `composite_z` / `composite_pct_in_market` / `composite_score` / `score_breakdown_raw` / `score_breakdown_residual` / `factor_winsorized` / `factor_neutralized` / `factor_orthogonal`（Phase 11+12 全字段）
- 已被 `ScoringService.score_universe:457` 在生产 critical path 调用 5 年验证

### 1.2 ScoringService.score_universe 是 service 层 orchestrator（不能直调）

`services/strategy_service.py:399-472` 的真实职责：

```python
async def score_universe(session, trade_date, universe, market_state) -> list[CompositeScore]:
    # 1. IO: _build_market_snapshot（DB 并发查询 adj_prices/snapshot_quotes/...）  ← 必须 service 层
    # 2. 并发跑各 strategy.compute_strategy_factors（纯函数）  ← 可以挪出
    # 3. IO: factor_monitor.get_active_weights(session, ...)  ← 必须 service 层
    # 4. self._scorer.aggregate(...)  ← 已经是 engine 层
```

**结论**：`score_universe` 是 orchestrator（IO + 编排），不是算法主体。算法主体 = `Scorer.aggregate` 已经在 engine 层。BacktestEngine 不需要也不应该调用 `score_universe`（async + 需 session）；BacktestEngine 直接调 `Scorer.aggregate` 即可。

### 1.3 BacktestEngine 当前路径核查

`engine/backtest/engine.py:240-273` 现状：

```python
# 主循环第 f 步：构建 MarketSnapshot
market_snap: MarketSnapshot = {
    "trade_date": trade_date,
    "adj_prices": adj_hist,
    "daily_quotes": quotes_t,
    "financials": financials_t,
    "pe_pb_history": pe_pb_t,
    "index_adj_prices": idx_adj_t,
    # ❌ 缺：industry / market_cap / beta（Phase 11 §3.0 P0-3 新增字段）
}

# 第 f 步循环每个策略
for s in self._strategies:
    score = s.score(universe_idx, market_snap)         # ❌ 返回 list[StrategyScore]（0-100）
    strategy_scores_dict[s.name] = score                #   是 Phase 4 旧路径

# 第 g 步聚合
composite_scores = self._scorer.aggregate_legacy(     # ❌ 走 legacy 路径
    market_state, strategy_scores_dict
)
```

**真正要改的 3 处**：
1. MarketSnapshot 补 industry / market_cap（beta 留 None）
2. `s.score(...)` → `s.compute_strategy_factors(...)`（返回 `pd.DataFrame[ts_code × factor]`）
3. `aggregate_legacy(market_state, strategy_scores_dict)` → `aggregate(market_state, strategy_factors, snapshot, weights_runtime, weights_source, order, hysteresis_status)`

### 1.4 BacktestService 数据加载核查

`services/backtest_service.py:165-333` `_load_data_bundle` 现状：

- ✅ 已加载：daily_quotes 完整字段 / stock_info 含 sw_industry_l1 / pe_pb_history / hs300 / adj_prices
- ❌ **未加载 daily_quote.float_mkt_cap 列**（`models/market.py:53` 已有此列，但 line 193-207 select dq_rows 后构造 dq_df 未取该列）→ BacktestEngine 主循环无法从 quotes_t 派生 market_cap
- ❌ **未加载 strategy_weights_history**（5y active_weights 时序）→ BacktestEngine 主循环无法 PIT 查询 trade_date 对应的 active weights snapshot

### 1.5 缺失的 WINSORIZE_MIN_SAMPLES 常量

- `engine/scorer.py` / `engine/factor_pipeline.py` / `services/strategy_service.py` 全代码 grep 均**无** `WINSORIZE_MIN_SAMPLES` 或类似常量
- `Scorer.aggregate` 自身无硬性 universe 下限检查（只在每个 strategy_df 全 NaN/empty 时跳过该策略）
- v1.1 §5.2 + R14-OPEN-3 + P3-4 三处引用"WINSORIZE_MIN_SAMPLES"常量，但未指明定义点

**结论**：Phase 14 §14-3 需要**自己在 engine 层新定义**该常量（建议落 `engine/scorer.py` 或 `engine/factor_pipeline.py` 顶部），而非"从 ScoringService import"。

---

## 2. v1.2 微修订建议（共 8 项）

### 2.1 C-1（必须 v1.2 改，3 项）

#### C-1-1：删除"新增 engine/scoring/pipeline.py 纯函数"

**v1.1 §5.2.2 原文**：
```
engine 层新增 `engine/scoring/pipeline.py`：
def run_scoring_pipeline(strategy_factor_dfs, market_state, weights_snapshot,
                         industry, market_cap, *, config) -> CompositeResultDataFrame: ...
```

**实证**：`engine/factor_pipeline.py::FactorPipeline.winsorize/neutralize/zscore` + `engine/orthogonalizer.py::Orthogonalizer.compute` + `engine/scorer.py::Scorer.aggregate` 已完整实现 5 步。新建 `engine/scoring/pipeline.py` 会与既有代码产生重复抽象。

**修订建议**：v1.2 §5.2 删除 5.2.2 整节；只保留 "直接复用既有 `Scorer.aggregate`" 一句话说明。

#### C-1-2：删除"Scorer 新增 aggregate_pipeline() 公开方法"

**v1.1 §5.2.3 原文**：
```
class Scorer:
    def aggregate_pipeline(self, strategy_factors, market_state, weights_snapshot,
                           industry, market_cap, config) -> ...:
        try:
            return run_scoring_pipeline(...)
        except InsufficientUniverseError:
            raise
```

**实证**：`Scorer.aggregate` 签名已与方案 A 需求一致——参数 `market_state` / `strategy_factors: dict[str, pd.DataFrame]` / `snapshot: MarketSnapshot`（含 industry / market_cap / beta）/ `weights_runtime` / `weights_source` / `orthogonalize_order` / `hysteresis_status` / `single_strategy_mode`。

**修订建议**：v1.2 §5.2 删除 5.2.3 整节；§5.2 主体改为"BacktestEngine 直接调 `Scorer.aggregate(...)`（既有方法，无需 Scorer 改动）"。

#### C-1-3：删除"InsufficientUniverseError 异常类"

**v1.1 §5.2.3 提及的 `InsufficientUniverseError`** 不存在（既有代码 grep 无此类）。`Scorer.aggregate` 自身的小 universe 行为是"全策略可能产出空结果 → return []"，不抛异常。

**修订建议**：v1.2 §5 删除该异常类引用；改为 BacktestEngine 主循环显式判 `len(universe) < WINSORIZE_MIN_SAMPLES`（见 C-2-2）→ 直接走 `aggregate_legacy` 不调 `aggregate`。

### 2.2 C-2（建议 v1.2 补 4 项细节）

#### C-2-1：MarketSnapshot 构造在 BacktestEngine 主循环补 industry / market_cap / beta

**当前缺口**：`engine/backtest/engine.py:240-247` 仅 6 个 key，缺 Phase 11 §3.0 P0-3 新增的 industry / market_cap / beta。BacktestEngine 改走 `Scorer.aggregate` 后，Step 2 中性化阶段会 `snapshot.get("industry") or {}` 取空 dict → 无行业中性化降级（合规但不准确）。

**修订建议**：v1.2 §5.2 实施细节补：

```python
# engine/backtest/engine.py 主循环 f 步
# 从 stock_info_t 派生 industry dict（PIT）
industry_map: dict[str, str] = {}
if "sw_industry_l1" in stock_info_t.columns:
    sw = stock_info_t["sw_industry_l1"].dropna()
    industry_map = {str(k): str(v) for k, v in sw.items()}

# 从 quotes_t 派生 market_cap Series（PIT，需 BacktestService 加载 float_mkt_cap 列，见 C-2-3）
market_cap_series: pd.Series | None = None
if "float_mkt_cap" in quotes_t.columns:
    market_cap_series = quotes_t["float_mkt_cap"].dropna().astype(float)

market_snap: MarketSnapshot = {
    "trade_date": trade_date,
    "adj_prices": adj_hist,
    "daily_quotes": quotes_t,
    "financials": financials_t,
    "pe_pb_history": pe_pb_t,
    "index_adj_prices": idx_adj_t,
    "industry": industry_map,
    "market_cap": market_cap_series,
    "beta": None,  # V1.0 永远 None
}
```

#### C-2-2：WINSORIZE_MIN_SAMPLES 常量定义点 + 引用路径

**修订建议**：v1.2 §5.2 + R14-OPEN-3 + P3-4 三处统一：

- **定义点**：`engine/scorer.py` 顶部加 `WINSORIZE_MIN_SAMPLES = 30  # 5 步管线 Winsorize 横截面最小样本（< 30 → 走 legacy_fallback）`
- **引用方**：BacktestEngine 主循环 `from quantpilot.engine.scorer import WINSORIZE_MIN_SAMPLES`；不是从 ScoringService import（ScoringService 也没有此常量）
- **R14-OPEN-3 措辞更新**：删 "与 ScoringService 既有常量同源"，改为 "Phase 14 §14-3 在 engine/scorer.py 新定义；ScoringService.score_universe 未来若需同等门槛检查可同源 import"

#### C-2-3：BacktestService._load_data_bundle 补加载 float_mkt_cap + active_weights_history

**当前缺口**：
- `_load_data_bundle:193-207` daily_quotes DataFrame 构造未取 `r.float_mkt_cap` 列
- 全无 `active_weights_history` 加载逻辑

**修订建议**：v1.2 §5.2.4 实施细节明确：

```python
# 1. daily_quotes 行字典追加 float_mkt_cap
"float_mkt_cap": float(r.float_mkt_cap) if r.float_mkt_cap is not None else None,

# 2. 新增 BacktestDataBundle 字段
@dataclass
class BacktestDataBundle:
    # ... 既有字段
    # Phase 14 §14-3：5y active_weights 时序（按 market_state + effective_date PIT 切片）
    # 结构：{(market_state_str, effective_date): {strategy: weight, weights_source, order, hysteresis_status}}
    # 主循环用 max(effective_date) <= trade_date 做前向查找（月末 snapshot）
    active_weights_history: dict[tuple[str, date], dict] = field(default_factory=dict)

# 3. _load_data_bundle 末尾新增加载
from quantpilot.models.business import StrategyWeightsHistory
sw_rows = (await self._session.execute(
    select(StrategyWeightsHistory)
    .where(StrategyWeightsHistory.effective_date <= config.end_date)
    .order_by(StrategyWeightsHistory.effective_date)
)).scalars().all()
active_weights_history = {
    (r.market_state, r.effective_date): {
        "weights": r.weights_json,
        "weights_source": r.weights_source,
        "orthogonalize_order": r.orthogonalize_order_json or [],
        "hysteresis_status": r.hysteresis_status or "stable",
    }
    for r in sw_rows
}
```

并在 §5.2.5 BacktestEngine 主循环加 helper `_lookup_active_weights(trade_date, market_state, bundle)`，前向查找最近 effective_date ≤ trade_date 的 snapshot；找不到 → 返回 (None, "default_matrix", [], "stable") 触发 fallback。

#### C-2-4：BacktestEngine 主循环 s.score → s.compute_strategy_factors 切换

**当前**：`engine/backtest/engine.py:251` `score = s.score(universe_idx, market_snap)`（返回 `list[StrategyScore]`，Phase 4 旧路径）

**修订建议**：v1.2 §5.2.5 明示：

```python
# 旧：
score = s.score(universe_idx, market_snap)
strategy_scores_dict[s.name] = score  # list[StrategyScore]

# 新（与 ScoringService.score_universe:422-430 一致）：
factor_df = s.compute_strategy_factors(universe_idx, market_snap)  # pd.DataFrame[ts_code × factor]
strategy_factors[s.name] = factor_df
```

并明示：`compute_strategy_factors` 是 `BaseStrategy:65` 的默认实现（已透传 `compute_raw_factors`），无需新策略接口改动。

### 2.3 C-3（措辞 / 估算）

#### C-3-1：§14-3 估算 1-1.5 pd 调降为 0.6-1 pd

**理由**：删除 "engine/scoring/pipeline.py 新建" + "Scorer 新方法" + "InsufficientUniverseError 新类" 三块（合计 ~0.4-0.5 pd）后，§14-3 实际工作量只剩：
- BacktestService 加载 float_mkt_cap + active_weights_history（~0.2 pd）
- BacktestEngine 主循环 3 处改造 + helper（~0.3 pd）
- UT/INT 4 项测试（~0.1-0.3 pd）

**修订建议**：v1.2 §1.2 表 §14-3 行估算从 `1-1.5` → `0.6-1`；§1.2 合计仍标 ~5-8 pd（其他子项扣留 buffer）。system_design v1.9 §9 不必再次同步（5-8 pd 范围不变）。

---

## 3. v1.2 重写后的 §5.2 草案（建议合入文本）

```markdown
### 5.2 实施路径（方案 A：直接复用既有 Scorer.aggregate）

#### 5.2.1 架构事实

`engine/scorer.py::Scorer.aggregate(market_state, strategy_factors, snapshot,
weights_runtime, weights_source, orthogonalize_order, hysteresis_status,
single_strategy_mode=False) -> list[CompositeScore]` 已是 engine 层纯函数 5 步管
线完整入口（基于 `FactorPipeline` + `Orthogonalizer`），已被 `ScoringService.
score_universe:457` 在生产 critical path 5 年验证。BacktestEngine 直接调用即可，
无需新增 engine 层抽象。

#### 5.2.2 BacktestService 数据预加载扩展

`_load_data_bundle` 追加两项：
1. daily_quotes DataFrame 行字典补 `float_mkt_cap`（既有 `models/market.py:53` 列）
2. 新增加载 `strategy_weights_history` 全表 → `bundle.active_weights_history:
   dict[(market_state, effective_date), dict]`

新增 `BacktestDataBundle.active_weights_history` 字段。

#### 5.2.3 BacktestEngine 主循环改造（共 3 处）

1. **MarketSnapshot 构造补字段**（§5.2 C-2-1 代码片段）：从 stock_info_t 派生
   `industry`，从 quotes_t 派生 `market_cap`，`beta=None`
2. **策略循环改用 compute_strategy_factors**：`s.score(...)` → `s.compute_
   strategy_factors(...)`，累积成 `dict[str, pd.DataFrame]`
3. **聚合分支二路径**：
   ```python
   from quantpilot.engine.scorer import WINSORIZE_MIN_SAMPLES  # 新常量，§5.2.4
   weights_record = self._lookup_active_weights(
       trade_date, market_state_str, data.active_weights_history,
   )
   if (len(universe_idx) < WINSORIZE_MIN_SAMPLES
       or weights_record["weights"] is None):
       composite_scores = self._scorer.aggregate_legacy(
           market_state, strategy_scores_dict,
       )
       pipeline_mode = "legacy_fallback"
   else:
       composite_scores = self._scorer.aggregate(
           market_state=market_state,
           strategy_factors=strategy_factors,
           snapshot=market_snap,
           weights_runtime=weights_record["weights"],
           weights_source=weights_record["weights_source"],
           orthogonalize_order=weights_record["orthogonalize_order"],
           hysteresis_status=weights_record["hysteresis_status"],
       )
       pipeline_mode = "real_5step"
   ```
4. **BacktestResult 写 pipeline_mode 字段**：`"real_5step" | "legacy_fallback"`
   供前端展示

#### 5.2.4 新增 WINSORIZE_MIN_SAMPLES 常量

`engine/scorer.py` 顶部加：
```python
WINSORIZE_MIN_SAMPLES = 30  # 5 步管线 Winsorize 横截面最小样本（< 30 → 走 legacy_fallback）
```

#### 5.2.5 新增 helper _lookup_active_weights

`engine/backtest/engine.py` 私有方法（纯函数）：给定 `trade_date`/`market_state`
+ `active_weights_history`，前向查找 `max(effective_date) <= trade_date AND
market_state` 的 snapshot；找不到 → 返回 `{"weights": None, "weights_source":
"default_matrix", "orthogonalize_order": [], "hysteresis_status": "stable"}`
触发降级路径。
```

---

## 4. v1.2 修订涉及范围（精确清单）

| 文件 | 改动 |
|------|------|
| `docs/design/phases/phase14_account_integrity.md` | §5.2.1~5.2.5 重写（按 §3 草案）；§1.2 表 §14-3 估算 1-1.5 → 0.6-1；§5.3 测试表保留不变；R14-OPEN-3 措辞收紧；P3-4 引用路径修正；修订历史追加 v1.2 条目 |
| `docs/reviews/phase14_design_review_2026-05-25.md` | §8 修订追踪表 P1-2 行追加 "v1.2 短复审收口：发现 §5.2 设计冗余（既有 Scorer.aggregate 已是 5 步入口），简化为直接复用既有方法 + BacktestEngine 主循环 3 处改造" |
| `docs/reviews/phase14_design_review_v1_1_short_2026-05-25.md` | 新建（本文）|
| `docs/design/system_design.md` | **无需改动**（5-8 pd 总估算不变）|

---

## 5. 启动 TDD 的依赖闭环验证（v1.0 评审 §9.1 落地）

| 验证项 | v1.2 通过后验收条件 |
|--------|---------------------|
| §1.3 grep 三链 | ✅ v1.1 已通过（无变化）|
| §5.2 engine 层抽象 | v1.2 改为 "直接复用既有 `Scorer.aggregate`"，无需新建 → 工作量从 ~1.5pd 降到 ~0.6-1pd |
| §14-1 deposit 既有签名兼容 | ✅ v1.1 已确保（FundFlowCreate 扩字段不破坏）|

---

## 6. 评审决策

- **v1.1 可直接进入 §14-1 / §14-5 / §14-6 / §14-7 / §14-8 TDD 实施**（这 5 子项不受 §5 影响）
- **§14-3 实施前必须先合入 v1.2 §5 修订**（避免落入"新建 pipeline.py 然后发现是冗余"的实施期返工）
- **§14-2 5y 回填可与 v1.2 并行启动**（脚本编写与 §5 解耦）

---

> **依据 CLAUDE.md §11 充分理由**：本次短复审发现的 3 项 C-1 + 4 项 C-2 均"现在改"——v1.2 修订工作量 ~0.2 pd，避免实施期 ~0.5-1 pd 返工 + 冗余抽象长期维护成本。
