# Phase 1–4 综合实现评审报告

> **审查日期**：2026-04-08
> **审查对象**：Phase 1–4 全部已交付代码
> **依据文档**：`docs/spec/QuantPilot_SDD.md`、`docs/design/phases/phase1_infrastructure.md` ~ `phase4_factor_engine.md`、`CLAUDE.md`
> **评审范围**：`engine/`、`services/`、`api/`、`models/`、`data/`、`pipeline/`
> **前置说明**：Phase 4 代码评审（`phase4_code_review_2026-04-06.md`）的 C-01 ~ C-12 全部修复已于 2026-04-06 验证通过，本次不重复评审已关闭问题。本次新增两个评审维度：**代码质量**（不限 Phase 4）和**设计整合性**（实现是否与设计文档完全一致）。

---

## 总体评价

Phase 1–4 代码整体质量良好：Engine 层 IO 隔离彻底，数据流（采集→过滤→评分→入池）主干逻辑清晰，frozen dataclass / asyncio.gather / DISTINCT ON 等关键技术点落地正确。存在 **2 个 P1 问题**（设计规范违反，须在 Phase 5 首个可运行里程碑前修复）、**4 个 P2 问题**（正确性或合同偏差）、**3 个 P3 问题**（次要质量）。设计整合性方面，主干端点和算法权重均与规格一致，但有 2 处偏差（接口契约、依赖注入规范）需要明确处理。

---

## P1 问题（须在 Phase 5 首个里程碑前修复）

### R-01：`get_data_service` 定义在路由文件，违反 CLAUDE.md 约定

**位置**：`backend/src/quantpilot/api/v1/data.py:26–41`

**问题**：

```python
# data.py 路由文件内部直接定义依赖注入函数
def get_data_service(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> DataService:
    adapter = getattr(request.app.state, "adapter", None)
    ...
```

CLAUDE.md 明确规定："**所有依赖注入函数**（`get_*_service`、`get_repo` 等）统一放在 `api/deps.py`，禁止在路由文件内定义（路由文件只允许 `from quantpilot.api.deps import ...`）"。

这条规范是 Phase 1 建立的，Phase 2 交付 `data.py` 时未遵守，Phase 4 修复 C-06 时将 `get_repo` 迁移到 `deps.py`，但遗漏了 `get_data_service`。其他路由文件（`market.py`、`watchlist.py`）均已合规，形成不一致。

**影响**：
- E2E 测试的 `override_dependencies` 需要在两处（`deps.py` + `data.py`）各自 mock，当前测试可能存在 mock 覆盖缺口；
- 后续 Phase 修改 DataService 构建逻辑时需同时维护两处。

**修复**：将 `get_data_service` 函数体完整移入 `api/deps.py`，`data.py` 改为 `from quantpilot.api.deps import get_data_service`。

---

### R-02：Phase 4 DoD 要求 TD-1/2/3 完成后 `financial_data` 有完整值，但日常采集流程未接入

**位置**：Phase 4 DoD 第一条；`data/adapters/tushare.py`（已实现 TD 方法）；`services/data_service.py`（`ingest_daily` 未调用）

**问题**：

Phase 4 设计文档 DoD 第一条：
> TD-1/2/3 全部修复，financial_data 有完整值

三个 Tushare 适配器方法（`fetch_financial_by_stock`、`fetch_balance_sheet`、`fetch_stock_industry`）已实现并通过单元测试。然而 `DataService.ingest_daily` 和 `ingest_history` 均未调用这些方法，生产路径下 `financial_data.roe`、`net_profit_yoy`、`revenue_yoy`、`debt_to_asset`、`total_equity`（balance_sheet）以及 `stock_info.sw_industry_l1` 仍为 NULL/占位值。

Phase 5 设计文档将此列为前置任务 P5-PRE-1/2/3，说明设计侧已意识到该问题并有计划，但**目前状态与 Phase 4 DoD 所写的"有完整值"不符**，属于设计文档与实现之间的显式偏差。

**影响**：
- UniverseFilter F-4（净资产正）、F-5（连续亏损）、F-6（高杠杆）全部因数据为 NULL 而跳过过滤；
- ValueStrategy `roe_quality` 因子全部为 NaN，权重自动退化到 PE/PB 两个因子（35%+35%→50%+50%），与设计权重矩阵不符；
- MomentumStrategy `industry_rs` 因 `sw_industry_l1` 为占位值而强制置 50（中性），25% 权重信息丢失；
- 实际运行的评分结果偏离设计意图。

