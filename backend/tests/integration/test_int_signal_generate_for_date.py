"""INT-SIG-GEN-01：SignalService.generate_for_date 集成测试（Phase 10 §10.3 → V1.5-G G-4d-1）。

V1.5-G G-4d-1（管线与账户解耦，§2）：每日管线 CP3 的 generate_for_date **不再读账户**。
链路收窄为：candidate_pool → SignalGenerator（current_positions=[]）→ save → DB。
产出为**账户无关的共享信号**：BUY 候选 + 客观 pct_above_sell SELL。

移到 API 请求期 per-user 叠加（G-4d-2）/ 每日 Job（G-4d-3）的行为，其覆盖不在本文件：
- 仓位建议（suggested_pct）：G-4d-2 SignalViewService 按用户账户实时算
- 集中度 BLOCK：G-4d-2（依赖用户持仓 + sizing）
- 账户回撤 DRAWDOWN WARN：G-4d-3 每日 Job 遍历 active 账户

本文件断言解耦后管线侧行为：管线不再产 suggested_pct / 不再 BLOCK / 不再推 RISK_WARN。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.models.business import InAppNotification, Signal
from quantpilot.models.market import DailyQuote, StockInfo
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
    composite_pct_in_market: float | None = None,
) -> None:
    # 走 bulk 版：单行 upsert_candidate_pool 不支持 Phase 11 列 composite_pct_in_market
    entry: dict = {
        "ts_code": ts_code,
        "trade_date": _TRADE_DATE,
        "composite_score": composite_score,
        "trend_score": composite_score,
        "momentum_score": composite_score,
        "reversion_score": composite_score,
        "value_score": composite_score,
        "market_state": market_state,
        "in_pool": True,
        "is_holding": False,
    }
    if composite_pct_in_market is not None:
        entry["composite_pct_in_market"] = composite_pct_in_market
    await repo.upsert_candidate_pool_bulk([entry])


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
# INT-SIG-GEN-01a: pool 含高分股 → 生成 BUY 信号入库（G-4d-1 解耦：无账户上下文）
# ---------------------------------------------------------------------------
async def test_int_sig_gen_01_basic_buy_signal(db_session: AsyncSession) -> None:
    """G-4d-1 解耦：高分股 → BUY 入库；管线不再读账户，故：
    - 无需注入 account_service（generate_for_date 不再要求）
    - suggested_pct 为 None（仓位建议移到 API 请求期按用户账户叠加）
    """
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    sig_svc = SignalService(repo, config_service=cfg_svc)  # 无 account_service

    await _seed_stock(db_session, "601398.SH", industry="银行")
    await _seed_pool_entry(repo, "601398.SH", composite_score=92.0)
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)

    assert len(saved) == 1
    assert saved[0].signal_type == "BUY"
    assert saved[0].ts_code == "601398.SH"
    assert saved[0].suggested_pct is None  # 管线不再 sizing（G-4d-2 API 期叠加）


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
    sig_svc = SignalService(repo, config_service=cfg_svc)

    await settings_svc.upsert_setting("signal_params", {"buy_threshold": 95.0})
    await db_session.flush()

    await _seed_stock(db_session, "601398.SH")
    await _seed_pool_entry(repo, "601398.SH", composite_score=92.0)
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)
    assert saved == []


# ---------------------------------------------------------------------------
# INT-SIG-GEN-01c: G-4d-1 解耦——管线不再做账户集中度 BLOCK
# （集中度依赖用户持仓 + sizing → 移 G-4d-2 API 请求期 per-user 叠加）
# ---------------------------------------------------------------------------
async def test_int_sig_gen_01_no_pipeline_concentration_block(
    db_session: AsyncSession,
) -> None:
    """解耦后管线不读账户持仓，高分股 BUY 正常入库不被 BLOCK 移除；无 RISK_WARN 由管线推送。"""
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    notifier = NotificationService(db_session, cfg_svc)
    sig_svc = SignalService(
        repo, config_service=cfg_svc, notification_service=notifier,
    )

    await _seed_stock(db_session, "601939.SH", industry="银行")
    await _seed_pool_entry(repo, "601939.SH", composite_score=92.0)
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)

    # 601939 BUY 正常入库（管线不再 BLOCK）
    assert any(s.ts_code == "601939.SH" and s.signal_type == "BUY" for s in saved)
    rows = (
        await db_session.execute(
            select(Signal).where(
                Signal.ts_code == "601939.SH",
                Signal.signal_type == "BUY",
            )
        )
    ).scalars().all()
    assert len(rows) == 1

    # 管线不再推送任何 RISK_WARN（集中度告警移 API 期）
    notifs = (
        await db_session.execute(
            select(InAppNotification).where(
                InAppNotification.notify_type == "RISK_WARN"
            )
        )
    ).scalars().all()
    assert len(notifs) == 0


# ---------------------------------------------------------------------------
# INT-SIG-GEN-01d: G-4d-1 解耦——管线不再读账户回撤，不产 DRAWDOWN WARN
# （回撤告警移 G-4d-3 每日 Job 遍历 active 账户 per-user 推送）
# ---------------------------------------------------------------------------
async def test_int_sig_gen_01d_no_pipeline_drawdown_warn(
    db_session: AsyncSession,
) -> None:
    """解耦后管线不读 DailyPortfolioValue 回撤；高分股 BUY 正常入库，无 RISK_WARN。"""
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    notifier = NotificationService(db_session, cfg_svc)
    sig_svc = SignalService(
        repo, config_service=cfg_svc, notification_service=notifier,
    )

    await _seed_stock(db_session, "601398.SH", industry="银行")
    await _seed_pool_entry(repo, "601398.SH", composite_score=92.0)
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)
    assert len(saved) >= 1, "高分股应触发 BUY 信号"

    notifs = (
        await db_session.execute(
            select(InAppNotification).where(
                InAppNotification.notify_type == "RISK_WARN"
            )
        )
    ).scalars().all()
    assert len(notifs) == 0, "解耦后管线不再产 DRAWDOWN WARN（移 G-4d-3 每日 Job）"


# ---------------------------------------------------------------------------
# INT-SIG-GEN-01e: G-4d-1——池成员评分跌入卖出区间 → 共享 pct_above_sell SELL 入库
# （客观市场事实，对全体持有者有意义；无账户上下文也产出）
# ---------------------------------------------------------------------------
async def test_int_sig_gen_01e_shared_pct_above_sell(db_session: AsyncSession) -> None:
    """池成员 composite_pct_in_market≥sell 阈值 → 共享 SELL 入库（trigger=pct_above_sell）。"""
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    sig_svc = SignalService(repo, config_service=cfg_svc)

    await _seed_stock(db_session, "600519.SH", industry="食品")
    # composite_pct_in_market=0.80 ≥ 默认 sell_pct_threshold 0.70 → 共享 SELL
    await _seed_pool_entry(
        repo, "600519.SH", composite_score=30.0, composite_pct_in_market=0.80,
    )
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)

    sells = [s for s in saved if s.signal_type == "SELL"]
    assert len(sells) == 1
    assert sells[0].ts_code == "600519.SH"
    assert sells[0].trigger_reason == "pct_above_sell"
