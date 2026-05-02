from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEngine, MarketStateEnum, MarketStateRecord

if TYPE_CHECKING:
    from quantpilot.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


def _orm_to_record(row: object) -> MarketStateRecord:
    """将 MarketStateHistory ORM 对象转换为 MarketStateRecord dataclass。
    trend_strength/adx_value/ma20/ma60 在 Phase 3 写入流程中由引擎保证非空，直接转换。
    """
    return MarketStateRecord(
        trade_date=row.trade_date,
        market_state=MarketStateEnum(row.market_state),
        trend_strength=float(row.trend_strength),
        adx_value=float(row.adx_value),
        ma20=float(row.ma20),
        ma60=float(row.ma60),
        state_changed=bool(row.state_changed),
        description=row.description or "",
    )


class MarketStateService:
    def __init__(
        self,
        engine: MarketStateEngine,
        repo: MarketDataRepository,
        index_code: str = "000300.SH",
        history_days: int = 100,
        *,
        notifier: "NotificationService | None" = None,
    ) -> None:
        self._engine = engine
        self._repo = repo
        self._index_code = index_code
        self._history_days = history_days
        # Phase 10 §5.4：注入后，状态切换时 best-effort 推送 MARKET_STATE 通知
        self._notifier = notifier

    async def identify_and_save(self, trade_date: date) -> MarketStateRecord | None:
        """
        1. 从 index_history 取最近 history_days 天的 OHLCV（到 trade_date 为止）
        2. 取 OHLCV 窗口第一天之前的已确认状态作为 prev_confirmed
        3. engine.identify_latest(ohlcv, prev_confirmed)
        4. 若结果不为 None：upsert_market_state(record)
        5. 返回 record（或 None 表示数据不足）
        """
        end_date = trade_date
        # 乘以 1.5 倍系数将交易日数转换为日历天数，确保节假日密集月份也能覆盖
        # 足够的交易日（100 交易日 × 1.5 ≈ 150 日历天，约 107 交易日，留足余量）
        calendar_days = int(self._history_days * 1.5)
        start_date = trade_date - timedelta(days=calendar_days)

        ohlcv_df = await self._repo.get_index_history(
            self._index_code, start_date, end_date
        )

        if ohlcv_df.empty:
            logger.warning(
                "market_state_no_data",
                extra={"trade_date": str(trade_date), "index_code": self._index_code},
            )
            return None

        # 设置 trade_date 为 index，仅保留 high/low/close；强制转 float（DB 返回 Decimal）
        ohlcv_df = ohlcv_df.set_index("trade_date")[["high", "low", "close"]].astype(float)

        # 获取 OHLCV 窗口第一天之前的已确认状态
        # index[0] 可能是 pd.Timestamp（合成数据）或 datetime.date（DB 数据），统一转换
        first_ohlcv_date = pd.Timestamp(ohlcv_df.index[0]).date()
        prev_row = await self._repo.get_latest_market_state(before_date=first_ohlcv_date)
        prev_confirmed = (
            MarketStateEnum(prev_row.market_state) if prev_row is not None
            else MarketStateEnum.OSCILLATION
        )

        record = self._engine.identify_latest(ohlcv_df, prev_confirmed=prev_confirmed)

        if record is None:
            logger.warning(
                "market_state_insufficient_data",
                extra={"trade_date": str(trade_date), "rows": len(ohlcv_df)},
            )
            return None

        await self._repo.upsert_market_state(record)

        # Phase 10 §5.4：状态切换 → best-effort 推送通知；失败只记 WARN 不阻断主流程
        if record.state_changed and self._notifier is not None:
            old_state = prev_confirmed.value
            new_state = record.market_state.value
            if old_state != new_state:
                try:
                    await self._notifier.notify_market_state_change(
                        old_state=old_state,
                        new_state=new_state,
                        trade_date=str(trade_date),
                    )
                except Exception:
                    logger.warning(
                        "notify_market_state_change_failed: trade_date=%s %s→%s",
                        trade_date, old_state, new_state, exc_info=True,
                    )
        return record

    async def get_current_state(self) -> MarketStateRecord | None:
        """从 DB 取最新状态行，转换为 MarketStateRecord 返回。无记录返回 None。"""
        row = await self._repo.get_latest_market_state()
        if row is None:
            return None
        return _orm_to_record(row)

    async def get_state_history(
        self, start_date: date, end_date: date
    ) -> list[MarketStateRecord]:
        """从 DB 取指定范围历史，返回 list[MarketStateRecord]（升序）。"""
        rows = await self._repo.get_market_state_history(start_date, end_date)
        return [_orm_to_record(r) for r in rows]
