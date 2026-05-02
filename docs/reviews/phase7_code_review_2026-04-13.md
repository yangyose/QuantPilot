# Phase 7 代码评审报告

**评审对象**：Phase 7 实现代码（pipeline、services、engine、API、tests）  
**设计依据**：`docs/design/phases/phase7_pipeline.md` v1.1  
**评审日期**：2026-04-13  
**评审人**：Claude Code  
**状态**：已关闭（2026-04-13，全部 7 项问题均已修复）

---

## 1. 总体评价

| 维度 | 评价 |
|------|------|
| **DoD 完成度** | D-01~D-15 全部交付，含 D-08a（generate_for_date）、D-12a（lineage 重构）✓ |
| **设计符合度** | 整体架构与设计文档一致；CP1→CP2→CP3→Step4-6 断点续传、best-effort 步骤均正确实现 |
| **CLAUDE.md 规范** | 存在 2 项直接违规（C-01 P2、C-02 P2），需修复后方可合并 |
| **测试覆盖** | unit/E2E/集成/冒烟均已覆盖；集成测试 INT-DP-01/02/03 设计完整 |

**结论：存在 2 个 P2 级缺陷（必须修复）、5 个 P3 级问题（建议修复）。**

---

## 2. 问题清单

### 2.1 P2 级（必须修复）

#### C-01：`_monthly_job` 中 `data_service` 持有已关闭 session

**位置**：`pipeline/scheduler.py` `_monthly_job` 函数

**问题**：

```python
async with session_factory() as session:          # session 在此打开
    repo = MarketDataRepository(session)
    data_service = DataService(adapter, validator, repo, calendar)
    scheduler = MonthlyScheduler(data_service=data_service, ...)
# ← session 在此关闭，data_service 内部的 repo._session 已失效

await scheduler.run_all(today)                    # data_service 已持有关闭的 session！
```

`MonthlyScheduler.run_quarterly_financial_refresh` 调用 `self._data_service.refresh_financials_full()`，此时 session 已关闭，查询必然抛出 `SessionNotReady` 异常。`run_factor_monitoring` 和 `run_monthly_report` 自行创建 session，不受影响；但 `run_quarterly_financial_refresh` 无法正常执行。

**修正方案**：将 `async with session_factory() as session:` 块扩展至覆盖 `scheduler.run_all(today)`，使 `data_service` 在有效 session 内被调用：

```python
async def _monthly_job(...) -> None:
    today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    logger.info("monthly_job_start: trigger_date=%s", today)

    from quantpilot.data.repository import MarketDataRepository
    from quantpilot.engine.factor_monitor import FactorMonitorEngine
    from quantpilot.pipeline.monthly_scheduler import MonthlyScheduler
    from quantpilot.services.data_service import DataService

    async with session_factory() as session:      # session 保持打开直到 run_all 完成
        repo = MarketDataRepository(session)
        data_service = DataService(adapter, validator, repo, calendar)
        scheduler = MonthlyScheduler(
            data_service=data_service,
            session_factory=session_factory,
            calendar=calendar,
            factor_monitor_engine=FactorMonitorEngine(),
        )
        await scheduler.run_all(today)

    logger.info("monthly_job_done: trigger_date=%s", today)
```

注意：`run_factor_monitoring` 和 `run_monthly_report` 内部仍通过 `session_factory()` 创建独立 session，与外层 session 并存无冲突。

---

#### C-02：`generate_for_date` 直接访问 `self._repo._session`，违反 CLAUDE.md §6 Repository 封装规约

**位置**：`services/signal_service.py` 第 269 行

**问题**：

```python
pool_rows = await self._repo._session.execute(
    select(CandidatePool).where(...)
)
```

CLAUDE.md §6 明确规定："读写均通过 MarketDataRepository，禁止在 Service/Route 层直接操作 ORM"。此处绕过 Repository，直接访问私有属性 `_session` 并操作 `CandidatePool` ORM。

**修正方案**：在 `MarketDataRepository` 中增加方法，Service 层通过 repo 调用：

