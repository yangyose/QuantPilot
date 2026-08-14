"""V1.5-C C0：日级 IC 产出闭环单元测试（RED 先行）。

覆盖设计文档 `docs/design/phases/v1_5_c_strategy_expansion.md` §2：
- UT-C0-01：`extract_strategy_z` 下沉到 engine 层且**策略无关**——从 CompositeScore
  的 `score_breakdown_raw` 键推导策略名，新增策略（low_volatility / money_flow）
  自动纳入，不再硬编码 4 策略元组（C3/C4 入 composite 的前提）
- UT-C0-02：`plan_catchup_dates` 追平计划纯函数——只取 ≤ last_eligible、跳过已有日、
  受 max_days 限流、按升序补最旧的（保证 ICIR 窗口连续）
- UT-C0-03：`FactorMonitorService.produce_daily_ic` —— 前向窗口未完成/空 universe/
  无有效 z/无 IC point 四条早退路径均返回 0 且不写行；正常路径每策略一行；
  market_state 缺失回落 OSCILLATION；成功时打 `daily_ic_produced` 日志
- UT-C0-04：`_daily_ic_producer_job` —— 逐日调用 + 单日失败隔离不阻断其余日
- UT-C0-05：调度注册——`create_scheduler` 含 daily_ic_producer，19:30 Asia/Shanghai
- UT-C0-06：双实现消除——`scripts/backfill_daily_ic.py` 复用下沉后的公共实现

RED 阶段：以上实现全部未落地，import 即失败。
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from quantpilot.engine.diagnostics.ic_aggregator import DailyICPoint, extract_strategy_z
from quantpilot.services.factor_monitor_service import (
    FactorMonitorService,
    plan_catchup_dates,
)

# 把 backend/scripts/ 加到 path 让 backfill_daily_ic 可 import（同 test_backfill_daily_ic）
_BACKEND_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_BACKEND_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SCRIPTS))


def _composite(ts_code: str, **z_by_strategy: float | None) -> SimpleNamespace:
    """构造带 score_breakdown_raw 的 CompositeScore 替身。"""
    breakdown = {
        s: {"z_raw": z, "weight": 0.25, "contribution": 0.0}
        for s, z in z_by_strategy.items()
    }
    return SimpleNamespace(ts_code=ts_code, score_breakdown_raw=breakdown)


# ============================================================
# UT-C0-01：extract_strategy_z（engine 纯函数，策略无关）
# ============================================================

def test_ut_c0_01a_extracts_z_raw_per_strategy() -> None:
    composites = [
        _composite("000001.SZ", trend=1.2, value=-0.3),
        _composite("000002.SZ", trend=0.5, value=0.8),
    ]
    z = extract_strategy_z(composites)

    assert set(z) == {"trend", "value"}
    assert z["trend"].to_dict() == {"000001.SZ": 1.2, "000002.SZ": 0.5}
    assert z["value"].to_dict() == {"000001.SZ": -0.3, "000002.SZ": 0.8}


def test_ut_c0_01b_skips_none_and_nan_and_drops_empty_strategy() -> None:
    composites = [
        _composite("000001.SZ", trend=1.0, value=None, momentum=float("nan")),
        _composite("000002.SZ", trend=2.0, value=None, momentum=float("nan")),
    ]
    z = extract_strategy_z(composites)

    # value 全 None / momentum 全 NaN → 该策略整列缺席（不产出空 Series）
    assert set(z) == {"trend"}
    assert z["trend"].to_dict() == {"000001.SZ": 1.0, "000002.SZ": 2.0}


def test_ut_c0_01c_is_strategy_agnostic_for_new_strategies() -> None:
    """C3/C4 前提：新策略名无需登记在任何硬编码元组中即可被抽取。"""
    composites = [
        _composite("000001.SZ", low_volatility=0.4, money_flow=-1.1),
        _composite("000002.SZ", low_volatility=-0.2, money_flow=0.9),
    ]
    z = extract_strategy_z(composites)

    assert set(z) == {"low_volatility", "money_flow"}
    assert z["money_flow"].to_dict() == {"000001.SZ": -1.1, "000002.SZ": 0.9}


def test_ut_c0_01d_empty_input_returns_empty_dict() -> None:
    assert extract_strategy_z([]) == {}
    # score_breakdown_raw 缺失 / 为 None 的行不炸
    assert extract_strategy_z([SimpleNamespace(ts_code="000001.SZ",
                                               score_breakdown_raw=None)]) == {}


# ============================================================
# UT-C0-02：plan_catchup_dates（追平计划纯函数）
# ============================================================

_D = [date(2026, 5, d) for d in range(11, 21)]  # 05-11 .. 05-20（视作交易日序列）


def test_ut_c0_02a_only_takes_dates_up_to_last_eligible() -> None:
    """滞后消费：晚于 last_eligible（= t-20 交易日）的日子前向收益未实现，不可算。"""
    got = plan_catchup_dates(
        trade_dates=_D, existing=set(), last_eligible=_D[3], max_days=99,
    )
    assert got == _D[:4]


def test_ut_c0_02b_skips_existing_dates() -> None:
    got = plan_catchup_dates(
        trade_dates=_D, existing={_D[0], _D[2]}, last_eligible=_D[4], max_days=99,
    )
    assert got == [_D[1], _D[3], _D[4]]


def test_ut_c0_02c_limits_to_max_days_taking_oldest_first() -> None:
    """限流取最旧的：ICIR 窗口要连续，必须从断档最左端往右补。"""
    got = plan_catchup_dates(
        trade_dates=_D, existing=set(), last_eligible=_D[-1], max_days=3,
    )
    assert got == _D[:3]


def test_ut_c0_02d_nothing_to_do_returns_empty() -> None:
    assert plan_catchup_dates(
        trade_dates=_D, existing=set(_D), last_eligible=_D[-1], max_days=3,
    ) == []
    # last_eligible 早于所有候选 → 无可算日
    assert plan_catchup_dates(
        trade_dates=_D, existing=set(), last_eligible=date(2026, 1, 1), max_days=3,
    ) == []


# ============================================================
# UT-C0-03：FactorMonitorService.produce_daily_ic
# ============================================================

def _service(calendar_next: date | None = date(2026, 6, 10)) -> FactorMonitorService:
    calendar = MagicMock()
    if calendar_next is None:
        calendar.get_next_trade_date.side_effect = ValueError("no such trade date")
    else:
        calendar.get_next_trade_date.return_value = calendar_next
    ic_repo = MagicMock()
    ic_repo.upsert_ic_daily = AsyncMock(return_value=0)
    return FactorMonitorService(
        session=MagicMock(), engine=MagicMock(), repo=ic_repo, calendar=calendar,
    )


def _market_repo_mock(
    max_quote_date: date = date(2026, 6, 30),
    state: str | None = "UPTREND",
) -> MagicMock:
    repo = MagicMock()
    repo.get_max_daily_quote_date = AsyncMock(return_value=max_quote_date)
    repo.get_latest_market_state = AsyncMock(
        return_value=SimpleNamespace(market_state=state) if state else None
    )
    repo.get_adj_prices_bulk = AsyncMock(return_value=pd.DataFrame())
    repo.get_excluded_codes_for_ic = AsyncMock(return_value=set())
    return repo


async def test_ut_c0_03a_returns_zero_when_forward_window_incomplete() -> None:
    """d+20 交易日尚无行情数据 → 前向收益未实现，不产出任何行。"""
    svc = _service(calendar_next=date(2026, 7, 20))
    market_repo = _market_repo_mock(max_quote_date=date(2026, 6, 30))
    scoring = MagicMock()
    scoring.score_universe_for_date = AsyncMock()

    with patch(
        "quantpilot.services.factor_monitor_service.MarketDataRepository",
        return_value=market_repo,
    ):
        n = await svc.produce_daily_ic(MagicMock(), date(2026, 6, 1), scoring)

    assert n == 0
    svc._repo.upsert_ic_daily.assert_not_awaited()
    scoring.score_universe_for_date.assert_not_awaited()  # 早退，不做昂贵的全市场评分


async def test_ut_c0_03b_returns_zero_on_empty_universe() -> None:
    svc = _service()
    scoring = MagicMock()
    scoring.score_universe_for_date = AsyncMock(return_value=[])

    with patch(
        "quantpilot.services.factor_monitor_service.MarketDataRepository",
        return_value=_market_repo_mock(),
    ):
        n = await svc.produce_daily_ic(MagicMock(), date(2026, 6, 1), scoring)

    assert n == 0
    svc._repo.upsert_ic_daily.assert_not_awaited()


async def test_ut_c0_03c_writes_one_row_per_strategy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc = _service()
    scoring = MagicMock()
    scoring.score_universe_for_date = AsyncMock(return_value=[
        _composite("000001.SZ", trend=1.0, value=0.5),
        _composite("000002.SZ", trend=-1.0, value=-0.5),
    ])
    points = [
        DailyICPoint(strategy="trend", ic_value=0.03, sample_size=1800),
        DailyICPoint(strategy="value", ic_value=0.05, sample_size=1800),
    ]

    with patch(
        "quantpilot.services.factor_monitor_service.MarketDataRepository",
        return_value=_market_repo_mock(state="UPTREND"),
    ), patch(
        "quantpilot.services.factor_monitor_service.compute_daily_ic",
        return_value=points,
    ), caplog.at_level(logging.INFO):
        n = await svc.produce_daily_ic(MagicMock(), date(2026, 6, 1), scoring)

    assert n == 2
    svc._repo.upsert_ic_daily.assert_awaited_once()
    rows = svc._repo.upsert_ic_daily.await_args.args[-1]
    assert {r.strategy for r in rows} == {"trend", "value"}
    assert all(r.state == "UPTREND" for r in rows)
    assert all(r.trade_date == date(2026, 6, 1) for r in rows)
    # 激活必须可实证（同 A5b forecast_roe_override_applied 先例）
    assert "daily_ic_produced" in caplog.text


async def test_ut_c0_03d_returns_zero_when_no_ic_points() -> None:
    """横截面样本不足 → compute_daily_ic 无 point → 不写占位行。"""
    svc = _service()
    scoring = MagicMock()
    scoring.score_universe_for_date = AsyncMock(
        return_value=[_composite("000001.SZ", trend=1.0)]
    )

    with patch(
        "quantpilot.services.factor_monitor_service.MarketDataRepository",
        return_value=_market_repo_mock(),
    ), patch(
        "quantpilot.services.factor_monitor_service.compute_daily_ic",
        return_value=[],
    ):
        n = await svc.produce_daily_ic(MagicMock(), date(2026, 6, 1), scoring)

    assert n == 0
    svc._repo.upsert_ic_daily.assert_not_awaited()


async def test_ut_c0_03e_falls_back_to_oscillation_when_state_missing() -> None:
    svc = _service()
    scoring = MagicMock()
    scoring.score_universe_for_date = AsyncMock(
        return_value=[_composite("000001.SZ", trend=1.0)]
    )
    points = [DailyICPoint(strategy="trend", ic_value=0.01, sample_size=900)]

    with patch(
        "quantpilot.services.factor_monitor_service.MarketDataRepository",
        return_value=_market_repo_mock(state=None),
    ), patch(
        "quantpilot.services.factor_monitor_service.compute_daily_ic",
        return_value=points,
    ):
        n = await svc.produce_daily_ic(MagicMock(), date(2026, 6, 1), scoring)

    assert n == 1
    rows = svc._repo.upsert_ic_daily.await_args.args[-1]
    assert rows[0].state == "OSCILLATION"


# ============================================================
# UT-C0-04 / UT-C0-05：调度 Job
# ============================================================

async def test_ut_c0_04a_job_processes_each_planned_date() -> None:
    from quantpilot.pipeline.scheduler import _daily_ic_producer_job

    produce = AsyncMock(return_value=4)
    planned = [date(2026, 5, 12), date(2026, 5, 13)]

    with patch(
        "quantpilot.pipeline.scheduler.plan_catchup_dates", return_value=planned,
    ), patch(
        "quantpilot.services.factor_monitor_service.FactorMonitorService"
        ".produce_daily_ic",
        produce,
    ):
        await _daily_ic_producer_job(_DummySessionFactory(), MagicMock(), MagicMock())

    assert produce.await_count == 2
    assert [c.args[1] for c in produce.await_args_list] == planned


async def test_ut_c0_04b_single_date_failure_does_not_block_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from quantpilot.pipeline.scheduler import _daily_ic_producer_job

    produce = AsyncMock(side_effect=[RuntimeError("boom"), 4])
    planned = [date(2026, 5, 12), date(2026, 5, 13)]

    with patch(
        "quantpilot.pipeline.scheduler.plan_catchup_dates", return_value=planned,
    ), patch(
        "quantpilot.services.factor_monitor_service.FactorMonitorService"
        ".produce_daily_ic",
        produce,
    ), caplog.at_level(logging.ERROR):
        await _daily_ic_producer_job(_DummySessionFactory(), MagicMock(), MagicMock())

    assert produce.await_count == 2          # 第一天失败后仍处理第二天
    assert "daily_ic_producer" in caplog.text  # 且异常被 logger.exception 记录，非静默


def test_ut_c0_05_job_registered_at_1930_shanghai() -> None:
    from quantpilot.pipeline.scheduler import create_scheduler

    scheduler = create_scheduler(
        session_factory=MagicMock(), adapter=MagicMock(),
        validator=MagicMock(), calendar=MagicMock(),
    )
    job = scheduler.get_job("daily_ic_producer")

    assert job is not None, "daily_ic_producer Job 未注册"
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "19"
    assert fields["minute"] == "30"
    assert str(job.trigger.timezone) == "Asia/Shanghai"
    # APScheduler Job 无法访问 app.state，依赖必须显式经 args 传入
    assert job.args, "依赖须显式经 args 注入"


def test_ut_c0_07a_lag_gauge_reports_days_since_newest_daily_row() -> None:
    from quantpilot.core.metrics import FACTOR_IC_DAILY_LAG
    from quantpilot.pipeline.scheduler import _set_daily_ic_lag_gauge

    today = date(2026, 8, 14)
    _set_daily_ic_lag_gauge(
        today,
        existing={date(2026, 7, 20), date(2026, 7, 25)},
        lookback_start=date(2025, 7, 10),
    )
    assert FACTOR_IC_DAILY_LAG._value.get() == 20.0  # 08-14 − 07-25


def test_ut_c0_07b_lag_gauge_reports_large_lag_when_no_rows_at_all() -> None:
    """存量为空必须报"滞后很大"而非 0——否则生产者停摆会伪装成健康。

    这正是 2026-05~08 断档 3 个月无人发现的形态（设计 §2.1）。
    """
    from quantpilot.core.metrics import FACTOR_IC_DAILY_LAG
    from quantpilot.pipeline.scheduler import _set_daily_ic_lag_gauge

    today = date(2026, 8, 14)
    _set_daily_ic_lag_gauge(today, existing=set(), lookback_start=date(2025, 7, 10))
    assert FACTOR_IC_DAILY_LAG._value.get() == 400.0
    assert FACTOR_IC_DAILY_LAG._value.get() > 40  # 设计 §2.2 告警阈值


class _DummySessionCtx:
    """最小 AsyncSession 替身：``execute`` 返回空结果集（无已回填日）。"""

    async def __aenter__(self) -> _DummySessionCtx:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, *_a: object, **_k: object) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: [], scalar=lambda: None,
                               scalars=lambda: SimpleNamespace(all=lambda: []))

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _DummySessionFactory:
    def __call__(self) -> _DummySessionCtx:
        return _DummySessionCtx()


# ============================================================
# UT-C0-06：双实现消除（脚本与生产共用同一实现）
# ============================================================

def test_ut_c0_06a_backfill_script_reuses_sunk_extract_strategy_z() -> None:
    import backfill_daily_ic

    assert backfill_daily_ic._extract_strategy_z is extract_strategy_z, (
        "脚本必须复用下沉后的 engine 实现，禁止各留一份（漂移源）"
    )


def test_ut_c0_06b_backfill_script_reuses_sunk_scoring_factory() -> None:
    # scripts/ 非包，须在模块顶部 sys.path 注入后才能 import
    import backfill_daily_ic

    from quantpilot.services.scoring_factory import build_default_scoring_service

    assert backfill_daily_ic._build_scoring_service is build_default_scoring_service
