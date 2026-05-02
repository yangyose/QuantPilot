"""LineageService V1.0：信号数据血缘查询（Phase 7）。

V1.0 最小实现：信号-快照绑定 + PipelineRun 关联。
V1.5 计划：完整因子级溯源（SDD §15.6）。
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.business import Signal, SignalScoreSnapshot
from quantpilot.models.system import PipelineRun

logger = logging.getLogger(__name__)


class LineageService:
    """信号数据血缘查询服务。

    V1.0：返回信号 + 评分快照 + 当日流水线运行摘要。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_signal_lineage(self, signal_id: int) -> dict | None:
        """返回信号的数据血缘摘要。signal_id 不存在 → None。

        返回结构：
        {
          "signal_id": int,
          "trade_date": str,
          "score_snapshot": {
              "ts_code": str,
              "composite_score": float | None,
              "market_state": str | None,
              "score_breakdown": dict | None,
          } | None,
          "pipeline_run": {
              "trade_date": str,
              "cp1_at": str | None,
              "cp2_at": str | None,
              "cp3_at": str | None,
              "data_snapshot_version": str | None,
          } | None,
        }
        """
        # 1. 取信号
        sig_result = await self._session.execute(
            select(Signal).where(Signal.id == signal_id)
        )
        signal: Signal | None = sig_result.scalar_one_or_none()
        if signal is None:
            return None

        trade_date: date = signal.signal_date

        # 2. 取评分快照
        snap_result = await self._session.execute(
            select(SignalScoreSnapshot).where(SignalScoreSnapshot.signal_id == signal_id)
        )
        snapshot: SignalScoreSnapshot | None = snap_result.scalar_one_or_none()

        score_snapshot: dict | None = None
        if snapshot is not None:
            score_snapshot = {
                "ts_code": signal.ts_code,
                "composite_score": (
                    float(snapshot.composite_score)
                    if snapshot.composite_score is not None
                    else None
                ),
                "market_state": snapshot.market_state,
                "score_breakdown": snapshot.score_breakdown,
            }

        # 3. 取当日 PipelineRun（可能不存在，V1.0 best-effort）
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