**处置方向**：
1. 在 Phase 5 DoD 中将 P5-PRE-1/2/3 作为进入信号生成逻辑前的**强制前置**；
2. 在 Phase 4 设计文档 DoD 处补充注释："TD 接入推迟至 Phase 5，见 P5-PRE-1/2/3"，避免 DoD 歧义；
3. Phase 5 完成 P5-PRE-1/2/3 后关闭 R-02。

---

## P2 问题（应在 Phase 5 结束前修复）

### R-03：Signal ORM 模型 DDL 注释仍包含 HOLD/EXIT，与设计规格不符

**位置**：`backend/src/quantpilot/models/business.py:67`

```python
signal_type: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY/SELL/HOLD/EXIT
```

SDD §8.3 明确：`signal_type` 仅有 **BUY（建仓/加仓）/ SELL（减仓/止损）** 两个合法值；Phase 4 设计文档端点契约也只约定 BUY/SELL。HOLD/EXIT 是早期草稿值，已被设计文档删除。

**影响**：注释构成错误的自文档，Phase 5 Signal 生成代码的作者可能会参考注释错误地创建 HOLD/EXIT 类型的信号行，导致下游逻辑无法识别。

**修复**：将注释改为 `# BUY / SELL`，并在 Phase 5 的 SignalGenerator 实现中添加值约束断言或 Enum 类型检查。

---

### R-04：`ScoringService.run_daily_scoring` Pool Upsert 逐条串行，N 次 DB 往返

**位置**：`backend/src/quantpilot/services/strategy_service.py:137–165`

```python
# 入池标的：逐条 upsert（20–30 次串行 await）
for entry in pool_entries:
    await self._repo.upsert_candidate_pool(ts_code=entry.ts_code, ...)

# 淡出标记：逐条 upsert（N 次串行 await）
for ts_code in fade_out_codes:
    await self._repo.upsert_candidate_pool(ts_code=ts_code, ...)
```

每次日度评分运行约有 20–30 个入池条目 + 若干淡出条目，产生约 30–50 次串行数据库往返。现有单条 `upsert_candidate_pool` 完全可以接受批量参数，无需每个条目单独一次 `await`。

**影响**：
- 日度流水线预期在 5 分钟内完成；每次 DB 往返 latency 假设 5ms，50 次 = 250ms 额外开销（可接受但非必要）；
- 更重要的是：批量 upsert 具有事务原子性，逐条 upsert 若中途失败会产生部分写入（当日 pool 数据半新半旧）。

**修复建议**：
```python
# Repository 新增批量 upsert 方法
async def upsert_candidate_pool_bulk(self, entries: list[dict]) -> None:
    stmt = pg_insert(CandidatePool).values(entries)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_candidate_pool_code_date",
        set_={...}
    )
    await self._session.execute(stmt)

# ScoringService 改为一次调用
await self._repo.upsert_candidate_pool_bulk([entry_to_dict(e) for e in pool_entries])
```

---

### R-05：`ingest_history` API 响应格式与设计契约不符，缺少降级注释

**位置**：`backend/src/quantpilot/api/v1/data.py:83–99`

**设计规格**（Phase 2 design §4.2）：
> `POST /api/v1/data/ingest/history` → **202 Accepted** + `task_id`（Phase 9 实现进度推送）

**实际实现**：
```python
@router.post("/ingest/history")
async def ingest_history(body, ...):
    summary = await service.ingest_history(...)   # 同步阻塞等待完成
    return {
        "code": 0,
        "data": {
            "task_id": f"backfill-{body.start_date}-{body.end_date}",   # 静态字符串，非真实任务 ID
            "status": "completed",
            ...
        },
        "msg": "ok",
    }
```

实现改为同步执行并返回 200，这在 Phase 2 范围内是合理的降级（无异步任务调度器），但存在两个问题：
1. HTTP 状态码仍为 200，与设计契约 202 不符，调用方无法区分"接受任务"与"完成任务"；
2. 没有按 CLAUDE.md 要求添加 `【降级说明】` 注释。

**影响**：Phase 9 接入 WebSocket 进度推送时，前端若按契约假设 202 行为将遇到兼容性问题。

**修复**：或在此处追加降级注释说明同步执行原因，或将 HTTP 状态码改为 202（配合 `JSONResponse(status_code=202, content=...)`），两者选其一并在函数内添加：
```python
# 【降级说明】Phase 2 同步执行；Phase 9 接入 APScheduler 后改为真实异步任务队列，返回真实 task_id。
```

---

### R-06：`MomentumStrategy.VALID_SW_INDUSTRIES` 包含申万旧版行业名，与 SW2021 标准不符

