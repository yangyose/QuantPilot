"""Phase 11 §5 SignalService.generate_for_date 端到端集成测试 INT-P11-SG-01~04。

覆盖：
- 01: pct_below_buy — candidate_pool 含 composite_pct_in_market → 走分位主路径，
  Signal 行写入 composite_z / composite_pct_in_market / trigger_reason
- 02: pct_above_sell — 持仓 + 高 pct → SELL trigger_reason=pct_above_sell
- 03: 旧 pool 无新列 → 自动 fallback V1.0-r5；新字段写 None
- 04: hard_stop_loss 是账户私有信号 — V1.5-G G-4d-1 解耦后管线不再产出（移 G-4d-2 API 期）
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.models.account import Account, Position
from quantpilot.models.business import Signal
from quantpilot.models.market import DailyQuote, StockInfo
from quantpilot.services.account_service import AccountService
from quantpilot.services.config_service import ConfigService
from quantpilot.services.signal_service import SignalService
from tests.integration._helpers import seeded_user_id

_TRADE_DATE = date(2026, 4, 8)


async def _seed_stock(
    session: AsyncSession,
    ts_code: str,
    *,
    industry: str = "银行",
    close: float = 10.0,
    is_suspended: bool = False,
    limit_up: bool = False,
) -> None:
    session.add(StockInfo(
        ts_code=ts_code, name=f"Test_{ts_code}",
        sw_industry_l1=industry, market="MAIN",
        list_date=date(2020, 1, 1), is_active=True,
    ))
    session.add(DailyQuote(
        ts_code=ts_code, trade_date=_TRADE_DATE,
        open=close, high=close * 1.01, low=close * 0.99,
        close=close, pre_close=close, pct_chg=0.0,
        vol=1_000_000, amount=10_000_000.0,
        adj_factor=1.0, is_suspended=is_suspended, is_st=False,
        limit_up=limit_up, limit_down=False,
    ))
    await session.flush()


async def _seed_pool_entry_with_phase11(
    repo: MarketDataRepository,
    ts_code: str,
    composite_score: float,
    composite_z: float,
    composite_pct: float,
    *,
    weights_source: str = "icir",
    market_state: str = "OSCILLATION",
    is_holding: bool = False,
) -> None:
    """写入含 Phase 11 新列的 candidate_pool 行（走 upsert_candidate_pool_bulk）。"""
    rows = [{
        "ts_code": ts_code,
        "trade_date": _TRADE_DATE,
        "composite_score": composite_score,
        "trend_score": composite_score,
        "momentum_score": composite_score,
        "reversion_score": composite_score,
        "value_score": composite_score,
        "market_state": market_state,
        "in_pool": True,
        "is_holding": is_holding,
        "composite_z": composite_z,
        "composite_pct_in_market": composite_pct,
        "weights_source": weights_source,
        "hysteresis_status": "stable",
        "score_breakdown_raw": {
            "trend": {"z_raw": 1.0, "weight": 0.5, "contribution": 0.5},
        },
        "score_breakdown_residual": {
            "trend": {"z_orthogonal_normalized": 1.0, "weight": 0.5, "contribution": 0.5},
        },
    }]
    await repo.upsert_candidate_pool_bulk(rows)


async def _seed_pool_entry_legacy(
    repo: MarketDataRepository,
    ts_code: str,
    composite_score: float,
    *,
    market_state: str = "OSCILLATION",
    is_holding: bool = False,
) -> None:
    """旧 candidate_pool 行（无 Phase 11 新列）— Phase 11 上线前 baseline 行。"""
    await repo.upsert_candidate_pool(
        ts_code=ts_code, trade_date=_TRADE_DATE,
        composite_score=composite_score,
        trend_score=composite_score, momentum_score=composite_score,
        reversion_score=composite_score, value_score=composite_score,
        market_state=market_state, in_pool=True, is_holding=is_holding,
    )


async def _seed_account(session: AsyncSession, cash: float = 1_000_000.0) -> Account:
    acc = await session.get(Account, 1)
    if acc is None:
        acc = Account(
            id=1, user_id=await seeded_user_id(session),
            name="测试账户", account_type="REAL", broker="MOCK",
            total_assets=cash, cash=cash,
        )
        session.add(acc)
    else:
        acc.total_assets = cash
        acc.cash = cash
    await session.flush()
    return acc


async def _seed_market_state(
    repo: MarketDataRepository, state: MarketStateEnum = MarketStateEnum.OSCILLATION,
) -> None:
    await repo.upsert_market_state(MarketStateRecord(
        trade_date=_TRADE_DATE, market_state=state,
        trend_strength=20.0, adx_value=20.0,
        ma20=10.0, ma60=10.0, state_changed=False, description="seed",
    ))


# ============================================================
# INT-P11-SG-01：pct_below_buy 端到端（分位主路径写入 Signal 新列）
# ============================================================
async def test_int_p11_sg_01_pct_below_buy_end_to_end(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    acc_svc = AccountService(db_session)
    sig_svc = SignalService(repo, account_service=acc_svc, config_service=cfg_svc)

    await _seed_stock(db_session, "P11SG01.SH")
    # pct=0.005 → STRONG（≤ 0.01）
    await _seed_pool_entry_with_phase11(
        repo, "P11SG01.SH",
        composite_score=70.0, composite_z=2.5, composite_pct=0.005,
        weights_source="icir",
    )
    await _seed_account(db_session)
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)
    assert len(saved) == 1
    sig = saved[0]
    assert sig.signal_type == "BUY"
    assert sig.trigger_reason == "pct_below_buy"
    assert sig.composite_z is not None
    assert abs(float(sig.composite_z) - 2.5) < 1e-3
    assert sig.composite_pct_in_market is not None
    assert abs(float(sig.composite_pct_in_market) - 0.005) < 1e-4
    assert sig.signal_strength == "STRONG"


# ============================================================
# INT-P11-SG-02：pct_above_sell — 持仓 + 高 pct → SELL trigger_reason 写入
# ============================================================
async def test_int_p11_sg_02_pct_above_sell_end_to_end(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    acc_svc = AccountService(db_session)
    sig_svc = SignalService(repo, account_service=acc_svc, config_service=cfg_svc)

    await _seed_stock(db_session, "P11SG02.SH")
    # pct=0.80 ≥ sell 阈值 0.70 → SELL pct_above_sell
    await _seed_pool_entry_with_phase11(
        repo, "P11SG02.SH",
        composite_score=30.0, composite_z=-0.8, composite_pct=0.80,
        is_holding=True,
    )
    acc = await _seed_account(db_session)
    # 持仓占总资产 5% 浮盈 2% — 不触发 hard_stop_loss
    db_session.add(Position(
        account_id=acc.id, ts_code="P11SG02.SH",
        shares=5000, cost_price=9.8,
    ))
    await db_session.flush()
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)
    sells = [s for s in saved if s.signal_type == "SELL"]
    assert len(sells) == 1
    assert sells[0].trigger_reason == "pct_above_sell"
    assert sells[0].composite_pct_in_market is not None
    assert abs(float(sells[0].composite_pct_in_market) - 0.80) < 1e-4


# ============================================================
# INT-P11-SG-03：旧 pool 行（无新列）→ 自动 fallback V1.0-r5
# ============================================================
async def test_int_p11_sg_03_legacy_pool_falls_back(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    acc_svc = AccountService(db_session)
    sig_svc = SignalService(repo, account_service=acc_svc, config_service=cfg_svc)

    await _seed_stock(db_session, "P11SG03.SH")
    # 旧 pool 行：仅 composite_score=92.0，无 composite_z/pct
    await _seed_pool_entry_legacy(repo, "P11SG03.SH", composite_score=92.0)
    await _seed_account(db_session)
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)
    assert len(saved) == 1
    sig = saved[0]
    assert sig.signal_type == "BUY"
    # 走旧路径：composite_z/pct 为 None
    assert sig.composite_z is None
    assert sig.composite_pct_in_market is None
    # trigger_reason 仍统一标记
    assert sig.trigger_reason == "pct_below_buy"


# ============================================================
# INT-P11-SG-04：hard_stop_loss 是账户私有信号 → V1.5-G G-4d-1 解耦后管线**不再产出**
# （持仓浮亏依赖用户成本价，移 API 请求期按用户账户叠加 G-4d-2 SignalViewService）
# ============================================================
async def test_int_p11_sg_04_hard_stop_loss_not_in_pipeline(db_session: AsyncSession) -> None:
    """G-4d-1 解耦：持仓浮亏 -10% 但 pct 在中性区 → 管线不读账户 → 无任何信号。

    hard_stop_loss 是账户私有 SELL（依赖用户成本价），其覆盖移至 G-4d-2
    SignalViewService API 请求期叠加的测试套件（届时验证同一持仓 + 浮亏 → 私有 SELL）。
    """
    repo = MarketDataRepository(db_session)
    cfg_svc = ConfigService(db_session)
    sig_svc = SignalService(repo, config_service=cfg_svc)  # 无 account_service

    await _seed_stock(db_session, "P11SG04.SH", close=10.0)
    # pct=0.30 在中性区（未跌出 SELL 阈值 0.70、未进 BUY 区）→ 管线本无信号；
    # 持仓浮亏 -10% 在 pre-解耦会触发 hard_stop_loss SELL，解耦后管线看不到账户 → 无 SELL。
    await _seed_pool_entry_with_phase11(
        repo, "P11SG04.SH",
        composite_score=55.0, composite_z=0.5, composite_pct=0.30,
        is_holding=True,
    )
    acc = await _seed_account(db_session)
    db_session.add(Position(
        account_id=acc.id, ts_code="P11SG04.SH",
        shares=5000, cost_price=11.2, current_price=10.0,
        market_value=50000.0, pnl_pct=-0.107,  # 浮亏 10.7% > 8%
    ))
    await db_session.flush()
    await _seed_market_state(repo)

    saved = await sig_svc.generate_for_date(_TRADE_DATE)
    # 解耦后管线不读账户 → 不产 hard_stop_loss（账户私有 SELL 移 G-4d-2 API 期）
    sells = [s for s in saved if s.signal_type == "SELL"]
    assert sells == []


# ============================================================
# 全套确认：Signal 行 trigger_reason 命中正确 enum 字符串
# ============================================================
async def test_int_p11_sg_05_trigger_reason_enum_values(db_session: AsyncSession) -> None:
    """trigger_reason 写入的字符串集合必须属于设计文档 §5 定义的 5 类。"""
    valid = {
        "pct_below_buy", "pct_above_sell", "hard_stop_loss",
        "short_term_z_drop", "mid_term_icir_flip",
    }
    stmt = select(Signal.trigger_reason).where(Signal.trigger_reason.isnot(None))
    rows = (await db_session.execute(stmt)).all()
    for (val,) in rows:
        assert val in valid, f"trigger_reason {val!r} 不在合法集合"
