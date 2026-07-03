"""INT-G4D3-01~03：SignalService.evaluate_private_signals 持仓私有信号（V1.5-G G-4d-3/4）。

G-4d-1 把持仓私有 SELL / 加仓 BUY 移出管线（见 test_int_p11_sg_04 断言管线不产
hard_stop_loss）；G-4d-3/4 让每日 Job 按账户经 evaluate_private_signals 重新评估并通知。
本文件验证同一持仓场景下 evaluate_private_signals **确实**产出 hard_stop_loss 私有 SELL
与加仓 BUY（不落库）。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.models.account import Account, Position
from quantpilot.models.market import DailyQuote, StockInfo
from quantpilot.services.account_service import AccountService
from quantpilot.services.config_service import ConfigService
from quantpilot.services.signal_service import SignalService
from tests.integration._helpers import seeded_user_id

_TRADE_DATE = date(2026, 4, 8)


async def _seed_stock(session: AsyncSession, ts_code: str, *, close: float = 10.0) -> None:
    session.add(StockInfo(
        ts_code=ts_code, name=f"Test_{ts_code}",
        sw_industry_l1="银行", market="MAIN",
        list_date=date(2020, 1, 1), is_active=True,
    ))
    session.add(DailyQuote(
        ts_code=ts_code, trade_date=_TRADE_DATE,
        open=close, high=close * 1.01, low=close * 0.99,
        close=close, pre_close=close, pct_chg=0.0,
        vol=1_000_000, amount=10_000_000.0,
        adj_factor=1.0, is_suspended=False, is_st=False,
        limit_up=False, limit_down=False,
    ))
    await session.flush()


async def _seed_pool(repo: MarketDataRepository, ts_code: str, composite_pct: float) -> None:
    await repo.upsert_candidate_pool_bulk([{
        "ts_code": ts_code, "trade_date": _TRADE_DATE,
        "composite_score": 55.0,
        "trend_score": 55.0, "momentum_score": 55.0,
        "reversion_score": 55.0, "value_score": 55.0,
        "market_state": "OSCILLATION", "in_pool": True, "is_holding": True,
        "composite_z": 0.5, "composite_pct_in_market": composite_pct,
        "weights_source": "icir", "hysteresis_status": "stable",
    }])


async def _seed_account(session: AsyncSession) -> Account:
    acc = Account(
        user_id=await seeded_user_id(session),
        name="测试账户", account_type="REAL", broker="MOCK",
        total_assets=1_000_000.0, cash=800_000.0,
    )
    session.add(acc)
    await session.flush()
    return acc


async def _seed_market_state(repo: MarketDataRepository) -> None:
    await repo.upsert_market_state(MarketStateRecord(
        trade_date=_TRADE_DATE, market_state=MarketStateEnum.OSCILLATION,
        trend_strength=20.0, adx_value=20.0,
        ma20=10.0, ma60=10.0, state_changed=False, description="seed",
    ))


# ============================================================
# INT-G4D3-01：持仓浮亏 -10.7% 中性区 → evaluate_private_signals 产 hard_stop_loss
# ============================================================
async def test_int_g4d3_01_hard_stop_loss_surfaced(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    acc_svc = AccountService(db_session)
    sig_svc = SignalService(repo, config_service=cfg_svc)

    await _seed_stock(db_session, "PRIV01.SZ", close=10.0)
    # pct=0.30 中性区（不进 BUY、不触 pct_above_sell 卖出）→ 只余 hard_stop_loss 可能
    await _seed_pool(repo, "PRIV01.SZ", composite_pct=0.30)
    acc = await _seed_account(db_session)
    db_session.add(Position(
        account_id=acc.id, ts_code="PRIV01.SZ",
        shares=5000, cost_price=11.2, current_price=10.0,
        market_value=50_000.0, pnl_pct=-0.107,  # 浮亏 10.7% > 8%
    ))
    await db_session.flush()
    await _seed_market_state(repo)

    positions = await acc_svc.get_positions(acc.id)
    result = await sig_svc.evaluate_private_signals(_TRADE_DATE, positions)

    assert len(result) == 1
    assert result[0].signal_type == "SELL"
    assert result[0].trigger_reason == "hard_stop_loss"
    assert result[0].ts_code == "PRIV01.SZ"


# ============================================================
# INT-G4D3-02：健康持仓（浮盈 + 中性评分）→ 无私有 SELL
# ============================================================
async def test_int_g4d3_02_healthy_holding_no_private_sell(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    acc_svc = AccountService(db_session)
    sig_svc = SignalService(repo, config_service=cfg_svc)

    await _seed_stock(db_session, "PRIV02.SZ", close=10.0)
    await _seed_pool(repo, "PRIV02.SZ", composite_pct=0.30)
    acc = await _seed_account(db_session)
    db_session.add(Position(
        account_id=acc.id, ts_code="PRIV02.SZ",
        shares=5000, cost_price=9.5, current_price=10.0,
        market_value=50_000.0, pnl_pct=0.05,  # 浮盈
    ))
    await db_session.flush()
    await _seed_market_state(repo)

    positions = await acc_svc.get_positions(acc.id)
    result = await sig_svc.evaluate_private_signals(_TRADE_DATE, positions)
    assert result == []


# ============================================================
# INT-G4D3-03：持仓 + 低分位（买入区）+ 浮盈 → 加仓 BUY 浮现（G-4d-4 拍板）
# ============================================================
async def test_int_g4d3_03_add_position_buy_surfaced(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    acc_svc = AccountService(db_session)
    sig_svc = SignalService(repo, config_service=cfg_svc)

    await _seed_stock(db_session, "PRIV03.SZ", close=10.0)
    # pct=0.005 ≤ buy 阈值 0.05 → 买入区；持仓 + 浮盈 → SDD §10.1 can_add
    await _seed_pool(repo, "PRIV03.SZ", composite_pct=0.005)
    acc = await _seed_account(db_session)
    db_session.add(Position(
        account_id=acc.id, ts_code="PRIV03.SZ",
        shares=5000, cost_price=9.5, current_price=10.0,
        market_value=50_000.0, pnl_pct=0.05,  # 浮盈 → 加仓条件满足
    ))
    await db_session.flush()
    await _seed_market_state(repo)

    positions = await acc_svc.get_positions(acc.id)
    result = await sig_svc.evaluate_private_signals(_TRADE_DATE, positions)

    assert len(result) == 1
    assert result[0].signal_type == "BUY"
    assert result[0].ts_code == "PRIV03.SZ"


# ============================================================
# INT-G4D3-04：当日池未产出（15:05 Job 早于 17:30 管线）→ 回落最新池日期评估
# ============================================================
async def test_int_g4d3_04_falls_back_to_latest_pool_date(db_session: AsyncSession) -> None:
    from datetime import timedelta

    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    acc_svc = AccountService(db_session)
    sig_svc = SignalService(repo, config_service=cfg_svc)

    # 池/行情/市场状态都只存在于 _TRADE_DATE；评估日 = 次日（当日池不存在）
    await _seed_stock(db_session, "PRIV04.SZ", close=10.0)
    await _seed_pool(repo, "PRIV04.SZ", composite_pct=0.30)
    acc = await _seed_account(db_session)
    db_session.add(Position(
        account_id=acc.id, ts_code="PRIV04.SZ",
        shares=5000, cost_price=11.2, current_price=10.0,
        market_value=50_000.0, pnl_pct=-0.107,
    ))
    await db_session.flush()
    await _seed_market_state(repo)

    positions = await acc_svc.get_positions(acc.id)
    result = await sig_svc.evaluate_private_signals(
        _TRADE_DATE + timedelta(days=1), positions
    )

    assert len(result) == 1
    assert result[0].trigger_reason == "hard_stop_loss"