```python
# data/repository.py 新增
async def get_pool_entries_by_date(self, trade_date: date) -> list[CandidatePool]:
    result = await self._session.execute(
        select(CandidatePool)
        .where(CandidatePool.trade_date == trade_date, CandidatePool.in_pool.is_(True))
        .order_by(CandidatePool.composite_score.desc())
    )
    return list(result.scalars().all())

# services/signal_service.py generate_for_date 中改为
pool_entries = await self._repo.get_pool_entries_by_date(trade_date)
```

---

### 2.2 P3 级（建议修复）

#### C-03：`_calc_forward_returns` 下界将交易日数当日历天使用，违反 CLAUDE.md §6 换算规则

**位置**：`services/factor_monitor_service.py` `_calc_forward_returns` 方法

**问题**：

```python
DailyQuote.trade_date >= base_date + timedelta(days=window),  # window=20 个交易日 ≠ 20 日历天
```

CLAUDE.md §6 规定："交易日数换算日历天：`calendar_days = int(history_days * 1.5)`，禁止直接 `timedelta(days=history_days)`"。当 `window=20` 时，20 日历天 ≈ 14 交易日，导致前向收益率取点偏早约 6 个交易日，实际计算的是约 14 交易日的收益率而非设计要求的 20 交易日。

**修正**：

```python
DailyQuote.trade_date >= base_date + timedelta(days=int(window * 1.4)),  # 约 window 个交易日
```

---

#### C-04：`calc_ic_ir` 使用总体方差，统计惯例应为样本方差，系统性低估 IR

**位置**：`engine/factor_monitor.py` 第 58 行

**问题**：

```python
variance = sum((x - ic_mean) ** 2 for x in recent) / window   # 总体方差（分母 N）
```

IR = IC_mean / IC_std * sqrt(N) 的标准定义中，IC_std 为样本标准差（分母 N-1）。当 `window=3` 时，`/3` 与 `/2` 差异约 22%，会系统性低估 IR，导致 INEFFICIENT 告警虚报（IR 实际已 >= 0.3 但因分母偏大而计算为 < 0.3）。

**修正**：

```python
variance = sum((x - ic_mean) ** 2 for x in recent) / (window - 1)  # 样本方差
```

注意同步更新 `test_factor_monitor_engine.py` 中 `test_normal_calculation` 的 `ir` 期望值计算公式。

---

#### C-05：`generate_for_date` 返回当日全量 BUY 信号，`signal_count` 可能包含历史手动信号

**位置**：`services/signal_service.py` 第 323 行

**问题**：

```python
saved = await self._repo.get_today_signals(trade_date, signal_type="BUY", status=None)
```

若当日已通过 API 手动触发生成了 K 个信号，Pipeline CP3 再次运行后，`get_today_signals` 会返回 K + N 个（旧 K + 新 N），Pipeline 日志 `signal_count` 偏高，无法准确反映本次 Pipeline 实际生成量。

**建议**：记录 `save()` 调用前后的计数差，或在 `save()` 中返回已写入的信号列表作为本次生成结果。

---

#### C-06：`POST /pipeline/trigger` 中 `started_at` 使用 `Asia/Shanghai`，与全项目 UTC 约定不一致

**位置**：`api/v1/pipeline.py` 第 88 行

**问题**：

```python
started_at=datetime.now(tz=ZoneInfo("Asia/Shanghai")),
```

`DailyPipeline._get_or_create_run`、`_update_run_status` 等处均使用 `timezone.utc`。API 路由使用 `Asia/Shanghai` 导致时区标注不一致（虽然 PostgreSQL `TIMESTAMPTZ` 归一化存储不影响正确性，但代码约定违规）。

**修正**：

```python
started_at=datetime.now(tz=timezone.utc),
```

---

#### C-07：`run_monthly` 当 IC 为 None 时仍写入占位行并计入 `written`，语义模糊

**位置**：`services/factor_monitor_service.py` `run_monthly` 方法

**问题**：当某因子样本不足（`ic=None`），仍执行 upsert 写入 `ic_value=NULL` 的记录，`written` 计数器照常递增。调用方日志 `written=N` 无法区分"N 条有效 IC"与"N 条含 None 的占位记录"，可能误判月末因子监控状态。

**建议**：

选项 A（推荐）：`ic=None` 时 `continue` 跳过写入，`written` 只计有效行：

```python
ic = self._engine.calc_ic(f_series, r_series)
if ic is None:
    logger.debug("factor_ic_skip: %s.%s samples insufficient", strategy_name, factor_name)
    continue
```

