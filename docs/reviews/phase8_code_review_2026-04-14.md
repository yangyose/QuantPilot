# Phase 8 代码评审报告

**评审对象**：Phase 8 实现代码（BacktestEngine、PerformanceService、BacktestService、API、tests）  
**设计依据**：`docs/design/phases/phase8_backtest.md` v1.1  
**评审日期**：2026-04-14  
**评审人**：Claude Code  
**状态**：已关闭（7 项问题全部修复）

---

## 1. 总体评价

| 维度 | 评价 |
|------|------|
| **DoD 完成度** | D-01~D-16 全部交付，模型/迁移/Engine/Service/API/测试结构均已实现 |
| **设计符合度** | 整体架构与设计文档一致；BacktestEngine 主循环流程、PerformanceService 7 项指标、WS 进度推送均按设计实现 |
| **CLAUDE.md 规范** | 存在 3 项直接违规（C-01 P2 Engine 数据格式错误、C-02 P2 基准日期精确匹配、C-03 P2 Service 手动 commit），需修复后方可合并 |
| **测试覆盖** | unit/E2E/集成/冒烟均已覆盖；INT-BE-02 因 C-01 存在导致断言为空操作（C-06） |

**结论：存在 3 个 P2 级缺陷（必须修复）、4 个 P3 级问题（建议修复）。**

---

## 2. 问题清单

### 2.1 P2 级（必须修复）

#### C-01：`_get_quotes_at` 返回错误的 DataFrame 格式，导致回测从不执行任何交易

**位置**：`engine/backtest/engine.py` `_get_quotes_at` 方法

**问题**：

```python
def _get_quotes_at(self, adj_prices: pd.DataFrame, td: date) -> pd.DataFrame:
    if adj_prices.empty or td not in adj_prices.index:
        return pd.DataFrame()
    row = adj_prices.loc[td]          # Series，index=ts_code，values=price
    result = row.to_frame().T         # DataFrame：columns=ts_codes，index=[td]
    result.index = result.index.map(lambda _: None)
    return result                     # ← columns 为股票代码，不含 "close"
```

然而 `_execute_signals` 依赖如下格式校验：

```python
if "close" not in quotes.columns or ts_code not in quotes.index:
    continue   # ← 由于 "close" 永远不是列名，所有交易信号全部跳过
```

两处数据格式完全不匹配：`_get_quotes_at` 生成的是 `columns=ts_codes、index=[None]` 的宽表，而 `_execute_signals` 期望的是 `index=ts_codes、columns=["close"]` 的长表。实际效果是：只要 `adj_prices` 非空，每个交易日的所有信号均被静默跳过，回测从不执行任何买卖。这使得 `daily_nav` 始终等于 `initial_capital`，`performance` 中所有收益指标恒为 0。

**修正方案**：

```python
def _get_quotes_at(self, adj_prices: pd.DataFrame, td: date) -> pd.DataFrame:
    if adj_prices.empty or td not in adj_prices.index:
        return pd.DataFrame()
    row = adj_prices.loc[td]          # Series，index=ts_code，values=price
    return row.rename("close").to_frame()   # DataFrame：index=ts_code，columns=["close"]
```

这样 `_execute_signals` 中 `"close" in quotes.columns` 为 True，`ts_code in quotes.index` 也能正确判断股票是否有行情，交易才能实际执行。

**连带影响**：此修复后 INT-BE-02（C-06）的测试断言需同步更新，`nav_with_cost < nav_no_cost` 将成为真实断言。

---

#### C-02：`_get_benchmark_return` 使用精确日期匹配，周末/节假日返回 None

**位置**：`services/performance_service.py` `_get_benchmark_return` 方法

**问题**：

```python
rows = (await self._session.execute(
    select(IndexHistory.close)
    .where(IndexHistory.index_code == "000300.SH")
    .where(IndexHistory.trade_date.in_([start, end]))   # ← 精确匹配
    .order_by(IndexHistory.trade_date)
)).scalars().all()
```

当 `start` 或 `end` 为周末或节假日（无指数行情记录）时，`.in_([start, end])` 匹配到的行数不足 2，方法返回 `None`，导致 `get_summary` 中 `benchmark_return=None`，API 返回中该字段缺失。

按照设计 §3.1，基准收益应取区间内最近可用交易日的收盘价，而非要求区间端点恰好有数据。

**修正方案**：使用两条独立查询分别取区间首尾的最近交易日价格：

