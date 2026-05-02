# Phase 4 设计文档审查报告

> **审查日期**：2026-04-01
> **审查对象**：`docs/design/phases/phase4_factor_engine.md`（v1.0）
> **依据文档**：SDD v1.0、system_design.md v1.2、CLAUDE.md
> **审查范围**：自身完整性、与 SDD/system_design 整合性、代码规范符合性

---

## 总体评价

文档结构清晰，TD 修复规格、策略因子设计、测试覆盖均有实质性改善。但存在 **5 个 P1 问题**（实现前必须修复）和 **5 个 P2 问题**（影响正确性或可维护性），需处理后方可开始编码。

---

## P1 问题（实现前必须修复）

### C-01：CandidatePoolManager 在 Engine 层执行 DB I/O，违反架构约束

**位置**：§5.8，`engine/pool.py`，`update()` 方法

**问题**：Phase 4 明确标注"Engine 层，**异步**方法（需访问 DB）"，但 CLAUDE.md §6 规定"Engine 层（`engine/`）严格无 IO（数据库、文件、网络）"。`update()` 内含两处 IO 操作：
1. `await self.repo.get_whitelist_codes()`（DB 读）
2. `await self.repo.upsert_candidate_pool(...)`（DB 写）

**修复方案**：将 CandidatePoolManager 拆分为纯函数层 + Service 层调用：

```python
# engine/pool.py（纯函数，无 IO）
class CandidatePoolManager:
    def compute_pool(
        self,
        composite_scores: list[CompositeScore],
        holding_codes: set[str],
        whitelist_codes: set[str],   # 由 ScoringService 注入
    ) -> list[PoolEntry]:            # 返回入池结果，不写 DB
        ...

# services/strategy_service.py（ScoringService 承接 IO）
whitelist_codes = await self.repo.get_whitelist_codes()
pool_entries = self.pool_manager.compute_pool(composite_scores, holding_codes, whitelist_codes)
for entry in pool_entries:
    await self.repo.upsert_candidate_pool(...)
```

---

### C-02：MomentumStrategy 追高剔除代码逻辑错误（不会生效）

**位置**：§5.5，`score()` 追高剔除约束代码块

**问题**：`for s in result: s = StrategyScore(...)` 只是对循环变量本地重绑定，不会修改 `result` 列表中的元素。由于 `StrategyScore` 是 `@dataclass(frozen=True)`，也无法原地修改。测试 MOM-02 在此实现下将**直接失败**（评分不会被置 0）。

**修复**：改为列表推导式：

```python
result = [
    StrategyScore(s.ts_code, s.raw_factors, score=0.0, reason="近1月涨幅前5%，追高剔除。")
    if return_1m.get(s.ts_code, 0) >= top5pct_threshold
    else s
    for s in result
]
```

---

### C-03：ValueStrategy 价值陷阱截断同一逻辑错误

**位置**：§5.6，`score()` 价值陷阱规避代码块

**问题**：与 C-02 完全相同——`for s in result: s = StrategyScore(...)` 不会修改列表，测试 VAL-02 将失败。

**修复**：同 C-02，改为列表推导式。

---

### C-04：ScoringService 向 Scorer 传入类型不匹配

**位置**：§5.9，`run_daily_scoring()` 步骤 5→6

**问题**：

```python
# 步骤 5：gather 返回 tuple[list[StrategyScore], ...]
strategy_scores = await asyncio.gather(*[asyncio.to_thread(s.score, ...) for s in strategies])

# 步骤 6：但 Scorer.aggregate() 期望 dict[str, list[StrategyScore]]
Scorer.aggregate(market_state, strategy_scores)  # 类型不匹配！
```

`asyncio.gather` 返回的是与传入协程对应顺序的元组，不是以策略名为 key 的字典。

**修复**：在步骤 5 和 6 之间加入转换：

```python
scores_by_name = {
    s.name: scores
    for s, scores in zip(self.strategies, strategy_scores)
}
composite_scores = self.scorer.aggregate(market_state, scores_by_name)
```

---

### C-05：UniverseFilter 缺少 SDD §5.4 两个必要过滤条件

**位置**：§5.1，过滤规则表（F-1 至 F-6）

**问题**：Phase 4 设计只实现 6 条过滤规则，但 SDD §5.4 规定 8 条硬性过滤条件：