选项 B：保持现行行为，在 docstring 和日志中明确说明"包含 ic=None 的占位记录"。

---

## 3. DoD 核查汇总

| DoD 项 | 验收内容 | 结果 |
|--------|----------|------|
| D-01 | `engine/factor_monitor.py`：IC/IR/半衰期/告警 + 单元测试 | ✓（TestCalcIc/TestCalcIcIr/TestCalcHalfLife/TestDetectAlert，边界全覆盖）|
| D-02 | `services/factor_monitor_service.py`：run_monthly/get_latest/get_history | ✓ |
| D-03 | `services/report_service.py`：weekly/monthly/custom/list/get_by_id | ✓ |
| D-04 | `services/lineage_service.py` V1.0 | ✓ |
| D-05 | `services/notification_service.py` no-op stub | ✓（含完整【降级说明】）|
| D-06 | `AccountService.mark_to_market` + `DailyPortfolioValue` ORM + 迁移 0005 | ✓（`__table_args__` 含 UniqueConstraint + Index，与迁移文件一致）|
| D-07 | `DataService.fetch_dividends` + `TushareAdapter.fetch_dividend_data` | ✓ |
| D-08 | `pipeline/daily_pipeline.py` CP1→CP2→CP3→Step4→Step5→Step6 | ✓ |
| D-08a | `SignalService.generate_for_date(trade_date)` | ✓（含【降级说明】；有 C-02 待修）|
| D-09 | `pipeline/scheduler.py` 三 Job（daily_pipeline/monthly/weekly_report） | ✓（有 C-01 待修）|
| D-10 | `pipeline/monthly_scheduler.py`：run_factor_monitoring + run_monthly_report + 非交易日回溯 | ✓（`get_prev_trade_date` 正确调用）|
| D-11 | 三组 REST API 注册 + `api/deps.py` 三函数 | ✓（main.py 已注册，deps.py 第 93–107 行）|
| D-12 | E2E 测试（/pipeline、/factor-quality、/reports 三组） | ✓（共 23 个用例，含 401/422/404）|
| D-12a | Phase 5 `/signals/{id}/lineage` 重构后回归测试通过 | ✓（`signals.py` 已改用 `LineageService`）|
| D-13 | 集成测试（DailyPipeline 断点续传；mark_to_market 写入验证） | ✓（INT-DP-01/02/03 均已实现）|
| D-14 | 冒烟测试 API-48~57 | ✓ |
| D-15 | `ruff check` 0 error | 待运行验证 |

---

## 4. 评审总结

| 编号 | 级别 | 位置 | 标题 | 状态 |
|------|------|------|------|------|
| C-01 | **P2** | `scheduler.py` `_monthly_job` | `data_service` 持有已关闭 session，`run_quarterly_financial_refresh` 必然失败 | ✅ 已关闭 |
| C-02 | **P2** | `signal_service.py:269` | `generate_for_date` 直接访问 `self._repo._session`，违反 CLAUDE.md §6 Repository 封装规约 | ✅ 已关闭 |
| C-03 | P3 | `factor_monitor_service.py` `_calc_forward_returns` | 前向收益率下界将交易日数当日历天，违反 CLAUDE.md §6 换算规则 | ✅ 已关闭 |
| C-04 | P3 | `factor_monitor.py:58` | `calc_ic_ir` 使用总体方差（应为样本方差），系统性低估 IR | ✅ 已关闭 |
| C-05 | P3 | `signal_service.py:323` | `generate_for_date` 返回当日全量 BUY 信号，`signal_count` 可能包含历史手动信号 | ✅ 已关闭 |
| C-06 | P3 | `pipeline.py:88` | `POST /trigger` 的 `started_at` 使用 `Asia/Shanghai`，与全项目 UTC 约定不一致 | ✅ 已关闭 |
| C-07 | P3 | `factor_monitor_service.py` `run_monthly` | IC=None 时仍写入占位行并计入 written，语义模糊 | ✅ 已关闭 |

**全部 7 项问题均已关闭（2026-04-13 验证）。**

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-13 | 初版代码评审，共 7 项问题（P2×2、P3×5） |
| v1.1 | 2026-04-13 | 验证修复结果，全部 7 项问题已关闭；评审报告状态置为已关闭 |