**位置**：`backend/src/quantpilot/engine/strategies/momentum.py:21–27`

```python
VALID_SW_INDUSTRIES: frozenset[str] = frozenset({
    "采掘",         # SW2021 已拆分为"煤炭"/"石油石化"
    "化工",         # SW2021 改名为"基础化工"
    "电气设备",     # SW2021 改名为"电力设备"
    "非银金融",     # SW2021 细分为"证券"/"保险"/"多元金融"（但 L1 层仍有"非银金融"？）
    "农业",         # 非 SW2021 一级行业（属于"农林牧渔"子分类）
    "商业贸易",     # SW2021 改名为"商贸零售"
    "休闲服务",     # SW2021 改名为"社会服务"
    ...
})
```

Tushare `stock_industry(src='SW2021')` 返回的是 SW2021 标准名称。如果 TD-3 修复后写入 `stock_info.sw_industry_l1` 的值是 SW2021 名称，则上面列出的旧版行业名会被判断为"占位值（非真实行业）"，导致大量有效行业数据的股票被错误地将 `industry_rs` 置为中性值 50，信息完全丢失。

**影响**：TD-3 修复后，若 VALID_SW_INDUSTRIES 未同步更新，`industry_rs` 因子（25% 权重）仍实质失效，动量策略退化。

**修复**：
1. 对照 Tushare `stock_industry(src='SW2021')` 实际返回的 `industry_name` 值域，修正集合内容；
2. 添加测试用例：传入已知 SW2021 行业名，断言 `is_placeholder` 为 False。

---

## P3 问题（次要，可在 Phase 4/5 迭代中处理）

### R-07：`MarketSnapshot` TypedDict 含运行时注入的未声明键 `_snapshot_quotes`

**位置**：`backend/src/quantpilot/engine/strategies/base.py:15–24`（TypedDict 定义）；`services/strategy_service.py:235–243`（注入处）

```python
# base.py — TypedDict 定义中不含此键
class MarketSnapshot(TypedDict):
    trade_date: date
    adj_prices: pd.DataFrame
    ...
    index_adj_prices: pd.DataFrame
    # _snapshot_quotes 未声明

# strategy_service.py — 运行时注入
result: MarketSnapshot = {  # type: ignore[assignment]
    ...
    "_snapshot_quotes": snapshot_quotes,   # 实际传入但 TypedDict 不认识
}
```

设计注释说 `_snapshot_quotes` 是"供 `run_daily_scoring` 内部使用"的辅助键，但它被放进了 TypedDict 结构中，用 `# type: ignore[assignment]` 压制类型错误。这使得 Engine 层的 MarketSnapshot 含有 Service 层的内部状态，两层边界模糊。

**修复建议**：将 `_snapshot_quotes` 从 MarketSnapshot 剥离，改为在 `run_daily_scoring` 中单独传变量：
```python
snapshot_quotes_raw, financials_raw = await self._build_filter_snapshot(...)
# 构建 MarketSnapshot 不含 _snapshot_quotes
market_data = await self._build_market_snapshot(trade_date, list(universe))
# 直接使用已有的 snapshot_quotes_raw 变量，不需要从 market_data 取
```

---

### R-08：`ScoringService.run_daily_scoring` 文档字符串步骤编号与实现不一致

**位置**：`backend/src/quantpilot/services/strategy_service.py:51–62`

文档字符串列出步骤 1–9，而实际实现中明确注释了步骤 1–11（含新增的步骤 10 批量 upsert 和步骤 11 淡出标记），相差 2 步。

**修复**：将文档字符串步骤列表更新为与代码注释一致的 11 步。

---

### R-09：`MeanReversionStrategy` 布林带列按位置索引，脆弱

**位置**：`backend/src/quantpilot/engine/strategies/mean_reversion.py:58–59`

```python
bb_lower = float(bb_df.iloc[-1, 0])   # BBL_20_2.0
bb_upper = float(bb_df.iloc[-1, 2])   # BBU_20_2.0
```

`pandas_ta.bbands()` 返回列名为 `BBL_20_2.0`、`BBM_20_2.0`、`BBU_20_2.0`，当前通过列位置（0、2）访问。若 `bbands` 参数调整（如 `std` 改变），列名随之变化但位置不变，注释可作参考；但若 pandas_ta 未来版本调整列顺序，将静默取到错误值。

