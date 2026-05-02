"""INT-SVC-01~05: SignalService 集成测试（需真实 PostgreSQL）。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.signal import TradeSignal
from quantpilot.services.signal_service import SignalService

# ---------------------------------------------------------------------------
# 测试常量
# ---------------------------------------------------------------------------
_TRADE_DATE = date(2026, 4, 8)
_TS_CODE_A = "INTSVC1.SZ"
_TS_CODE_B = "INTSVC2.SZ"


def _make_buy_signal(ts_code: str, score: float = 85.0) -> TradeSignal:
    return TradeSignal(
        ts_code=ts_code,
        signal_type="BUY",
        trade_date=_TRADE_DATE,
        score=score,
        suggested_pct=0.10,
        suggested_price_low=9.90,
        suggested_price_high=10.20,
        stop_loss_price=9.57,
        signal_strength="MODERATE",
        t1_warning="A股T+1制度：买入当日不可卖出",
        reason="综合评分满足",
    )


def _make_sell_signal(ts_code: str) -> TradeSignal:
    return TradeSignal(
        ts_code=ts_code,
        signal_type="SELL",
        trade_date=_TRADE_DATE,
        score=35.0,
        reason="评分低于卖出阈值",
    )


# ---------------------------------------------------------------------------
# INT-SVC-01: save() 写入 2 条信号 → get_today_signals() 返回 2 条
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_svc_01_save_and_query(db_session: AsyncSession) -> None:
    """INT-SVC-01: save() 写入 2 条信号 → get_today_signals() 正确返回"""
    repo = MarketDataRepository(db_session)
    svc = SignalService(repo)

    signals = [_make_buy_signal(_TS_CODE_A), _make_sell_signal(_TS_CODE_B)]
    count = await svc.save(signals, _TRADE_DATE)
    assert count == 2

    result = await svc.get_today_signals(_TRADE_DATE)
    codes = {s.ts_code for s in result}
    assert _TS_CODE_A in codes
    assert _TS_CODE_B in codes


# ---------------------------------------------------------------------------
# INT-SVC-02: save() 重复写同一信号（upsert）→ 不报错，仍只有 1 条
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_svc_02_save_idempotent(db_session: AsyncSession) -> None:
    """INT-SVC-02: 同一 (ts_code, trade_date, signal_type) 两次 save → 幂等，只有 1 条"""
    repo = MarketDataRepository(db_session)
    svc = SignalService(repo)

    sig = _make_buy_signal(_TS_CODE_A, score=85.0)
    await svc.save([sig], _TRADE_DATE)

    # 再次保存同一信号（score 略有变化）
    sig2 = _make_buy_signal(_TS_CODE_A, score=87.0)
    await svc.save([sig2], _TRADE_DATE)

    result = await svc.get_today_signals(_TRADE_DATE, signal_type="BUY")
    a_signals = [s for s in result if s.ts_code == _TS_CODE_A]
    # 幂等：只有 1 条，且 score 被更新为最新值
    assert len(a_signals) == 1
    assert float(a_signals[0].score) == pytest.approx(87.0)


# ---------------------------------------------------------------------------
# INT-SVC-03: expire_old_signals(as_of_date, ttl=3) → 3 日前的 NEW → EXPIRED
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_svc_03_expire_old_signals(db_session: AsyncSession) -> None:
    """INT-SVC-03: trade_date 为 3 日前的 NEW 信号 → expire_old_signals 后变为 EXPIRED"""
    repo = MarketDataRepository(db_session)
    svc = SignalService(repo)

    old_date = _TRADE_DATE - timedelta(days=4)  # 4 天前（超过 ttl=3）
    old_sig = TradeSignal(
        ts_code=_TS_CODE_A,
        signal_type="BUY",
        trade_date=old_date,
        score=82.0,
        reason="旧信号",
    )
    await svc.save([old_sig], old_date)

    # 确认写入为 NEW
    before = await svc.get_today_signals(old_date, signal_type="BUY")
    assert any(s.ts_code == _TS_CODE_A and s.status == "NEW" for s in before)

    # 执行过期扫描
    expired_count = await svc.expire_old_signals(as_of_date=_TRADE_DATE, ttl_days=3)
    assert expired_count >= 1

    # 验证状态已变为 EXPIRED
    after = await svc.get_today_signals(old_date, signal_type="BUY")
    a_signals = [s for s in after if s.ts_code == _TS_CODE_A]
    assert len(a_signals) == 1
    assert a_signals[0].status == "EXPIRED"


# ---------------------------------------------------------------------------
# INT-SVC-04: get_lineage(signal_id) → 返回信号及其 SignalScoreSnapshot
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_svc_04_get_lineage(db_session: AsyncSession) -> None:
    """INT-SVC-04: 写入信号和快照 → get_lineage 正确返回两者"""
    repo = MarketDataRepository(db_session)
    svc = SignalService(repo)

    # 写入信号
    sig = _make_buy_signal(_TS_CODE_A, score=91.0)
    await svc.save([sig], _TRADE_DATE)

    # 查询信号 ID
    signals = await svc.get_today_signals(_TRADE_DATE, signal_type="BUY")
    a_sigs = [s for s in signals if s.ts_code == _TS_CODE_A]
    assert len(a_sigs) == 1
    signal_id = a_sigs[0].id

    # 手动写入快照（Phase 5 的 save() 尚未完整实现快照写入，此处直接用 repo）
    await repo.upsert_signal_snapshots([{
        "signal_id": signal_id,
        "trade_date": _TRADE_DATE,
        "ts_code": _TS_CODE_A,
        "composite_score": 91.0,
        "trend_score": 88.0,
        "reversion_score": 75.0,
        "momentum_score": 92.0,
        "value_score": 85.0,
        "market_state": "UPTREND",
        "score_breakdown": {"trend": {"score": 88.0, "weight": 0.4}},
        "raw_factors": {"ma_alignment": 1.0},
    }])

    # 验证 get_lineage
    result_sig, snapshot = await svc.get_lineage(signal_id)
    assert result_sig.ts_code == _TS_CODE_A
    assert snapshot is not None
    assert float(snapshot.composite_score) == pytest.approx(91.0)
    assert snapshot.market_state == "UPTREND"


# ---------------------------------------------------------------------------
# INT-SVC-05: update_status(ACTED) → 状态变更持久化
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_svc_05_update_status(db_session: AsyncSession) -> None:
    """INT-SVC-05: update_status(NEW→ACTED) → get_lineage 状态为 ACTED"""
    repo = MarketDataRepository(db_session)
    svc = SignalService(repo)

    # 写入信号
    sig = _make_buy_signal(_TS_CODE_B, score=83.0)
    await svc.save([sig], _TRADE_DATE)

    # 查询 ID
    signals = await svc.get_today_signals(_TRADE_DATE, signal_type="BUY")
    b_sigs = [s for s in signals if s.ts_code == _TS_CODE_B]
    assert len(b_sigs) == 1
    signal_id = b_sigs[0].id

    # 更新状态：NEW → ACTED
    updated = await svc.update_status(signal_id, "ACTED")
    assert updated.status == "ACTED"

    # 再次查询验证持久化
    fetched, _ = await svc.get_lineage(signal_id)
    assert fetched.status == "ACTED"

    # 验证非法转换抛出 ValueError
    with pytest.raises(ValueError, match="非法状态转换"):
        await svc.update_status(signal_id, "NEW")