| SDD 条件 | Phase 4 状态 |
|---------|-------------|
| ST/\*ST 排除 | ✅ F-1 |
| 上市不足 60 交易日 | ✅ F-2 |
| 当日停牌 | ✅ F-3 |
| 净资产为负 | ✅ F-4 |
| 连续亏损 | ✅ F-5 |
| 高杠杆 | ✅ F-6 |
| **20 日均日成交额 < 500 万元（可配置）** | ❌ **缺失** |
| **当日涨停封死（流动性不足）** | ❌ **缺失** |

缺失的两条是重要的流动性过滤，遗漏会导致评分系统推荐实际无法买入的标的。

**修复**：在 UniverseFilter.filter() 中补充：
- F-7：20 日均成交额过滤（`daily_quotes` 中 `amount` 字段的 rolling 均值）
- F-8：当日涨停封死过滤（`limit_up=True` 且 `vol == 0` 或类似判断）

并将 F-7 阈值（500 万元）作为可配置参数，与 SDD §14.3 用户配置体系对齐。

---

## P2 问题（影响正确性或可维护性）

### C-06：signal_score_snapshot 写入时机与 FK 约束冲突

**位置**：§5.9，`run_daily_scoring()` 步骤 8；§6 数据库 Schema

**问题**：`signal_score_snapshot.signal_id` 是指向 `signal(id)` 的外键，而 Signal 在 Phase 5 才生成。Phase 4 在 `run_daily_scoring()` 中写入 `signal_score_snapshot` 时，没有合法的 `signal_id`。

设计文档未说明：
- `signal_id` 是否为 nullable（允许 Phase 4 写入无信号绑定的快照）？
- 还是 Phase 4 根本不应写 `signal_score_snapshot`，此步骤应推迟至 Phase 5？

**修复建议**：明确选择一种方案并在文档中注明：
- **方案 A**：确认 Phase 1 迁移中 `signal_id` 为 nullable，Phase 4 写入时 `signal_id=NULL`，Phase 5 信号生成后回填
- **方案 B**：Phase 4 不写 `signal_score_snapshot`，步骤 8 改为写 `candidate_pool`（已在步骤 7 中完成），彻底推迟至 Phase 5

---

### C-07：TD-1 ROE 缺失时策略内权重降级算法不一致

**位置**：§5.6，TD-1 依赖说明

**问题**：当前设计：ROE 缺失时 `pe_percentile +20%`、`pb_percentile +15%`（共 55%/45%）。但 Scorer 的缺失策略处理（§5.7 SCR-04 测试）采用**按比例归一化**——两者方法不同，会导致跨策略权重行为不一致，增加调试难度。

按原始权重（pe 35%, pb 30%）比例分配 ROE 的 35%：
- pe: 35 + 35 × 35/65 ≈ 35 + 18.8 = **53.8%**
- pb: 30 + 35 × 30/65 ≈ 30 + 16.2 = **46.2%**

与文档中的 55%/45% 不同。

**修复建议**：使用与 Scorer.aggregate() 一致的比例归一化方法，或明确注释为何选择了不同的 ad-hoc 分配。

---

### C-08：POOL-05 "淡出标记" 逻辑在代码规格中缺失

**位置**：§5.8，`CandidatePoolManager.update()` 代码规格；§8.4 POOL-05 测试

**问题**：文档描述了"写出上一交易日的 `in_pool=False` 标记（淡出池的标的保留记录但标记为不在池中）"，但 `update()` 的代码规格只包含对当日入池标的的 upsert，没有实现对昨日在池、今日不在池标的的标记逻辑。

POOL-05 测试会因此失败（因为代码中根本没有这段逻辑）。

**修复**：在 `update()` 代码规格中补充步骤：
```python
# 获取上一交易日在池标的，标记淡出
prev_pool_codes = await self.repo.get_pool_codes(prev_trade_date)
departed = prev_pool_codes - pool_codes
for ts_code in departed:
    await self.repo.upsert_candidate_pool(ts_code, trade_date, in_pool=False, ...)
```
（按 C-01 修复后，此逻辑移入 ScoringService）

---

### C-09：pe_pb_history 全市场 5 年数据加载内存压力未评估

**位置**：§5.2，`market_data` dict；§5.9 `_build_market_snapshot()`

