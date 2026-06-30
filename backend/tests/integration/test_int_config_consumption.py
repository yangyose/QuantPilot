"""INT-CFG-02/03/04：配置消费链路集成测试（Phase 10 §10.3）。

INT-CFG-02：修改 `signal_params.buy_threshold` → 第二次 `generate_for_date` 信号数变化
            （证明 ConfigService 注入路径生效，不再读 SDD 默认值）。
INT-CFG-03：DailyPipeline._write_config_snapshot → `pipeline_run.config_snapshot` 含
            11 个运行时键（不含 backtest_defaults）+ 反映最新写入的 user_config 值。
INT-CFG-04：修改 `strategy_weights` → DailyPipeline._cp2_scoring 实例化的 Scorer
            必须使用新权重（评审 C-01/C-02/C-03 修复后的回归守门）。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.models.account import Account
from quantpilot.models.market import DailyQuote, StockInfo
from quantpilot.models.system import PipelineRun
from quantpilot.pipeline.daily_pipeline import DailyPipeline
from quantpilot.services.account_service import AccountService
from quantpilot.services.config_service import ConfigService
from quantpilot.services.settings_service import SettingsService
from quantpilot.services.signal_service import SignalService
from tests.integration._helpers import seeded_user_id

_TRADE_DATE = date(2026, 4, 9)


# ---------------------------------------------------------------------------
# INT-CFG-02: 修改 signal_params.buy_threshold → 同一 pool 信号数变化
# ---------------------------------------------------------------------------
async def test_int_cfg_02_buy_threshold_change_filters_signals(
    db_session: AsyncSession,
) -> None:
    """同一 candidate_pool（score=85）：
    - buy_threshold=80（默认）→ 1 条 BUY
    - 修改 buy_threshold=90 → 0 条 BUY
    """
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    settings_svc = SettingsService(db_session)
    acc_svc = AccountService(db_session)
    sig_svc = SignalService(repo, account_service=acc_svc, config_service=cfg_svc)

    # 准备数据
    db_session.add(
        StockInfo(
            ts_code="601398.SH",
            name="工商银行",
            sw_industry_l1="银行",
            market="MAIN",
            list_date=date(2010, 1, 1),
            is_active=True,
        )
    )
    db_session.add(
        DailyQuote(
            ts_code="601398.SH",
            trade_date=_TRADE_DATE,
            open=10.0, high=10.1, low=9.9, close=10.0,
            pre_close=10.0, pct_chg=0.0, vol=1_000_000, amount=10_000_000.0,
            adj_factor=1.0, is_suspended=False, is_st=False,
            limit_up=False, limit_down=False,
        )
    )
    await db_session.flush()
    await repo.upsert_candidate_pool(
        ts_code="601398.SH",
        trade_date=_TRADE_DATE,
        composite_score=85.0,
        trend_score=85.0, momentum_score=85.0,
        reversion_score=85.0, value_score=85.0,
        market_state="OSCILLATION",
        in_pool=True, is_holding=False,
    )
    db_session.add(
        Account(
            user_id=await seeded_user_id(db_session),
            name="测试", account_type="REAL", broker="MOCK",
            total_assets=1_000_000.0, cash=1_000_000.0,
        )
    )
    await db_session.flush()
    await repo.upsert_market_state(
        MarketStateRecord(
            trade_date=_TRADE_DATE,
            market_state=MarketStateEnum.OSCILLATION,
            trend_strength=20.0, adx_value=20.0,
            ma20=10.0, ma60=10.0,
            state_changed=False, description="seed",
        )
    )

    # 第一次：默认 buy_threshold=80 → score=85 触发 BUY
    saved_v1 = await sig_svc.generate_for_date(_TRADE_DATE)
    assert len(saved_v1) == 1
    assert saved_v1[0].signal_type == "BUY"

    # 修改 signal_params.buy_threshold=90，并新建 ConfigService 触发 re-read
    await settings_svc.upsert_setting("signal_params", {"buy_threshold": 90.0})
    await db_session.flush()

    # 标记上次的 BUY 信号为 EXPIRED 释放唯一约束（避免 upsert 冲突干扰断言）
    from sqlalchemy import update

    from quantpilot.models.business import Signal as SignalModel
    await db_session.execute(
        update(SignalModel)
        .where(SignalModel.ts_code == "601398.SH", SignalModel.trade_date == _TRADE_DATE)
        .values(status="EXPIRED")
    )
    await db_session.flush()

    cfg_svc_v2 = ConfigService(db_session)  # 新实例，无 Redis 缓存
    sig_svc_v2 = SignalService(
        repo, account_service=acc_svc, config_service=cfg_svc_v2
    )
    saved_v2 = await sig_svc_v2.generate_for_date(_TRADE_DATE)

    # 修改后：score=85 < buy_threshold=90 → 不触发 BUY
    new_buys = [s for s in saved_v2 if s.signal_type == "BUY" and s.status == "NEW"]
    assert new_buys == []


# ---------------------------------------------------------------------------
# INT-CFG-03: DailyPipeline._write_config_snapshot → pipeline_run.config_snapshot
# ---------------------------------------------------------------------------
async def test_int_cfg_03_pipeline_config_snapshot_written(
    db_engine: AsyncEngine,
) -> None:
    """启动时一次性写 snapshot → 11 个运行时键（不含 backtest_defaults）+
    反映最新 user_config 写入值。"""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    snap_trade_date = date(2026, 4, 10)
    pipeline = DailyPipeline(
        session_factory=factory,
        adapter=MagicMock(),
        validator=MagicMock(),
        calendar=MagicMock(),
    )

    try:
        # 1) 写一个 user_config 自定义值（与默认不同）
        async with factory() as session:
            settings_svc = SettingsService(session)
            await settings_svc.upsert_setting(
                "signal_params", {"buy_threshold": 88.0}
            )
            await settings_svc.upsert_setting(
                "risk_limits", {"max_single_stock_pct": 0.15}
            )
            await session.commit()

        # 2) 创建 PipelineRun（模拟 _get_or_create_run 已执行）
        async with factory() as session:
            run = PipelineRun(
                trade_date=snap_trade_date,
                status="RUNNING",
                started_at=datetime.now(tz=timezone.utc),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
            run_id = run.id

        # 3) 调用 _write_config_snapshot
        async with factory() as session:
            run_proxy = (
                await session.execute(
                    select(PipelineRun).where(PipelineRun.id == run_id)
                )
            ).scalar_one()
            await pipeline._write_config_snapshot(run_proxy)

        # 4) 校验 snapshot 内容
        async with factory() as session:
            saved_run = (
                await session.execute(
                    select(PipelineRun).where(PipelineRun.id == run_id)
                )
            ).scalar_one()
            snap = saved_run.config_snapshot

        assert snap is not None
        # 11 个运行时键 + _snapshot_at
        for key in [
            "signal_params", "risk_limits", "market_state_params",
            "universe_params", "strategy_weights",
            "strategy_params_trend", "strategy_params_momentum",
            "strategy_params_mean_reversion", "strategy_params_value",
            "notification_prefs", "factor_monitor_params",
            "_snapshot_at",
        ]:
            assert key in snap, f"snapshot missing key: {key}"

        # backtest_defaults 不应出现（§4.3 Q-5）
        assert "backtest_defaults" not in snap

        # 自定义值正确反映（partial-overlay 后 buy_threshold=88，其它字段保持默认）
        assert snap["signal_params"]["buy_threshold"] == 88.0
        assert snap["risk_limits"]["max_single_stock_pct"] == 0.15
        # 默认值仍存在于其它字段
        assert "sell_threshold" in snap["signal_params"]
        assert "max_industry_pct" in snap["risk_limits"]

    finally:
        # 清理 PipelineRun + 测试写入的 user_config（避免污染后续测试）
        from quantpilot.models.system import UserConfig, UserConfigHistory

        async with factory() as session:
            run = (
                await session.execute(
                    select(PipelineRun).where(PipelineRun.trade_date == snap_trade_date)
                )
            ).scalar_one_or_none()
            if run is not None:
                await session.delete(run)
            for key in ("signal_params", "risk_limits"):
                cfg = (
                    await session.execute(
                        select(UserConfig).where(UserConfig.config_key == key)
                    )
                ).scalar_one_or_none()
                if cfg is not None:
                    await session.delete(cfg)
                # 同步清理 history（否则 changed_at 仍残留）
                hist_rows = (
                    await session.execute(
                        select(UserConfigHistory).where(
                            UserConfigHistory.config_key == key
                        )
                    )
                ).scalars().all()
                for h in hist_rows:
                    await session.delete(h)
            await session.commit()


# ---------------------------------------------------------------------------
# INT-CFG-04: 修改 strategy_weights → CP2 Scorer 使用新权重，composite 反映改动
#  评审 C-01/C-02/C-03 修复后的回归守门：若有人将 `Scorer(weights_cfg)` 退回为
#  `Scorer()`（默认权重），本用例立即失败。
# ---------------------------------------------------------------------------
async def test_int_cfg_04_strategy_weights_drives_composite_score(
    db_engine: AsyncEngine,
) -> None:
    """同一 user_config 写极端 strategy_weights → 跑 daily_pipeline →
    `pipeline_run.config_snapshot.strategy_weights` 含新值；
    `_cp2_scoring` 实例化的 Scorer._weights 等同新值；
    Scorer.aggregate 用合成 StrategyScore 调用，composite 反映新权重。
    """
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    snap_date = date(2026, 4, 15)
    custom_uptrend = {"trend": 0.0, "momentum": 0.0, "mean_reversion": 0.0, "value": 1.0}
    custom_oscillation = {"trend": 1.0, "momentum": 0.0, "mean_reversion": 0.0, "value": 0.0}
    custom_downtrend = {"trend": 0.0, "momentum": 1.0, "mean_reversion": 0.0, "value": 0.0}

    pipeline = DailyPipeline(
        session_factory=factory,
        adapter=MagicMock(),
        validator=MagicMock(),
        calendar=MagicMock(),
    )

    captured: dict = {}

    class _CapturingScoringService:
        def __init__(
            self, *, repo, universe_filter, strategies, scorer, pool_manager, calendar,
            factor_monitor=None,
        ) -> None:
            captured["scorer"] = scorer

        async def run_daily_scoring(
            self, trade_date, holding_codes=frozenset(),
        ):
            return []

    try:
        # 1) 写自定义 strategy_weights
        async with factory() as session:
            await SettingsService(session).upsert_setting(
                "strategy_weights",
                {
                    "uptrend": custom_uptrend,
                    "oscillation": custom_oscillation,
                    "downtrend": custom_downtrend,
                },
            )
            await session.commit()

        # 2) 预写 PipelineRun（cp1 已完成，跳过 CP1 数据采集；snapshot=None 触发首次写入）
        async with factory() as session:
            run = PipelineRun(
                trade_date=snap_date,
                status="RUNNING",
                started_at=datetime.now(tz=timezone.utc),
                cp1_data_ready=True,
                cp1_at=datetime.now(tz=timezone.utc),
                data_snapshot_version="20260415T000000Z",
            )
            session.add(run)
            await session.commit()

        # 3) 运行流水线：CP2 走 capturing service；CP3/Step4-6 走 mocks
        mock_signal = AsyncMock()
        mock_signal.generate_for_date.return_value = []
        mock_signal.expire_old_signals.return_value = 0
        mock_account = AsyncMock()
        mock_account.mark_to_market.return_value = []
        mock_ds = AsyncMock()
        mock_ds.fetch_dividends.return_value = 0

        with (
            patch(
                "quantpilot.services.strategy_service.ScoringService",
                _CapturingScoringService,
            ),
            patch(
                "quantpilot.services.signal_service.SignalService",
                return_value=mock_signal,
            ),
            patch(
                "quantpilot.services.account_service.AccountService",
                return_value=mock_account,
            ),
            patch(
                "quantpilot.services.data_service.DataService",
                return_value=mock_ds,
            ),
        ):
            run_result = await pipeline.run(snap_date)

        # 4) 校验 snapshot 写入正确
        assert run_result.status == "SUCCESS"
        assert run_result.config_snapshot is not None
        snap_weights = run_result.config_snapshot["strategy_weights"]
        assert snap_weights["oscillation"] == custom_oscillation
        assert snap_weights["uptrend"] == custom_uptrend
        assert snap_weights["downtrend"] == custom_downtrend

        # 5) 校验 CP2 实例化的 Scorer 使用 snapshot 权重
        captured_scorer = captured.get("scorer")
        assert captured_scorer is not None, "ScoringService not invoked in CP2"
        assert captured_scorer._weights.oscillation == custom_oscillation
        assert captured_scorer._weights.uptrend == custom_uptrend
        assert captured_scorer._weights.downtrend == custom_downtrend

        # 6) 用合成 StrategyScore 调 aggregate 验证 composite 反映新权重
        from quantpilot.engine.strategies.base import StrategyScore

        fake_scores = {
            "trend": [StrategyScore(
                ts_code="000001.SZ", raw_factors={}, score=80.0, reason="t")],
            "momentum": [StrategyScore(
                ts_code="000001.SZ", raw_factors={}, score=20.0, reason="m")],
            "mean_reversion": [StrategyScore(
                ts_code="000001.SZ", raw_factors={}, score=30.0, reason="r")],
            "value": [StrategyScore(
                ts_code="000001.SZ", raw_factors={}, score=40.0, reason="v")],
        }
        # OSCILLATION 权重 trend=1.0 → composite == trend_score == 80
        composite_osc = captured_scorer.aggregate_legacy(MarketStateEnum.OSCILLATION, fake_scores)
        assert len(composite_osc) == 1
        assert composite_osc[0].composite_score == pytest.approx(80.0)
        # DOWNTREND 权重 momentum=1.0 → composite == momentum_score == 20
        composite_dn = captured_scorer.aggregate_legacy(MarketStateEnum.DOWNTREND, fake_scores)
        assert composite_dn[0].composite_score == pytest.approx(20.0)
        # UPTREND 权重 value=1.0 → composite == value_score == 40
        composite_up = captured_scorer.aggregate_legacy(MarketStateEnum.UPTREND, fake_scores)
        assert composite_up[0].composite_score == pytest.approx(40.0)

    finally:
        # 清理 PipelineRun + user_config + history
        from quantpilot.models.system import UserConfig, UserConfigHistory

        async with factory() as session:
            run = (
                await session.execute(
                    select(PipelineRun).where(PipelineRun.trade_date == snap_date)
                )
            ).scalar_one_or_none()
            if run is not None:
                await session.delete(run)
            cfg = (
                await session.execute(
                    select(UserConfig).where(UserConfig.config_key == "strategy_weights")
                )
            ).scalar_one_or_none()
            if cfg is not None:
                await session.delete(cfg)
            hist_rows = (
                await session.execute(
                    select(UserConfigHistory).where(
                        UserConfigHistory.config_key == "strategy_weights"
                    )
                )
            ).scalars().all()
            for h in hist_rows:
                await session.delete(h)
            await session.commit()