**修复**：
```python
col_map = {c.split("_")[0]: c for c in bb_df.columns}   # {"BBL": "BBL_20_2.0", ...}
bb_lower = float(bb_df.iloc[-1][col_map["BBL"]])
bb_upper = float(bb_df.iloc[-1][col_map["BBU"]])
```
或直接硬编码构造预期列名：`f"BBL_{length}_{std}"`。

---

## 设计整合性专项检查

### Phase 1 对标

| 设计项 | 规格 | 实现状态 |
|--------|------|----------|
| POST /auth/login | 返回 access_token + refresh_token | ✓ 符合 |
| POST /auth/refresh | 返回新 access_token | ✓ 符合 |
| GET /health | 返回 {status, version} | ✓ 符合 |
| 19 张表全量建表 | DB Schema 完整 | ✓ 符合 |
| signal_type 约束 | 仅 BUY/SELL（SDD §8.3） | ✗ 注释含 HOLD/EXIT（R-03） |
| 统一响应格式 | {"code":0,"data":…,"msg":"ok"} | ✓ 全局一致 |
| 422 统一格式 | 含 errors 字段 | ✓ 符合 |

### Phase 2 对标

| 设计项 | 规格 | 实现状态 |
|--------|------|----------|
| GET /data/status | 返回 {latest_quote_date, stock_count, …} | ✓ 符合 |
| POST /data/ingest/daily | 返回 IngestResult | ✓ 符合 |
| POST /data/ingest/history | **202 Accepted + task_id** | ✗ 返回 200 同步结果（R-05） |
| POST /data/refresh/stock-list | 返回 {upserted_count} | ✓ 符合 |
| get_data_service 位置 | deps.py | ✗ 定义在 data.py（R-01） |
| TradingCalendar 接口 | 含 count_trade_days / offset_trade_date | ✓ 已实现 |
| DataValidator 接口 | validate_daily_quotes / validate_financial_data | ✓ 已实现 |
| AdjustedPriceProvider | backward/forward_adjusted | ✓ 已实现 |
| TD-1/2/3 适配器方法 | fetch_financial_by_stock / fetch_balance_sheet / fetch_stock_industry | ✓ 已实现 |
| TD-1/2/3 接入 ingest 流程 | financial_data 有完整值 | ✗ 未接入（R-02，推迟至 P5-PRE-1/2/3） |

### Phase 3 对标

| 设计项 | 规格 | 实现状态 |
|--------|------|----------|
| GET /market/state | 返回当前市场状态 | ✓ 符合 |
| GET /market/state/history | start/end 参数，返回历史列表 | ✓ 符合 |
| MarketStateEnum | UPTREND / DOWNTREND / OSCILLATION | ✓ 符合 |
| compute_indicators | ADX(14) + MA20 + MA60 | ✓ 符合 |
| determine_raw_state | ADX>25 + MA 关系三态判定 | ✓ 符合 |
| apply_debounce | 连续 3 日确认切换 | ✓ 符合 |
| identify / identify_latest | 完整流水线 + 便捷方法 | ✓ 符合 |
| description 模板 | 4 种场景 | ✓ 符合 |
| state_changed 语义 | Engine 内部逐行对比产生 | ✓ 符合 |

### Phase 4 对标

| 设计项 | 规格 | 实现状态 |
|--------|------|----------|
| GET /market/pool | 含 rank/is_holding/is_watchlist/sort_by | ✓ 符合 |
| GET /market/stock/{ts_code}/score | 历史评分走势 | ✓ 符合 |
| GET/POST/DELETE /watchlist | 黑白名单 CRUD，幂等 | ✓ 符合 |
| UniverseFilter F-1~F-8 | 八条过滤规则 | ✓（F-5/F-7 降级已注释） |
| 金融行业豁免 | F-4/F-5/F-6 豁免 {银行,证券,保险,多元金融} | ✓ 符合 |
| TrendStrategy 权重 | ma_alignment 40%/macd_signal 30%/price_breakout 30% | ✓ 符合 |
| MeanReversionStrategy 权重 | rsi_oversold 35%/price_deviation 35%/bb_position 30% | ✓ 符合 |
| MomentumStrategy 权重 | return_3m 40%/rs_6m 35%/industry_rs 25% | ✓ 符合 |
| ValueStrategy 权重 | pe_percentile 35%/pb_percentile 30%/roe_quality 35% | ✓ 符合 |
| 追高剔除（MomentumStrategy） | 1M 涨幅前 5% 得分置 0 | ✓ 符合 |
| 价值陷阱截断（ValueStrategy） | ROE < 行业中位 ROE → 得分≤50 | ✓ 符合 |
| Scorer 权重矩阵（SDD §7.5） | 三状态 4×4 权重表 | ✓ 完全符合 |
| 缺失策略权重归一化 | 按比例分配给剩余策略 | ✓ 符合 |
| CandidatePoolManager | top-N + 持仓保护 + 白名单 | ✓ 符合 |
| ScoringService 11 步流程 | 含两阶段快照 + 并发策略 + upsert | ✓ 符合（upsert 性能见 R-04） |
| VALID_SW_INDUSTRIES | 对齐 SW2021 | ✗ 含旧版行业名（R-06） |
| Pool upsert 原子性 | 批量写入 | ✗ 逐条串行（R-04） |

