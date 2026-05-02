# Phase 4 代码评审报告

> **审查日期**：2026-04-06
> **审查对象**：Phase 4 因子计算引擎全部交付代码
> **依据文档**：`docs/design/phases/phase4_factor_engine.md`（v1.1）、SDD、CLAUDE.md
> **审查范围**：engine/、services/（Phase 4 新增）、api/v1/market.py + watchlist.py、schemas/scoring.py、api/deps.py（Phase 4 扩展）、全部 Phase 4 测试文件

---

## 总体评价

代码整体结构清晰，设计文档 v1.1 的 C-01~C-12 修复全部正确落地，Engine 层 IO 隔离、frozen dataclass 处理、asyncio.gather 类型转换等关键问题均已修复。但存在 **3 个 P1 问题**（规格符合性和生产性能）和 **5 个 P2 问题**（正确性/可维护性），以及 **4 个 P3 问题**（细节质量）。

---

## P1 问题（须修复后才能交付/进入下一 Phase）

### C-01：F-5 单期检查 ≠ 设计规格"连续亏损（最近两期非全为负）"

**位置**：`engine/universe.py:73–77`

**问题**：
```python
# 当前实现
yoy_ok = yoy.isna() | (yoy >= 0)
mask &= (yoy_ok | is_financial)
```
设计规格（SDD §5.4、phase4 §5.1）明确要求"最近**两期** `net_profit_yoy` 非全为负"，即只要有**一期**为正就不应排除。当前代码只检查最新一期：`net_profit_yoy < 0` 即被排除，将一过性亏损（如一季度受损但另一季度盈利）的公司错误剔除。

**根因**：`get_latest_financial()` 采用 `DISTINCT ON (ts_code)` 只返回每只股票一条记录，Repository 层没有提供两期数据的查询接口，实现被迫降级为单期检查，但**未在代码/注释中注明此降级**。

**影响**：过度过滤可投资宇宙，合理公司被误排除；与规格不符。

**修复建议（三选一）**：
1. 在 `get_latest_financial()` 基础上新增 `get_latest_n_financials(ts_codes, n, as_of_date)` 返回每股最新 n 条，F-5 检查取 n=2；
2. 降级为仅检查最新一期，但**在代码注释和设计文档中显式标注此降级**，并更新 URF-05 的测试用例描述；
3. 以最新一期 `net_profit_yoy` 是否为 NULL（TD 未修复前跳过，与 F-4/F-6 一致）作为过渡，待 Repository 支持多期后再完整实现。

---

### C-02：F-7 使用单日 `amount`，非设计规格的"20日均日成交额"

**位置**：`engine/universe.py:87–90`；`services/strategy_service.py:73–74`

**问题**：
```python
# universe.py（注释与实现不符）
# F-7：流动性过滤（20 日均成交额 >= min_avg_amount，NaN → 跳过）
if "amount" in daily_quotes.columns:
    amount = daily_quotes["amount"].reindex(idx)
    amount_ok = amount.isna() | (amount >= min_avg_amount)
```
```python
# strategy_service.py
daily_quotes_filter = snapshot[["amount", "vol", "limit_up"]].copy()
```
`snapshot_quotes` 来自 `get_snapshot_quotes(ts_codes, trade_date)`，其 `amount` 字段是**当日一日**的成交额。当日成交额受涨跌停、特殊公告等短期事件影响，波动远大于 20 日均值。连注释本身都写着"20 日均成交额"但实现检查的是单日值，形成自我矛盾。

**根因**：`_build_market_snapshot` 中没有针对 F-7 提供 20 日滚动均量的数据源；当前 Repository 没有类似 `get_20d_avg_amount(ts_codes, date)` 的方法。

**影响**：
- 低流动性过滤准确性下降（单日量可高出或低于 20 日均量数倍）；
- 代码注释误导阅读者，造成理解错误。

**修复建议**：
1. 新增 Repository 方法 `get_avg_amount(ts_codes, trade_date, window=20) -> pd.Series`，在 DB 层用 `AVG(amount) OVER(ORDER BY trade_date ROWS 19 PRECEDING)` 计算滚动均量；
2. 或在 `_build_market_snapshot` 的 `get_adj_prices_bulk` 调用中同时查出近 20 日 `amount`，在 Service 层计算均值；
3. 短期过渡方案：使用当日 `amount` 但修改注释为"当日成交额（暂用单日代替 20 日均值，待 Repository 扩展后修复）"，并同步更新 URF-09 测试用例描述。

