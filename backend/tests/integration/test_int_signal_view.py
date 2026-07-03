"""INT-G4D2-01~03：SignalViewService 账户维度叠加跨账户隔离（V1.5-G G-4d-2 §2）。

管线产的是**账户无关的共享信号**（G-4d-1）；同一批共享信号 dict 经不同账户叠加，
is_holding / suggested_pct 必须按各账户持仓与资产独立计算，互不串扰。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.models.account import Account, Position
from quantpilot.services.account_service import AccountService
from quantpilot.services.config_service import ConfigService
from quantpilot.services.signal_view_service import SignalViewService
from tests.integration._helpers import seeded_user_id

_TRADE_DATE = date(2026, 4, 8)


def _signal_dict(ts_code: str, signal_type: str = "BUY") -> dict:
    """共享信号响应 dict（叠加前，suggested_pct/is_holding 为缺省）。"""
    return {
        "id": 1,
        "ts_code": ts_code,
        "signal_type": signal_type,
        "trade_date": _TRADE_DATE,
        "score": 85.0,
        "suggested_pct": None,
        "is_holding": False,
    }


async def _seed_account(
    session: AsyncSession, *, cash: float = 800_000.0, total: float = 1_000_000.0
) -> Account:
    acc = Account(
        user_id=await seeded_user_id(session),
        name="测试账户", account_type="REAL", broker="MOCK",
        total_assets=total, cash=cash,
    )
    session.add(acc)
    await session.flush()
    return acc


async def _seed_position(session: AsyncSession, account_id: int, ts_code: str) -> None:
    session.add(Position(
        account_id=account_id, ts_code=ts_code,
        shares=5000, cost_price=9.8, current_price=10.0,
        market_value=50_000.0, pnl_pct=0.02,
    ))
    await session.flush()


async def _seed_market_state(repo: MarketDataRepository) -> None:
    await repo.upsert_market_state(MarketStateRecord(
        trade_date=_TRADE_DATE, market_state=MarketStateEnum.OSCILLATION,
        trend_strength=20.0, adx_value=20.0,
        ma20=10.0, ma60=10.0, state_changed=False, description="seed",
    ))


def _make_view_service(session: AsyncSession) -> SignalViewService:
    repo = MarketDataRepository(session)
    return SignalViewService(
        repo,
        account_service=AccountService(session),
        config_service=ConfigService(session),
    )


# ============================================================
# INT-G4D2-01：账户 A 持仓仅标记 A 的持股，不串到 B 的持股
# ============================================================
async def test_int_g4d2_01_is_holding_isolated_per_account(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    acc_a = await _seed_account(db_session)
    acc_b = await _seed_account(db_session)
    await _seed_position(db_session, acc_a.id, "AAAA01.SZ")
    await _seed_position(db_session, acc_b.id, "BBBB02.SZ")
    await _seed_market_state(repo)

    svc = _make_view_service(db_session)

    # 账户 A 视角：仅 AAAA01 标持仓
    dicts_a = [_signal_dict("AAAA01.SZ"), _signal_dict("BBBB02.SZ")]
    await svc.apply_account_overlay(dicts_a, acc_a.id)
    by_a = {d["ts_code"]: d for d in dicts_a}
    assert by_a["AAAA01.SZ"]["is_holding"] is True
    assert by_a["BBBB02.SZ"]["is_holding"] is False

    # 账户 B 视角：仅 BBBB02 标持仓（同一批共享信号，隔离叠加）
    dicts_b = [_signal_dict("AAAA01.SZ"), _signal_dict("BBBB02.SZ")]
    await svc.apply_account_overlay(dicts_b, acc_b.id)
    by_b = {d["ts_code"]: d for d in dicts_b}
    assert by_b["AAAA01.SZ"]["is_holding"] is False
    assert by_b["BBBB02.SZ"]["is_holding"] is True


# ============================================================
# INT-G4D2-02：BUY 信号叠加 suggested_pct（PositionSizer 真实计算）
# ============================================================
async def test_int_g4d2_02_buy_suggested_pct_filled(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    acc = await _seed_account(db_session)  # 无持仓 → 充足可用仓位
    await _seed_market_state(repo)

    svc = _make_view_service(db_session)
    dicts = [_signal_dict("CCCC03.SZ", "BUY")]
    await svc.apply_account_overlay(dicts, acc.id)

    assert dicts[0]["suggested_pct"] is not None
    assert dicts[0]["suggested_pct"] > 0


# ============================================================
# INT-G4D2-03：SELL 信号不 sizing → suggested_pct 保持 None
# ============================================================
async def test_int_g4d2_03_sell_not_sized(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    acc = await _seed_account(db_session)
    await _seed_position(db_session, acc.id, "DDDD04.SZ")
    await _seed_market_state(repo)

    svc = _make_view_service(db_session)
    dicts = [_signal_dict("DDDD04.SZ", "SELL")]
    await svc.apply_account_overlay(dicts, acc.id)

    assert dicts[0]["suggested_pct"] is None
    assert dicts[0]["is_holding"] is True
