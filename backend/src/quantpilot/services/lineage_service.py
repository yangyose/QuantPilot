"""LineageService：信号数据血缘查询（Phase 7 起；Phase 12 P12-A 三层稳定化）。

Phase 12 §3.1.2 重构：
- 去除 Phase 11 P1-7 临时 `getattr(snapshot, X, None)` fallback，统一直读 ORM 字段；
- `score_breakdown_raw` / `score_breakdown_residual` / `weights_source` /
  `hysteresis_status` 实际在 `candidate_pool` 表，从同日同 ts_code 行 join 读取；
- 返回 dict 形状对齐 `SignalLineageResponse`（19 字段 score_snapshot + pipeline_run）。

NULL 与 missing 严格区分：snapshot 不存在 → `score_snapshot=None`；snapshot 存在
但 5 步管线产物未落库（v1.1 commit 之前历史信号）→ `score_snapshot` 是 dict 但
factor_* 字段为 None。
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.business import CandidatePool, Signal, SignalScoreSnapshot
from quantpilot.models.system import PipelineRun

logger = logging.getLogger(__name__)


class LineageService:
    """信号数据血缘查询服务（Phase 12 三层 schema 稳定化）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_signal_lineage(self, signal_id: int) -> dict | None:
        """返回信号的数据血缘摘要。signal_id 不存在 → None。

        返回 dict 与 `quantpilot.schemas.signals.SignalLineageResponse` 一一对应：
        {
          "signal_id": int,
          "trade_date": str,
          "score_snapshot": ScoreSnapshotLineage(19 字段) | None,
          "pipeline_run": PipelineRunLineage(5 字段) | None,
        }
        """
        # 1. 取信号
        sig_result = await self._session.execute(
            select(Signal).where(Signal.id == signal_id)
        )
        signal: Signal | None = sig_result.scalar_one_or_none()
        if signal is None:
            return None

        trade_date: date = signal.trade_date

        # 2. 取评分快照（snapshot 不存在 ≠ snapshot 存在但字段 NULL）
        snap_result = await self._session.execute(
            select(SignalScoreSnapshot).where(SignalScoreSnapshot.signal_id == signal_id)
        )
        snapshot: SignalScoreSnapshot | None = snap_result.scalar_one_or_none()

        score_snapshot: dict | None = None
        if snapshot is not None:
            # 3. candidate_pool 同日同 ts_code 行：补 L2 审计字段（weights_source /
            # hysteresis_status）+ L3 breakdown_raw / breakdown_residual。
            # 注：snapshot 存在但 pool 不存在 → L2/L3 这 4 字段为 None；snapshot 不
            # 存在则整对象返回 null（不从 pool 虚构 snapshot，避免数据不自洽）。
            pool_result = await self._session.execute(
                select(CandidatePool).where(
                    CandidatePool.trade_date == trade_date,
                    CandidatePool.ts_code == signal.ts_code,
                )
            )
            pool_row: CandidatePool | None = pool_result.scalar_one_or_none()

            score_snapshot = {
                # 标识
                "ts_code": signal.ts_code,
                # L1 业务可解释（5）
                "composite_score": (
                    float(snapshot.composite_score)
                    if snapshot.composite_score is not None else None
                ),
                "composite_z": (
                    float(signal.composite_z)
                    if signal.composite_z is not None else None
                ),
                "composite_pct_in_market": (
                    float(signal.composite_pct_in_market)
                    if signal.composite_pct_in_market is not None else None
                ),
                "market_state": snapshot.market_state,
                "trigger_reason": signal.trigger_reason,
                # L2 策略分数（4）
                "trend_score": (
                    float(snapshot.trend_score)
                    if snapshot.trend_score is not None else None
                ),
                "momentum_score": (
                    float(snapshot.momentum_score)
                    if snapshot.momentum_score is not None else None
                ),
                "reversion_score": (
                    float(snapshot.reversion_score)
                    if snapshot.reversion_score is not None else None
                ),
                "value_score": (
                    float(snapshot.value_score)
                    if snapshot.value_score is not None else None
                ),
                # L2 审计（2）— 来自 candidate_pool
                "weights_source": pool_row.weights_source if pool_row else None,
                "hysteresis_status": pool_row.hysteresis_status if pool_row else None,
                # L2 JSONB（3）
                "score_breakdown": snapshot.score_breakdown,
                "factor_winsorized": snapshot.factor_winsorized,
                "factor_neutralized": snapshot.factor_neutralized,
                # L3（4）
                "raw_factors": snapshot.raw_factors,
                "factor_orthogonal": snapshot.factor_orthogonal,
                "score_breakdown_raw": pool_row.score_breakdown_raw if pool_row else None,
                "score_breakdown_residual": (
                    pool_row.score_breakdown_residual if pool_row else None
                ),
            }

        # 4. 取当日 PipelineRun（best-effort：可能不存在）
        run_result = await self._session.execute(
            select(PipelineRun).where(PipelineRun.trade_date == trade_date)
        )
        run: PipelineRun | None = run_result.scalar_one_or_none()

        pipeline_run: dict | None = None
        if run is not None:
            pipeline_run = {
                "trade_date": str(run.trade_date),
                "cp1_at": run.cp1_at.isoformat() if run.cp1_at else None,
                "cp2_at": run.cp2_at.isoformat() if run.cp2_at else None,
                "cp3_at": run.cp3_at.isoformat() if run.cp3_at else None,
                "data_snapshot_version": run.data_snapshot_version,
            }

        logger.debug("lineage_fetched: signal_id=%d trade_date=%s", signal_id, trade_date)
        return {
            "signal_id": signal_id,
            "trade_date": str(trade_date),
            "score_snapshot": score_snapshot,
            "pipeline_run": pipeline_run,
        }