---

### C-03：`_compute_historical_percentile` O(n) MultiIndex 扫描，生产环境严重性能衰退

**位置**：`engine/strategies/value.py:134`

**问题**：
```python
for ts_code in universe:          # 循环约 2000 次
    ...
    if ts_code not in pe_pb_history.index.get_level_values("ts_code"):  # O(n) 扫描
```
`pe_pb_history.index.get_level_values("ts_code")` 返回一个包含所有行的 `Index`（含重复 ts_code）。`in` 操作在此 Index 上为 **O(n)** 线性扫描（pandas 对含重复值的非有序 Index 不使用哈希或二分）。

- 当 universe = 2000 只，pe_pb_history = 2000 只 × 20 条季报 = 40K 行
- 每次检查 O(40K)，合计 2000 × 40K = **8000 万次**逐元素比较

虽然与 Phase 2 数据模型下 pe_pb_history 为季频（而非日频）使绝对量不算极大，但仍是不必要的 O(n²) 设计，随数据积累（更多季度、更多股票）持续恶化。

**修复方案**（两行代码）：
```python
# 在循环外预计算 set（O(n) 一次性）
available_codes = set(pe_pb_history.index.get_level_values("ts_code"))

for ts_code in universe:
    ...
    if ts_code not in available_codes:   # O(1) 哈希查找
        ...
```

---

## P2 问题（正确性/可维护性问题，建议修复后合并）

### C-04：双次 `_build_market_snapshot()` 全量加载，C-09 修复仅部分有效

**位置**：`services/strategy_service.py:69–95`

**问题**：
```python
# 第一次：全量 ~5000 只股票
market_data_raw = await self._build_market_snapshot(trade_date, ts_codes)

# ...执行 UniverseFilter，得到 universe (~2000 只)

# 第二次：universe 过滤后
market_data = await self._build_market_snapshot(trade_date, list(universe))
```
`_build_market_snapshot` 内部通过 `asyncio.gather` **同时**加载 5 个数据源，包括：
1. `get_adj_prices_bulk`：第一次为 ~5000 只 × 130 天 ≈ **65 万行**（UniverseFilter 不使用）
2. `get_pe_pb_history_bulk`：第一次为 ~5000 只 × 20 条 ≈ **10 万行**（UniverseFilter 不使用）

第一次调用的数据源 #1/#2 完全浪费，C-09 的分批加载修复只对第二次调用有效。

**修复建议**：提取轻量快照方法供第一次调用使用：
```python
async def _build_filter_snapshot(
    self, trade_date: date, ts_codes: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """仅加载 UniverseFilter 所需：snapshot_quotes + financials。"""
    snapshot_quotes, financials = await asyncio.gather(
        self._repo.get_snapshot_quotes(ts_codes, trade_date),
        self._repo.get_latest_financial(ts_codes, trade_date),
    )
    return snapshot_quotes, financials
```
`run_daily_scoring` 第一次调用改为 `_build_filter_snapshot()`，只加载两个数据源。

---

### C-05：`MarketSnapshot` TypedDict 注释与实际数据格式不符

**位置**：`engine/strategies/base.py:23`

**问题**：
```python
class MarketSnapshot(TypedDict):
    ...
    index_adj_prices: pd.DataFrame  # index=trade_date，沪深300后复权收盘价（近180日历天）
```
但 `strategy_service.py:195–197` 实际构建为：
```python
index_adj_prices = idx_hist.pivot_table(
    index="index_code", columns="trade_date", values="adj_close"
)
# 实际格式：index=index_code，columns=trade_date
```
`MomentumStrategy` 按实际格式访问（`len(index_prices.columns)` 取日期列数），与股票 `adj_prices`（`index=ts_code, columns=trade_date`）格式一致，代码运行正确。但 TypedDict 注释"index=trade_date"会误导后续维护者。

**修复**：将 TypedDict 注释改为 `# index=index_code，columns=trade_date，Wide 格式（与 adj_prices 结构一致）`。

---

### C-06：`market.py` 中的 `get_repo` 依赖函数违反 CLAUDE.md 约定

**位置**：`api/v1/market.py:28–30`

