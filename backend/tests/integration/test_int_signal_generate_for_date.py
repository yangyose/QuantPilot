"""INT-SIG-GEN-01：SignalService.generate_for_date 完整链路集成测试（Phase 10 §10.3）。

链路：candidate_pool → SignalGenerator → PositionSizer → RiskChecker → save → DB

覆盖：
- pool 含高分股 → 生成 BUY 信号
- ConfigService.get_signal_params 注入：buy_threshold 调高 → 同一 pool 无信号
- RiskChecker BLOCK 集中度告警 → 信号被移除（不入库）
- 注入 NotificationService → 风险告警写入 InAppNotification
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.models.account import Account, Position
from quantpilot.models.business import InAppNotification, Signal
from quantpilot.models.market import DailyQuote, StockInfo
from quantpilot.services.account_service import AccountService
from quantpilot.services.config_service import ConfigService
from quantpilot.services.notification_service import NotificationService
from quantpilot.services.settings_service import SettingsService
from quantpilot.services.signal_service import SignalService

_TRADE_DATE = date(2026, 4, 8)


async def _seed_stock(
    session: AsyncSession,
    ts_code: str,
    *,
    name: str = "测试股",
    industry: str = "银行",
    list_date: date = date(2020, 1, 1),
    close: float = 10.0,
    is_suspended: bool = False,
    limit_up: bool = False,
) -> None:
    session.add(
        StockInfo(
            ts_code=ts_code,
            name=name,
            sw_industry_l1=industry,
            market="MAIN",
            list_date=list_date,
            is_active=True,
        )
    )
    session.add(
        DailyQuote(
            ts_code=ts_code,
            trade_date=_TRADE_DATE,
            open=close,
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            pre_close=close,
            pct_chg=0.0,
            vol=1_000_000,
            amount=10_000_000.0,  # >5M 流动性阈值
            adj_factor=1.0,
            is_suspended=is_suspended,
            is_st=False,
            limit_up=limit_up,
            limit_down=False,
        )
    )
    await session.flush()


async def _seed_pool_entry(
    repo: MarketDataRepository,
    ts_code: str,
    composite_score: float,
    *,
    market_state: str = "OSCILLATION",
) -> None:
    await repo.upsert_candidate_pool(
        ts_code=ts_code,
        trade_date=_TRADE_DATE,
        composite_score=composite_score,
        trend_score=composite_score,
        momentum_score=composite_score,
        reversion_score=composite_score,
        value_score=composite_score,
        market_state=market_state,
        in_pool=True,
        is_holding=False,
    )


async def _seed_account(
    session: AsyncSession,
    *,
    cash: float = 1_000_000.0,
    total_assets: float = 1_000_000.0,
) -> Account:
    acc = Account(
        name="测试账户",
        account_type="REAL",
        broker="MOCK",
        total_assets=total_assets,
        cash=cash,
    )
    session.add(acc)
    await session.flush()
    return acc


async def _seed_market_state(
    repo: MarketDataRepository, state: MarketStateEnum = MarketStateEnum.OSCILLATION
) -> None:
    await repo.upsert_market_state(
        MarketStateRecord(
            trade_date=_TRADE_DATE,
            market_state=state,
            trend_strength=20.0,
            adx_value=20.0,
            ma20=10.0,
            ma60=10.0,
            state_changed=False,
            description="seed",
        )
    )


# ---------------------------------------------------------------------------
# INT-SIG-GEN-01a: pool 含高分股 → 生成 BUY 信号入库
# ---------------------------------------------------------------------------
async def test_int_sig_gen_01_basic_buy_signal(db_session: AsyncSession) -> None:
    """高分股 + 充足资金 + 振荡市 → save 一条 BUY 信号。"""
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    acc_svc = AccountService(db_session)
    sig_svc = SignalService(repo, account_service=acc_svc, config_service=cfg_svc)

    await _seed_stock(db_session, "601398.SH", industry="银行")
    await _seed_pool_entry(repo, "601398.SH", composite_score=92.0)
    await _seed_account(db_session)
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)

    assert len(saved) == 1
    assert saved[0].signal_type == "BUY"
    assert saved[0].ts_code == "601398.SH"
    assert saved[0].suggested_pct is not None and float(saved[0].suggested_pct) > 0


# ---------------------------------------------------------------------------
# INT-SIG-GEN-01b: ConfigService 提高 buy_threshold → 同一 pool 无信号（INT-CFG-02 闭环）
# ---------------------------------------------------------------------------
async def test_int_sig_gen_01_config_change_filters_signals(
    db_session: AsyncSession,
) -> None:
    """signal_params.buy_threshold=95 → score=92 不再触发 BUY（验证 ConfigService 注入生效）。"""
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    settings_svc = SettingsService(db_session)
    acc_svc = AccountService(db_session)
    sig_svc = SignalService(repo, account_service=acc_svc, config_service=cfg_svc)

    await settings_svc.upsert_setting("signal_params", {"buy_threshold": 95.0})
    await db_session.flush()

    await _seed_stock(db_session, "601398.SH")
    await _seed_pool_entry(repo, "601398.SH", composite_score=92.0)
    await _seed_account(db_session)
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)
    assert saved == []


# ---------------------------------------------------------------------------
# INT-SIG-GEN-01c: 行业集中度 BLOCK → 信号不入库 + RISK_WARN 通知入库
# ---------------------------------------------------------------------------
async def test_int_sig_gen_01_block_concentration_with_notify(
    db_session: AsyncSession,
) -> None:
    """已持有同行业 25% + 拟买另一只同行业 10% → 行业集中度 35% > 30% 上限 → BLOCK；
    注入 NotificationService → InAppNotification.RISK_WARN 入库。"""
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    acc_svc = AccountService(db_session)
    notifier = NotificationService(db_session, cfg_svc)
    sig_svc = SignalService(
        repo,
        account_service=acc_svc,
        config_service=cfg_svc,
        notification_service=notifier,
    )

    # 同行业两只股票：A 已持有 25%，B 拟买（高分）
    await _seed_stock(db_session, "601398.SH", industry="银行")
    await _seed_stock(db_session, "601939.SH", industry="银行")
    # 两只均在候选池，A 评分低（不触发 BUY）、B 评分高（触发 BUY）
    await _seed_pool_entry(repo, "601398.SH", composite_score=50.0)
    await _seed_pool_entry(repo, "601939.SH", composite_score=92.0)
    acc = await _seed_account(db_session, cash=1_000_000.0, total_assets=1_000_000.0)
    # A 持仓 25 万 → 银行行业占 25%；B BUY 10% → 银行行业 35% > 30% 上限
    db_session.add(
        Position(
            account_id=acc.id,
            ts_code="601398.SH",
            shares=25_000,
            cost_price=10.0,
            current_price=10.0,
            market_value=250_000.0,
            pnl_pct=0.0,
            open_date=date(2025, 1, 1),
            phase="HOLD",
        )
    )
    await db_session.flush()
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)

    # 601939.SH 的 BUY 被 BLOCK 移除
    assert all(s.ts_code != "601939.SH" or s.signal_type != "BUY" for s in saved)

    # DB 中确认无 601939.SH BUY 信号写入
    rows = (
        await db_session.execute(
            select(Signal).where(
                Signal.ts_code == "601939.SH",
                Signal.signal_type == "BUY",
            )
        )
    ).scalars().all()
    assert len(rows) == 0

    # RISK_WARN 通知入库（CONCENTRATION_INDUSTRY）
    notifs = (
        await db_session.execute(
            select(InAppNotification).where(
                InAppNotification.notify_type == "RISK_WARN"
            )
        )
    ).scalars().all()
    assert len(notifs) >= 1
    industry_warns = [n for n in notifs if "银行" in n.body or "INDUSTRY" in n.body]
    assert len(industry_warns) >= 1


# ---------------------------------------------------------------------------
# INT-SIG-GEN-01d (V1.0 整改 Batch 2 — B2-6 / B2-1 闭环):
# 账户回撤超 risk_limits.max_drawdown_pct → DRAWDOWN WARN 触发
# ---------------------------------------------------------------------------
async def test_int_sig_gen_01d_drawdown_warn_triggers(db_session: AsyncSession) -> None:
    """seed daily_portfolio_value 形成 25% 回撤（> 默认 20% 阈值）→ DRAWDOWN WARN 入库。"""
    from datetime import timedelta

    from quantpilot.models.account import DailyPortfolioValue

    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    acc_svc = AccountService(db_session)
    notifier = NotificationService(db_session, cfg_svc)
    sig_svc = SignalService(
        repo,
        account_service=acc_svc,
        config_service=cfg_svc,
        notification_service=notifier,
    )

    await _seed_stock(db_session, "601398.SH", industry="银行")
    await _seed_pool_entry(repo, "601398.SH", composite_score=92.0)
    acc = await _seed_account(db_session, cash=1_000_000.0, total_assets=1_000_000.0)
    await _seed_market_state(repo)

    # 净值序列：100 → 100（峰）→ 75（DD = 25% > 默认阈值 20%）
    for offset, total in [(0, 1_000_000.0), (1, 1_000_000.0), (2, 750_000.0)]:
        db_session.add(DailyPortfolioValue(
            account_id=acc.id,
            trade_date=_TRADE_DATE - timedelta(days=10 - offset),
            total_value=total,
            cash=acc.cash,
            position_value=total - float(acc.cash),
        ))
    await db_session.flush()

    saved = await sig_svc.generate_for_date(_TRADE_DATE)
    assert len(saved) >= 1, "高分股应触发 BUY 信号（与 DRAWDOWN WARN 并存）"

    # DRAWDOWN WARN 应入 InAppNotification（B2-1 闭环：CP3 现已传 max_drawdown_pct）
    notifs = (
        await db_session.execute(
            select(InAppNotification).where(
                InAppNotification.notify_type == "RISK_WARN"
            )
        )
    ).scalars().all()
    drawdown_warns = [n for n in notifs if "DRAWDOWN" in n.body or "回撤" in n.body]
    assert len(drawdown_warns) >= 1, (
        "DRAWDOWN WARN 应触发：B2-1 修复 CP3 漏传 max_drawdown_pct 后，"
        "25% 回撤 > 20% 阈值应进入告警链路"
    )