```python
async def _get_benchmark_return(self, start: date, end: date) -> float | None:
    # 取 >= start 的第一个交易日收盘价
    start_row = (await self._session.execute(
        select(IndexHistory.close)
        .where(IndexHistory.index_code == "000300.SH")
        .where(IndexHistory.trade_date >= start)
        .order_by(IndexHistory.trade_date.asc())
        .limit(1)
    )).scalar_one_or_none()

    # 取 <= end 的最后一个交易日收盘价
    end_row = (await self._session.execute(
        select(IndexHistory.close)
        .where(IndexHistory.index_code == "000300.SH")
        .where(IndexHistory.trade_date <= end)
        .order_by(IndexHistory.trade_date.desc())
        .limit(1)
    )).scalar_one_or_none()

    if start_row is None or end_row is None or float(start_row) == 0:
        return None
    return (float(end_row) - float(start_row)) / float(start_row)
```

---

#### C-03：`BacktestService.create_task` 在 Service 层手动调用 `commit()`，违反 CLAUDE.md §6 自动 commit 约定

**位置**：`services/backtest_service.py` `create_task` 方法（第 47 行）

**问题**：

```python
async def create_task(self, config: BacktestConfig) -> str:
    task_id = str(uuid.uuid4())
    task = BacktestTask(...)
    self._session.add(task)
    await self._session.flush()
    await self._session.commit()   # ← 违规：Service 层不应手动 commit
    logger.info("backtest_task_created task_id=%s", task_id)
    return task_id
```

CLAUDE.md §6 明确规定："get_db() yield 后自动 commit，异常自动 rollback；路由中无需手动 commit/rollback"。Service 层手动调用 `commit()` 会导致以下问题：

1. 若路由层后续操作失败需要回滚，Task 记录已不可撤销地提交（部分提交）
2. 与 `run_task` 末尾的 `await self._session.commit()` 形成双重 commit 模式，不一致

`run_task` 末尾（第 101、111 行）也存在同样的手动 `commit()` 问题。由于 `run_task` 在 `asyncio.to_thread` 后写入结果，其 session 生命周期由路由层 `BackgroundTask` 管理，此处的手动 commit 也属于违规。

**修正方案**：删除 Service 层所有手动 `commit()` 调用，仅保留 `flush()`（用于生成 DB 端序列/UUID）：

```python
async def create_task(self, config: BacktestConfig) -> str:
    task_id = str(uuid.uuid4())
    task = BacktestTask(...)
    self._session.add(task)
    await self._session.flush()   # 保留 flush，使 task 可被后续同 session 查询到
    # 删除 await self._session.commit()
    logger.info("backtest_task_created task_id=%s", task_id)
    return task_id
```

注意：`run_task` 在路由的 `BackgroundTask` 中执行，路由层退出时 `get_db()` 自动 commit，Service 层无需操心。

---

### 2.2 P3 级（建议修复）

#### C-04：`_make_redis_progress_cb` 在线程回调中使用 `asyncio.get_event_loop()`，Python 3.12 已弃用

**位置**：`api/v1/backtest.py` `_make_redis_progress_cb` 函数

**问题**：

```python
def _make_redis_progress_cb(redis: Redis, channel: str) -> Callable:
    def cb(trade_date_str: str, progress_pct: int, current_nav: float) -> None:
        loop = asyncio.get_event_loop()       # ← 在 asyncio.to_thread 启动的子线程中调用
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(redis.publish(channel, msg), loop)
    return cb
```

`asyncio.get_event_loop()` 在子线程（`asyncio.to_thread` 内部）中的行为自 Python 3.10 起已弃用，Python 3.12 默认情况下会发出 `DeprecationWarning`，并在未来版本中会抛出 `RuntimeError`（无法从子线程获取主线程的事件循环）。

**修正方案**：在创建回调时（仍在异步上下文中）预先捕获 loop：

```python
def _make_redis_progress_cb(redis: Redis, channel: str) -> Callable:
    loop = asyncio.get_running_loop()         # ← 在 async 上下文中捕获，此时 loop 确实在运行

    def cb(trade_date_str: str, progress_pct: int, current_nav: float) -> None:
        msg = ...
        asyncio.run_coroutine_threadsafe(redis.publish(channel, msg), loop)
        # 无需再检查 loop.is_running()，run_coroutine_threadsafe 线程安全

    return cb
```

---

#### C-05：冒烟测试 API-58~69 内容与设计 §8 不完全一致，缺少关键边界场景

**位置**：`tests/smoke/test_api_live.py` API-58~69

