"""INT-P12-A-01~03: LineageService 三层 schema 字段完整性集成测试（Phase 12 P12-A2）。

依据 phase12_factor_lineage.md §6.2 + §3.1.2：
- INT-P12-A-01: 完整 5 步管线产物 → 19 字段全非 None
- INT-P12-A-02: 手动信号（无 snapshot）→ score_snapshot=null
- INT-P12-A-03: snapshot 不存在 / pool 存在 → score_snapshot=null（不从 pool 补字段）
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.models.business import (
    CandidatePool,
    Signal,
    SignalScoreSnapshot,
)
from quantpilot.models.system import PipelineRun
from quantpilot.services.lineage_service import LineageService

_TRADE_DATE = date(2026, 5, 12)


async def _seed_signal(
    session: AsyncSession,
    ts_code: str,
    *,
    composite_z: float | None = 3.85,
    composite_pct: float | None = 0.0005,
    trigger_reason: str | None = "pct_below_buy",
) -> Signal:
    sig = Signal(
        ts_code=ts_code,
        signal_type="BUY",
        trade_date=_TRADE_DATE,
        score=99.87,
        suggested_pct=0.10,
        suggested_price_low=9.90,
        suggested_price_high=10.20,
        stop_loss_price=9.57,
        signal_strength="STRONG",
        liquidity_note=None,
        t1_warning="A股T+1制度：买入当日不可卖出",
        reason="综合评分 99.87",
        status="NEW",
        composite_z=composite_z,
        composite_pct_in_market=composite_pct,
        trigger_reason=trigger_reason,
    )
    session.add(sig)
    await session.flush()
    return sig


async def _seed_snapshot_full(session: AsyncSession, signal_id: int, ts_code: str) -> None:
    """Phase 11 5 步管线完整产物（factor_winsorized/neutralized/orthogonal 均非空）。"""
    session.add(SignalScoreSnapshot(
        signal_id=signal_id,
        trade_date=_TRADE_DATE,
        ts_code=ts_code,
        composite_score=99.87,
        trend_score=1.85,
        momentum_score=0.94,
        reversion_score=-0.21,
        value_score=1.12,
        market_state="UPTREND",
        score_breakdown={"trend": {"score": 1.85, "weight": 0.4}},
        raw_factors={"ma_diff": 0.92, "rsi": 65.0},
        factor_winsorized={"trend": {"ma_diff": 0.85}, "value": {"pe_ttm_inv": 0.55}},
        factor_neutralized={"trend": {"ma_diff": 0.72}, "value": {"pe_ttm_inv": 0.49}},
        factor_orthogonal={
            "trend": {"ma_diff_normalized": 0.65},
            "value": {"pe_ttm_inv_normalized": 0.41},
        },
    ))
    await session.flush()


async def _seed_pool_full(repo: MarketDataRepository, ts_code: str) -> None:
    """candidate_pool 同日同 ts_code 行（含 score_breakdown_raw/residual + weights_source）。"""
    rows = [{
        "ts_code": ts_code,
        "trade_date": _TRADE_DATE,
        "composite_score": 99.87,
        "trend_score": 99.0,
        "momentum_score": 80.0,
        "reversion_score": 45.0,
        "value_score": 88.0,
        "market_state": "UPTREND",
        "in_pool": True,
        "is_holding": False,
        "composite_z": 3.85,
        "composite_pct_in_market": 0.0005,
        "weights_source": "default_matrix",
        "hysteresis_status": "active",
        "score_breakdown_raw": {
            "trend": {"z_raw": 1.85, "weight": 0.4, "contribution": 0.74},
        },
        "score_breakdown_residual": {
            "trend": {"z_orthogonal_normalized": 0.65, "weight": 0.4, "contribution": 0.26},
        },
        "factor_winsorized": {"trend": {"ma_diff": 0.85}},
        "factor_neutralized": {"trend": {"ma_diff": 0.72}},
        "factor_orthogonal": {"trend": {"ma_diff_normalized": 0.65}},
    }]
    await repo.upsert_candidate_pool_bulk(rows)


async def _seed_pipeline_run(session: AsyncSession) -> None:
    session.add(PipelineRun(
        trade_date=_TRADE_DATE,
        status="SUCCESS",
        started_at=datetime(2026, 5, 12, 15, 25, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 5, 12, 15, 38, 0, tzinfo=timezone.utc),
        signal_count=1,
        cp1_data_ready=True,
        cp1_at=datetime(2026, 5, 12, 15, 30, 0, tzinfo=timezone.utc),
        cp2_scoring_done=True,
        cp2_at=datetime(2026, 5, 12, 15, 35, 12, tzinfo=timezone.utc),
        cp3_signals_done=True,
        cp3_at=datetime(2026, 5, 12, 15, 37, 48, tzinfo=timezone.utc),
        data_snapshot_version="abc12345",
    ))
    await session.flush()


# ===========================================================================
# INT-P12-A-01：完整 5 步管线产物 → 19 字段全非 None
# ===========================================================================
async def test_int_p12_a_01_lineage_full_fields(db_session: AsyncSession) -> None:
    """INT-P12-A-01: 完整 Phase 11 5 步管线产物 + pool 行 + PipelineRun → 19 字段非 None。"""
    repo = MarketDataRepository(db_session)
    svc = LineageService(db_session)

    ts_code = "P12A01.SH"
    sig = await _seed_signal(db_session, ts_code)
    await _seed_snapshot_full(db_session, sig.id, ts_code)
    await _seed_pool_full(repo, ts_code)
    await _seed_pipeline_run(db_session)

    result = await svc.get_signal_lineage(sig.id)
    assert result is not None
    assert result["signal_id"] == sig.id
    assert result["trade_date"] == str(_TRADE_DATE)

    snap = result["score_snapshot"]
    assert snap is not None
    # 标识
    assert snap["ts_code"] == ts_code
    # L1（5）
    assert snap["composite_score"] == 99.87
    assert snap["composite_z"] == 3.85
    assert snap["composite_pct_in_market"] == 0.0005
    assert snap["market_state"] == "UPTREND"
    assert snap["trigger_reason"] == "pct_below_buy"
    # L2 4 策略分（9 中的 4）
    assert snap["trend_score"] == 1.85
    assert snap["momentum_score"] == 0.94
    assert snap["reversion_score"] == -0.21
    assert snap["value_score"] == 1.12
    # L2 审计（9 中的 2）
    assert snap["weights_source"] == "default_matrix"
    assert snap["hysteresis_status"] == "active"
    # L2 JSONB（9 中的 3）
    assert snap["score_breakdown"] is not None
    assert snap["factor_winsorized"] is not None
    assert snap["factor_neutralized"] is not None
    # L3（4）
    assert snap["raw_factors"] is not None
    assert snap["factor_orthogonal"] is not None
    assert snap["score_breakdown_raw"] is not None
    assert snap["score_breakdown_residual"] is not None

    run = result["pipeline_run"]
    assert run is not None
    assert run["trade_date"] == str(_TRADE_DATE)
    assert run["cp1_at"] is not None
    assert run["cp2_at"] is not None
    assert run["cp3_at"] is not None
    assert run["data_snapshot_version"] == "abc12345"


# ===========================================================================
# INT-P12-A-02：手动信号 / 无 snapshot → score_snapshot=null
# ===========================================================================
async def test_int_p12_a_02_lineage_no_snapshot(db_session: AsyncSession) -> None:
    """INT-P12-A-02: 信号无对应 snapshot → score_snapshot=null。"""
    svc = LineageService(db_session)

    ts_code = "P12A02.SH"
    sig = await _seed_signal(
        db_session, ts_code,
        composite_z=None, composite_pct=None, trigger_reason=None,
    )

    result = await svc.get_signal_lineage(sig.id)
    assert result is not None
    assert result["signal_id"] == sig.id
    assert result["score_snapshot"] is None
    assert result["pipeline_run"] is None


# ===========================================================================
# INT-P12-A-03：snapshot 无 / pool 有 → score_snapshot=null（不从 pool 补字段）
# ===========================================================================
async def test_int_p12_a_03_lineage_pool_only_no_snapshot(db_session: AsyncSession) -> None:
    """INT-P12-A-03: candidate_pool 有同日同 ts_code 行但 snapshot 不存在 → score_snapshot=null。

    避免数据不自洽（pool 是当日 CP2 输出，snapshot 是 CP3 信号生成时持久化；
    pool 有但 snapshot 无表示当日未生成信号，那么所谓 lineage 不应虚构 snapshot）。
    """
    repo = MarketDataRepository(db_session)
    svc = LineageService(db_session)

    ts_code = "P12A03.SH"
    sig = await _seed_signal(db_session, ts_code)
    # 故意 _不_ 插 signal_score_snapshot
    await _seed_pool_full(repo, ts_code)

    result = await svc.get_signal_lineage(sig.id)
    assert result is not None
    assert result["signal_id"] == sig.id
    # 关键断言：snapshot 不存在 → score_snapshot=null，不从 pool 补
    assert result["score_snapshot"] is None
    # 同样核实 candidate_pool 行确实存在（不是测试失误）
    from sqlalchemy import select
    pool_row = (
        await db_session.execute(
            select(CandidatePool).where(
                CandidatePool.trade_date == _TRADE_DATE,
                CandidatePool.ts_code == ts_code,
            )
        )
    ).scalar_one_or_none()
    assert pool_row is not None
    assert pool_row.score_breakdown_raw is not None