**问题**：`pe_pb_history` 的数据量约为 5000 只股票 × 5 年 × 250 交易日 = **625 万行**，每次日度评分都全量加载为 MultiIndex DataFrame，约占 100–150MB 内存。在 Phase 7 DailyPipeline 中，这部分加载与其余数据加载并发执行，峰值内存压力较大，且每次重建此 DataFrame 的 IO 代价高。

**修复建议**：在设计文档中选择并说明一种策略：
- **方案 A**：在 `ValueStrategy.compute_raw_factors()` 内仅按 `universe`（约 3000–4000 只）过滤后加载
- **方案 B**：`pe_pb_history` 改为懒加载（按批次或按股票分组），不一次性全量加载
- **方案 C**（推荐）：在 `daily_quote` 入库时增量维护一张 `pe_pb_percentile` 预计算表，避免运行时窗口扫描

---

### C-10：TD 修复后历史数据回填任务缺失

**位置**：§4（TD-1/2/3 修复规格）；§9（任务计划）

**问题**：T-01/T-02/T-03 任务新增了 `fetch_financial_by_stock()`、`fetch_balance_sheet()`、`fetch_stock_industry()` 三个方法，但任务计划中**没有**专门的历史数据回填任务。修复方法添加后，DB 中现有股票的历史记录仍然是 NULL，只有新增的增量数据才会使用新方法填充。

**修复建议**：在任务计划中各增加一个回填任务：
- T-01b：TD-1 修复后，对全库现有股票执行 `fetch_financial_by_stock()` 回填历史财务数据
- T-02b：TD-2 修复后，对全库执行 `fetch_balance_sheet()` 回填 `total_equity`
- T-03b（已有一次性执行逻辑）：确认 §4.3 描述的"一次性历史回填"对应一个可执行任务

---

## P3 问题（代码质量）

### C-11：`market_data` 使用裸 `dict` 类型，缺乏静态安全性

**位置**：§5.2，`market_data` dict 结构定义；所有策略的 `compute_raw_factors(market_data: dict)`

所有策略依赖 `market_data["adj_prices"]`、`market_data["financials"]` 等 6 个 key，但类型标注为 `dict`，访问缺失 key 时只能在运行时发现。

**修复建议**：定义 `MarketSnapshot` TypedDict：

```python
class MarketSnapshot(TypedDict):
    trade_date: date
    adj_prices: pd.DataFrame
    daily_quotes: pd.DataFrame
    financials: pd.DataFrame
    pe_pb_history: pd.DataFrame
    index_adj_prices: pd.DataFrame
```

---

### C-12：`holding_codes` 默认值类型注解不一致

**位置**：§5.9，`ScoringService.run_daily_scoring()` 签名

```python
async def run_daily_scoring(
    self,
    trade_date: date,
    holding_codes: set[str] = frozenset(),  # frozenset 赋给 set[str]
) -> list[CompositeScore]:
```

`frozenset()` 是 `frozenset[str]`，不是 `set[str]`。类型检查工具会报告不兼容。

**修复**：改为 `holding_codes: frozenset[str] = frozenset()` 或 `set[str] | frozenset[str]`。

---

## 问题汇总

| 编号 | 优先级 | 描述 | 位置 |
|------|--------|------|------|
| C-01 | **P1** | CandidatePoolManager Engine层执行DB I/O，违反架构约束 | §5.8 |
| C-02 | **P1** | MomentumStrategy追高剔除循环变量重绑定bug（不生效） | §5.5 |
| C-03 | **P1** | ValueStrategy价值陷阱截断同一bug | §5.6 |
| C-04 | **P1** | ScoringService传给Scorer的类型不匹配（tuple vs dict） | §5.9 |
| C-05 | **P1** | UniverseFilter缺少SDD §5.4两个过滤条件（流动性/涨停） | §5.1 |
| C-06 | P2 | signal_score_snapshot写入时signal_id FK约束未明确 | §5.9, §6 |
| C-07 | P2 | TD-1 ROE降级权重算法与Scorer归一化方式不一致 | §5.6 |
| C-08 | P2 | POOL-05淡出标记逻辑在代码规格中缺失 | §5.8 |
| C-09 | P2 | pe_pb_history全市场5年数据内存压力未评估 | §5.2, §5.9 |
| C-10 | P2 | TD修复后历史数据回填任务在任务计划中缺失 | §4, §9 |
| C-11 | P3 | market_data裸dict类型，建议改为TypedDict | §5.2 |
| C-12 | P3 | holding_codes默认值frozenset()与set[str]类型注解不一致 | §5.9 |