---

## 设计整合性已验证通过的关键项

以下关键设计要求已在代码中完整实现，特别标注以减少未来评审范围：

- **Scorer 权重矩阵**（UPTREND/DOWNTREND/OSCILLATION）与 SDD §7.5 完全一致，已通过单元测试验证
- **Decimal→float 转换**：Engine 层各策略均调用 `.astype(float)`，避免 `pandas_ta` TypeError（Phase 3 CR-03 模式延续）
- **横截面百分位归一化**：`rank(pct=True) * 100`，全 NaN 列自动剔除后按比例重新归一化权重
- **全 NaN 因子跳过**：`skipna=False` 保证任一因子为 NaN 的标的被排除而非得到错误复合分
- **MultiIndex O(1) 预计算**：`_compute_historical_percentile` 中 `available_codes = set(...)` 已修复 C-03 问题
- **get_repo 位置**：`api/deps.py` 统一管理（C-06 修复）
- **frozen dataclass 重建**：`MomentumStrategy` 追高剔除和 `ValueStrategy` 价值陷阱截断均使用 list comprehension 重建，未原地修改 frozen StrategyScore
- **Repository 读写均通过 MarketDataRepository**：Service/Route 层无直接 ORM 操作
- **upsert 模式**：所有批量写入均使用 `pg_insert(...).on_conflict_do_update()`，幂等
- **APScheduler 单例传参**：通过 `create_scheduler(args=[engine])` 显式传入，不依赖 `app.state`（Phase 3 CR-07 模式）

---

## 修复优先级汇总

| 编号 | 位置 | 级别 | 描述 | 目标 Phase |
|------|------|------|------|------------|
| R-01 | `api/v1/data.py:26–41` | **P1** | `get_data_service` 移入 `deps.py` | Phase 5 启动前 |
| R-02 | Phase 4 DoD / `data_service.py` | **P1** | TD-1/2/3 pipeline 接入（P5-PRE-1/2/3）；更新 Phase 4 DoD 注释 | Phase 5 前置任务 |
| R-03 | `models/business.py:67` | P2 | Signal 注释改为 `# BUY / SELL` | Phase 5 Signal 实现前 |
| R-04 | `services/strategy_service.py:137–165` | P2 | Pool upsert 改批量（原子性+性能） | Phase 5 结束前 |
| R-05 | `api/v1/data.py:83–99` | P2 | `ingest_history` 添加降级注释或改 202 状态码 | Phase 5 结束前 |
| R-06 | `engine/strategies/momentum.py:21–27` | P2 | 更新 `VALID_SW_INDUSTRIES` 为 SW2021 标准名称 | TD-3 接入时同步 |
| R-07 | `services/strategy_service.py:235–243` | P3 | 剥离 `_snapshot_quotes` 出 MarketSnapshot | 可迭代 |
| R-08 | `services/strategy_service.py:51–62` | P3 | 文档字符串步骤编号同步 | 可迭代 |
| R-09 | `engine/strategies/mean_reversion.py:58–59` | P3 | bb_df 列改用名称索引 | 可迭代 |

---

## Phase 4 DoD 验收状态复核

| DoD 项 | 状态 | 说明 |
|--------|------|------|
| TD-1/2/3 全部修复，financial_data 有完整值 | ✗ 部分 | 适配器已实现，pipeline 未接入（R-02），推迟 P5-PRE-1/2/3 |
| UniverseFilter 单元测试 ≥10 个 | ✓ | F-1~F-8 含金融豁免、NULL 处理全覆盖 |
| 四大策略单元测试 | ✓ | 各策略独立测试通过 |
| Scorer 权重矩阵一致性 | ✓ | 与 SDD §7.5 完全一致 |
| CandidatePoolManager 持仓+白名单 | ✓ | 逻辑正确 |
| E2E 测试 ≥9 个 | ✓ | 通过 |
| Engine 层禁止 IO | ✓ | 严格隔离 |
| ruff 无错误 | ✓ | 已验证 |