**问题**：设计文档 §8 规定冒烟测试应覆盖以下场景：

| 编号 | 设计要求 | 实际实现 |
|------|----------|----------|
| API-60 | history 有鉴权 → 200 | history 无鉴权 → 401（API-60）/ 有鉴权另一编号 |
| API-61 | attribution 缺参 → 422 | history 有鉴权（编号偏移） |
| API-66 | backtest/status 有效 → 200 | backtest/run 无鉴权 → 401 |
| API-67 | backtest/status 不存在 → 404 | backtest/run 有鉴权（不验证 run 结果） |
| API-68 | backtest/result PENDING → 409 | status 无鉴权 → 401 |
| 缺失 | backtest/result PENDING → 409（冒烟级） | 未覆盖（仅 E2E-BT-05 覆盖）|
| 缺失 | backtest/result xfail 场景 | 未覆盖 |

冒烟测试的 `xfail` 和 PENDING→409 场景需要真实后端才能验证，E2E mock 测试虽覆盖逻辑，但无法在集成环境发现配置问题（如 Redis 未启动时 WS 端点直接 500）。

**修正建议**：

- 补充 `API-67`：`GET /backtest/{random_uuid}/status` → 404（创建任务后立即查询不存在的 task_id）
- 补充 `API-68`（标记 `@pytest.mark.xfail`）：创建任务后在 PENDING/RUNNING 期间查询 `/result` → 409

可在现有 `API-67/68` 测试后追加两个测试用例，或用 `@pytest.mark.xfail(strict=False)` 标记。

---

#### C-06：INT-BE-02 因 C-01 导致断言为空操作（nav_with_cost == nav_no_cost == 1_000_000），测试通过但不验证任何语义

**位置**：`tests/integration/test_int_backtest_engine.py` `test_int_be_02_cost_reduces_nav`

**问题**：

由于 C-01（`_get_quotes_at` 格式错误），`_execute_signals` 永远跳过所有信号，无论 `commission_rate`、`stamp_tax_rate`、`slippage_rate` 是否为 0，`daily_nav` 的最终值均等于 `initial_capital=1_000_000`。因此：

```python
nav_with_cost = 1_000_000.0   # 无交易发生
nav_no_cost   = 1_000_000.0   # 无交易发生
assert nav_with_cost <= nav_no_cost + 1e-9   # 1_000_000 ≤ 1_000_001，永远为 True
```

此测试的通过不证明任何语义，属于虚假绿灯（false positive）。

**修正方案**：此问题在 C-01 修复后自动具备真实断言能力，届时需同步验证 INT-BE-02 断言：

```python
# C-01 修复后，有成本路径实际发生了买入（手续费+印花税+滑点）
# final nav_with_cost = 1_000_000 - 交易成本 < 1_000_000 = nav_no_cost
assert nav_with_cost < nav_no_cost - 1.0    # 成本至少 1 元，使用严格不等号
```

注意：在 C-01 未修复前，不应先修改此断言（会变为红色）；建议与 C-01 一并提交修复。

---

#### C-07：`BacktestResult.task_id` 缺少 `unique=True` 约束，与设计 §2.1 不符

**位置**：`models/system.py` `BacktestResult` 模型（及对应迁移文件）

**问题**：

设计文档 §2.1 明确规定：

> `task_id`：`VARCHAR(36)，FK(backtest_task.task_id) UNIQUE，NOT NULL`

实际 ORM 定义：

```python
class BacktestResult(SystemBase):
    task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("backtest_task.task_id", ondelete="CASCADE"),
        nullable=False,
        # ← 缺少 unique=True
    )
```

虽然业务逻辑上 `run_task` 只写一次 `BacktestResult`，缺少 `UNIQUE` 约束意味着：重试或异常路径下可能出现同一 `task_id` 对应多条结果的情况，后续 `get_result` 使用 `scalar_one_or_none()` 会抛出 `MultipleResultsFound`。

**修正方案**：

```python
task_id: Mapped[str] = mapped_column(
    String(36),
    ForeignKey("backtest_task.task_id", ondelete="CASCADE"),
    nullable=False,
    unique=True,     # ← 补充
)
```

同时在迁移文件中为 `backtest_result.task_id` 添加 `UNIQUE` 约束，或生成新的 `alembic revision` 追加约束：

```python
op.create_unique_constraint("uq_backtest_result_task_id", "backtest_result", ["task_id"])
```

---

## 3. DoD 核查汇总