**问题**：
```python
def get_repo(session: AsyncSession = Depends(get_db)) -> MarketDataRepository:
    """提供 MarketDataRepository 依赖（供测试 override）。"""
    return MarketDataRepository(session)
```
CLAUDE.md §6 明确规定：**"所有 `get_*_service` 依赖函数统一放在 `api/deps.py`，不散落于各路由文件"**。`get_repo` 是供测试 override 的依赖注入函数，应遵循同一约定，移入 `api/deps.py`。

当前散落在路由文件的额外问题：若其他路由文件也需要 `get_repo`，会产生重复定义。

**修复**：将 `get_repo` 移到 `api/deps.py`，`market.py` 改为 `from quantpilot.api.deps import get_repo`。

---

### C-07：`GET /market/pool` 端点缺少设计规格要求的 `sort_by` 查询参数

**位置**：`api/v1/market.py:73–80`

**问题**：
```python
async def get_candidate_pool(
    trade_date: date | None = Query(default=None),
    in_pool_only: bool = Query(default=True),
    # 缺少：sort_by: str = Query(default="composite_score")
```
Phase 4 设计文档 §7.1 明确列出 `sort_by` 参数（默认 `composite_score`，允许用户按不同维度排序）。当前实现硬编码排序，API 文档与实际行为不一致，Phase 5+ 前端可能依赖此参数。

**修复**：添加 `sort_by: str = Query(default="composite_score")` 并在排序逻辑中使用：
```python
ALLOWED_SORT_FIELDS = {"composite_score", "trend_score", "momentum_score",
                       "reversion_score", "value_score"}
sort_field = sort_by if sort_by in ALLOWED_SORT_FIELDS else "composite_score"
sorted_records = sorted(
    pool_records,
    key=lambda r: (getattr(r, sort_field) is None, -(getattr(r, sort_field) or 0)),
)
```

---

### C-08：`momentum.py:104` 死代码 `float("nan") if True else 0`

**位置**：`engine/strategies/momentum.py:104`

**问题**：
```python
if float(return_1m.get(s.ts_code, float("nan") if True else 0)) >= top5pct_threshold
```
`float("nan") if True else 0` 的 `else 0` 分支永远不会执行（`True` 恒为真）。此表达式等价于 `return_1m.get(s.ts_code, float("nan"))`，`else 0` 是明显的调试遗留代码，增加阅读噪音，且可能误导维护者以为此处有意保留了异常分支。

**修复**：直接写为：
```python
if (
    not pd.isna(return_1m.get(s.ts_code, float("nan")))
    and float(return_1m.get(s.ts_code, float("nan"))) >= top5pct_threshold
)
```

---

## P3 问题（细节/测试质量）

### C-09：`value.py _build_reason` 缺少 PB 分位信息

**位置**：`engine/strategies/value.py:95–111`

**问题**：`_build_reason` 返回字符串仅包含 PE 分位和 ROE，缺少 `pb_percentile` 因子的展示。设计规格要求包含 `PB={pb:.2f}` 或 `PB历史分位={pb_pct:.0f}%`（phase4 §5.6 理由模板）。用户在查看评分理由时无法了解 PB 估值情况。

**建议**：在 reason 字符串中补充 `pb_pct = raw_row.get("pb_percentile", float("nan"))` 的展示。

---

### C-10：`trend.py _build_reason` NaN MACD 显示为误导性"死叉"标签

**位置**：`engine/strategies/trend.py:99–100`

**问题**：
```python
else:
    macd_label = "死叉"   # NaN 时也走此分支
```
当 MACD 因数据不足返回 `float("nan")` 时（`pd.isna(macd)` 为 True），代码走到 `else` 分支并显示"死叉"——把"数据不足"错误解读为"死叉信号"，对用户产生误导。

**建议**：添加 NaN 判断分支：
```python
if pd.isna(macd):
    macd_label = "数据不足"
elif macd == 1.0:
    macd_label = "金叉"
elif macd == 0.5:
    macd_label = "中性"
else:
    macd_label = "死叉"
```

---

### C-11：`test_int_04` 断言过于宽松，注释存在误导

**位置**：`tests/integration/test_scoring_service.py:189–190`

**问题**：
```python
assert len(pool_codes) > 0
assert len(pool_codes) <= 3 + 1  # pool_capacity=3，容许白名单超出
```
测试中无白名单设置，5 只股票均通过 UniverseFilter，pool_capacity=3，pool_codes 应精确为 3。`<= 4` 的宽松断言无法检测入池数量超出的 bug；注释"容许白名单超出"在无白名单时语义错误。