| DoD 项 | 验收内容 | 结果 |
|--------|----------|------|
| D-01 | `BacktestTask` / `BacktestResult` ORM + 迁移 0006 | ✓（`models/system.py` + `alembic/versions/0006_backtest_tables.py`；有 C-07 待修）|
| D-02 | `BacktestConfig` / `BacktestDataBundle` / `BacktestResult` dataclass | ✓ |
| D-03 | `BacktestEngine.run(config, data, progress_cb)` 主循环 | ✓（结构完整；有 C-01 格式 bug 需修复）|
| D-04 | `_buy_cost_per_unit` / `_sell_proceeds_per_unit` 纯函数 | ✓（INV-BT-01~03 全覆盖）|
| D-05 | `engine/backtest/report.py` BacktestReport（7 指标）| ✓（INV-BR-01~03 全覆盖；generate 签名正确）|
| D-06 | `BacktestService.create_task / run_task / get_task / get_result` | ✓（结构正确；有 C-03 手动 commit 待修）|
| D-07 | `BacktestService._load_data_bundle` 含【降级说明】 | ✓（adj_prices 空 DataFrame 降级注释完整）|
| D-08 | `PerformanceService.get_summary`（7 项指标含 benchmark_return）| ✓（有 C-02 日期精确匹配 bug 待修）|
| D-09 | `PerformanceService.get_history`（DailyPortfolioValue 时序）| ✓ |
| D-10 | `PerformanceService.get_attribution`（by_stock/by_strategy/by_holding_period）| ✓ |
| D-11 | `PerformanceService.get_behavioral_analysis`（signal_compliance_rate + 两项降级）| ✓（含完整【降级说明】）|
| D-12 | `/backtest/*` 3 REST + 1 WS 端点 | ✓（有 C-04 asyncio.get_event_loop 待修）|
| D-13 | `/performance/*` 4 REST 端点 | ✓ |
| D-14 | E2E 测试 E2E-BT-01~06 + E2E-PF-01~06 | ✓（共 12 个核心用例全覆盖）|
| D-15 | 集成测试 INT-PS-01~03 + INT-BE-01~02 | ✓（INT-BE-02 为空操作，C-06；INT-PS-01~03 逻辑正确）|
| D-16 | 冒烟测试 API-58~69 | ✓（内容有 C-05 所述偏差，建议补全）|

---

## 4. 评审总结

| 编号 | 级别 | 位置 | 标题 | 状态 |
|------|------|------|------|------|
| C-01 | **P2** | `engine/backtest/engine.py` `_get_quotes_at` | DataFrame 格式错误：columns=ts_codes 而非 index=ts_codes，导致 `_execute_signals` 永远跳过所有交易 | **已修复** |
| C-02 | **P2** | `services/performance_service.py` `_get_benchmark_return` | 使用精确日期匹配 `.in_([start, end])`，周末/节假日时返回 None，benchmark_return 缺失 | **已修复** |
| C-03 | **P2** | `services/backtest_service.py` `create_task`（第 48 行） | `create_task` 手动 commit 违反 get_db() 自动 commit 约定；run_task 使用 AsyncSessionLocal() 直接会话需显式 commit，不属违规 | **已修复** |
| C-04 | P3 | `api/v1/backtest.py` `_make_redis_progress_cb` | 线程回调中使用 `asyncio.get_event_loop()`，Python 3.12 已弃用；应在 async 上下文预捕获 loop | **已修复** |
| C-05 | P3 | `tests/smoke/test_api_live.py` API-60~69 | 冒烟测试内容与设计 §8 有偏差：缺少 `/backtest/{id}/result PENDING→409` 和 xfail 场景 | **已修复（补 API-70/71）** |
| C-06 | P3 | `tests/integration/test_int_backtest_engine.py` `test_int_be_02_cost_reduces_nav` | 因 C-01 导致断言为空操作（nav_with_cost == nav_no_cost），测试通过但不验证任何语义；C-01 修复后须同步更新为严格不等号 | **已修复（加 mock_strategy + 修正阈值）** |
| C-07 | P3 | `models/system.py` `BacktestResult.task_id` | 缺少 `unique=True` 约束，与设计 §2.1 "FK UNIQUE" 要求不符；异常重试路径可能导致 MultipleResultsFound | **已修复（已有 __table_args__ UniqueConstraint，补 unique=True 注释）** |

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-14 | 初版代码评审，共 7 项问题（P2×3、P3×4） |
| v1.1 | 2026-04-14 | C-01~C-07 全部修复；343 tests passed，ruff 0 error |