**建议**：改为 `assert len(pool_codes) == 3`。

---

### C-12：`test_strategies_impl.py` VAL-01 测试数据构建代码为 O(n²)

**位置**：`tests/unit/test_strategies_impl.py:365–366`

**问题**：
```python
pe_ttm_vals = [pe_hist[c][i] for c, _ in tuples
               for i in [list(history_dates).index(tuples[list(tuples).index((c, _))][1])]]
```
此嵌套推导式中 `list(history_dates).index(...)` 和 `list(tuples).index(...)` 均为 O(n) 线性搜索，整体复杂度 O(n²)（n = 5×250×2 = 2500）。在 CI 中每次执行此段代码约做 **625 万次**元素比较，显著拖慢测试套件。

**建议**：利用 tuples 的生成顺序（外层 codes、内层 history_dates）直接按索引取值：
```python
n_hist = len(history_dates)
pe_ttm_vals = [pe_hist[c][i % n_hist] for i, (c, _) in enumerate(tuples)]
```

---

## 问题汇总

| 编号 | 位置 | 类别 | 问题摘要 | 优先级 |
|------|------|------|----------|--------|
| C-01 | `engine/universe.py:73–77` | 规格符合性 | F-5 单期检查 ≠ "连续亏损（两期）" | **P1** |
| C-02 | `engine/universe.py:87–90` + `strategy_service.py:73` | 规格符合性 | F-7 单日 amount ≠ "20日均日成交额" | **P1** |
| C-03 | `engine/strategies/value.py:134` | 性能 | O(n) MultiIndex 扫描在循环内，累计 O(n²) | **P1** |
| C-04 | `services/strategy_service.py:69–95` | 性能 | 双次全量快照加载，C-09 部分失效 | P2 |
| C-05 | `engine/strategies/base.py:23` | 文档 | `index_adj_prices` TypedDict 注释格式错误 | P2 |
| C-06 | `api/v1/market.py:28–30` | 架构规范 | `get_repo` 在路由文件违反 CLAUDE.md 约定 | P2 |
| C-07 | `api/v1/market.py:73–80` | 功能缺失 | `GET /market/pool` 缺少 `sort_by` 参数 | P2 |
| C-08 | `engine/strategies/momentum.py:104` | 代码质量 | 死代码 `float("nan") if True else 0` | P2 |
| C-09 | `engine/strategies/value.py:95–111` | 展示完整性 | `_build_reason` 缺少 PB 分位信息 | P3 |
| C-10 | `engine/strategies/trend.py:99–100` | 展示准确性 | NaN MACD 误显为"死叉" | P3 |
| C-11 | `tests/integration/test_scoring_service.py:189–190` | 测试严格性 | `INT-04` 断言 `<= 3+1` 过松，注释有误 | P3 |
| C-12 | `tests/unit/test_strategies_impl.py:365–366` | 测试性能 | VAL-01 数据构建代码 O(n²) | P3 |

---

## 附：已验证的优良实现（供参考）

以下设计文档 v1.1 要求的关键点均已正确实现，质量良好：

| 关注点 | 位置 | 评价 |
|--------|------|------|
| Engine 层无 IO | `engine/universe.py`、`engine/pool.py`、`engine/scorer.py` 及四大策略 | ✅ 无任何 DB/文件/网络调用 |
| frozen dataclass 正确修改 | `momentum.py:98–108`、`value.py:79–92` | ✅ 全部使用列表推导式重建 |
| asyncio.gather 类型转换 | `strategy_service.py:113–115` | ✅ 正确 zip 转 dict |
| 权重归一化（缺失策略） | `scorer.py:63–64`、`base.py:85–87` | ✅ 两处均正确按比例归一化 |
| pe_pb_history universe 过滤 | `strategy_service.py:95` | ✅ 第二次快照仅加载 universe 股票 |
| signal_score_snapshot 不写入 | `strategy_service.py:536` 注释 + 无写入代码 | ✅ Phase 4 完全不触碰此表 |
| MarketSnapshot TypedDict | `engine/strategies/base.py:15–23` | ✅ 六个字段完整定义 |
| `holding_codes` 类型 | `strategy_service.py:49`、`pool.py:32` | ✅ `frozenset[str] \| set[str]` 统一 |
