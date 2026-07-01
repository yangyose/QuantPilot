"""ReportService：周报/月报/自定义报告生成与查询（Phase 7）。"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.factor_ic_repository import (
    AGNOSTIC_STATE,
    MONTHLY_QUALITY_ROW_TYPE,
)
from quantpilot.models.business import FactorICWindowState, Report

logger = logging.getLogger(__name__)


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ 生成

    async def generate_weekly(self, week_end: date, account_id: int) -> Report:
        """生成周报。week_end 为周五（自然周结束日）。

        account_id：报告归属账户（G-3 账户层隔离）。成交按此账户过滤；信号为共享层。

        数据结构：
        - trade_summary：本周成交数量（从 trade_record 统计）
        - new_signals：本周新生成信号数
        若无交易日则 summary="本周无交易日"。
        """
        from datetime import timedelta

        from quantpilot.models.account import TradeRecord
        from quantpilot.models.business import Signal

        week_start = week_end - timedelta(days=week_end.weekday())  # 本周一

        trade_count: int = (
            await self._session.execute(
                select(func.count()).where(
                    TradeRecord.account_id == account_id,
                    TradeRecord.trade_date >= week_start,
                    TradeRecord.trade_date <= week_end,
                )
            )
        ).scalar_one()

        buy_count: int = (
            await self._session.execute(
                select(func.count()).where(
                    TradeRecord.account_id == account_id,
                    TradeRecord.trade_date >= week_start,
                    TradeRecord.trade_date <= week_end,
                    TradeRecord.trade_type == "BUY",
                )
            )
        ).scalar_one()

        signal_count: int = (
            await self._session.execute(
                select(func.count()).where(
                    Signal.trade_date >= week_start,
                    Signal.trade_date <= week_end,
                )
            )
        ).scalar_one()

        content: dict = {
            "period": {"start": str(week_start), "end": str(week_end)},
            "trade_summary": {
                "total": trade_count,
                "buy": buy_count,
                "sell": trade_count - buy_count,
            },
            "new_signals": signal_count,
        }
        summary = (
            "本周无交易日"
            if trade_count == 0 and signal_count == 0
            else f"本周成交 {trade_count} 笔，新信号 {signal_count} 个"
        )

        return await self._insert_report(
            "WEEKLY", week_start, week_end, content, summary, account_id
        )

    async def generate_monthly(self, month_end: date, account_id: int) -> Report:
        """生成月报。month_end 为月末最后交易日。

        account_id：报告归属账户（G-3 账户层隔离）。成交/持仓按此账户过滤。

        数据结构：
        - trade_count：月内成交笔数
        - factor_alerts：当月因子告警（factor_ic_window_state row_type='monthly_quality'）
        - top_holdings：当前持仓股票列表
        V1.0 不含图表数据，仅输出结构化 JSON。
        """
        from quantpilot.models.account import Position, TradeRecord

        month_start = month_end.replace(day=1)

        trade_count: int = (
            await self._session.execute(
                select(func.count()).where(
                    TradeRecord.account_id == account_id,
                    TradeRecord.trade_date >= month_start,
                    TradeRecord.trade_date <= month_end,
                )
            )
        ).scalar_one()

        # Phase 15 §15-7：月度因子告警 repoint 到 factor_ic_window_state monthly_quality
        alerts_rows = await self._session.execute(
            select(
                FactorICWindowState.strategy,
                FactorICWindowState.factor,
                FactorICWindowState.alert_status,
            ).where(
                FactorICWindowState.row_type == MONTHLY_QUALITY_ROW_TYPE,
                FactorICWindowState.state == AGNOSTIC_STATE,
                FactorICWindowState.trade_date == month_end,
                FactorICWindowState.alert_status.isnot(None),
            )
        )
        factor_alerts = [
            {
                "strategy": row.strategy,
                "factor": row.factor,
                "alert": row.alert_status,
            }
            for row in alerts_rows
        ]

        pos_rows = await self._session.execute(
            select(Position.ts_code).where(
                Position.account_id == account_id, Position.shares > 0
            )
        )
        top_holdings = [row.ts_code for row in pos_rows]

        content: dict = {
            "period": {"start": str(month_start), "end": str(month_end)},
            "trade_count": trade_count,
            "factor_alerts": factor_alerts,
            "top_holdings": top_holdings,
        }
        summary = (
            f"月度成交 {trade_count} 笔，"
            f"因子告警 {len(factor_alerts)} 个，"
            f"当前持仓 {len(top_holdings)} 只"
        )

        return await self._insert_report(
            "MONTHLY", month_start, month_end, content, summary, account_id
        )

    async def generate_custom(self, start: date, end: date, account_id: int) -> Report:
        """用户触发的自定义时间段报告（含持仓快照、交易明细、信号统计、因子告警）。

        account_id：报告归属账户（G-3 账户层隔离）。成交/持仓按此账户过滤；信号为共享层。
        """
        from quantpilot.models.account import Position, TradeRecord
        from quantpilot.models.business import Signal

        # ── 交易统计 ────────────────────────────────────────────────────
        trade_rows = await self._session.execute(
            select(
                TradeRecord.ts_code,
                TradeRecord.trade_type,
                TradeRecord.trade_date,
                TradeRecord.price,
                TradeRecord.shares,
                TradeRecord.amount,
            ).where(
                TradeRecord.account_id == account_id,
                TradeRecord.trade_date >= start,
                TradeRecord.trade_date <= end,
            ).order_by(TradeRecord.trade_date)
        )
        trades = [
            {
                "ts_code": r.ts_code,
                "trade_type": r.trade_type,
                "trade_date": str(r.trade_date),
                "price": float(r.price) if r.price else None,
                "shares": r.shares,
                "amount": float(r.amount) if r.amount else None,
            }
            for r in trade_rows
        ]
        buy_amount  = sum(t["amount"] or 0 for t in trades if t["trade_type"] == "BUY")
        sell_amount = sum(t["amount"] or 0 for t in trades if t["trade_type"] == "SELL")

        # ── 信号统计 ────────────────────────────────────────────────────
        signal_rows = await self._session.execute(
            select(
                Signal.ts_code,
                Signal.signal_type,
                Signal.trade_date,
                Signal.score,
                Signal.status,
            ).where(
                Signal.trade_date >= start,
                Signal.trade_date <= end,
            ).order_by(Signal.trade_date)
        )
        signals = [
            {
                "ts_code": r.ts_code,
                "signal_type": r.signal_type,
                "trade_date": str(r.trade_date),
                "score": float(r.score) if r.score else None,
                "status": r.status,
            }
            for r in signal_rows
        ]
        acted_count  = sum(1 for s in signals if s["status"] == "ACTED")
        compliance   = round(acted_count / len(signals), 4) if signals else None

        # ── 持仓快照 ────────────────────────────────────────────────────
        pos_rows = await self._session.execute(
            select(
                Position.ts_code,
                Position.shares,
                Position.cost_price,
                Position.current_price,
                Position.market_value,
                Position.pnl_pct,
            ).where(Position.account_id == account_id, Position.shares > 0)
        )
        holdings = [
            {
                "ts_code": r.ts_code,
                "shares": r.shares,
                "cost_price": float(r.cost_price) if r.cost_price else None,
                "current_price": float(r.current_price) if r.current_price else None,
                "market_value": float(r.market_value) if r.market_value else None,
                "pnl_pct": float(r.pnl_pct) if r.pnl_pct else None,
            }
            for r in pos_rows
        ]

        # ── 因子告警 ────────────────────────────────────────────────────
        # Phase 15 §15-7：月度因子告警 repoint 到 factor_ic_window_state monthly_quality
        #（ic_mean_3m 复用列 ic_mean_state）
        alert_rows = await self._session.execute(
            select(
                FactorICWindowState.strategy,
                FactorICWindowState.factor,
                FactorICWindowState.ic_mean_state,
                FactorICWindowState.alert_status,
            ).where(
                FactorICWindowState.row_type == MONTHLY_QUALITY_ROW_TYPE,
                FactorICWindowState.state == AGNOSTIC_STATE,
                FactorICWindowState.trade_date >= start,
                FactorICWindowState.trade_date <= end,
                FactorICWindowState.alert_status.isnot(None),
            )
        )
        factor_alerts = [
            {
                "strategy": r.strategy,
                "factor": r.factor,
                "ic_mean_3m": float(r.ic_mean_state) if r.ic_mean_state else None,
                "alert": r.alert_status,
            }
            for r in alert_rows
        ]

        content: dict = {
            "period": {"start": str(start), "end": str(end)},
            "trade_summary": {
                "count": len(trades),
                "buy_count": sum(1 for t in trades if t["trade_type"] == "BUY"),
                "sell_count": sum(1 for t in trades if t["trade_type"] == "SELL"),
                "buy_amount": round(buy_amount, 2),
                "sell_amount": round(sell_amount, 2),
                "records": trades,
            },
            "signal_summary": {
                "count": len(signals),
                "acted_count": acted_count,
                "compliance_rate": compliance,
                "records": signals,
            },
            "holdings_snapshot": holdings,
            "factor_alerts": factor_alerts,
        }
        holding_pnl = [h["pnl_pct"] for h in holdings if h["pnl_pct"] is not None]
        avg_pnl = sum(holding_pnl) / len(holding_pnl) * 100 if holding_pnl else None
        avg_pnl_str = f"持仓均盈亏 {avg_pnl:.1f}%" if avg_pnl is not None else "持仓无盈亏数据"
        summary = (
            f"区间 {start}~{end}：成交 {len(trades)} 笔"
            f"（买入 ¥{buy_amount/10000:.1f}万 / 卖出 ¥{sell_amount/10000:.1f}万），"
            f"信号执行率 {compliance*100:.0f}%" if compliance else f"区间 {start}~{end}：无信号数据"
        )
        if holdings:
            summary += f"，当前 {len(holdings)} 只持仓，{avg_pnl_str}"
        if factor_alerts:
            summary += f"，{len(factor_alerts)} 个因子告警"

        return await self._insert_report("CUSTOM", start, end, content, summary, account_id)

    # ------------------------------------------------------------------ 查询

    async def get_list(
        self,
        account_id: int,
        report_type: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Report], int]:
        """查询历史报告列表（分页）。返回 (records, total_count)。

        account_id：仅返回归属当前用户账户的报告（G-3 账户层隔离）。
        """
        stmt = select(Report).where(Report.account_id == account_id)
        if report_type:
            stmt = stmt.where(Report.report_type == report_type)
        if start_date:
            stmt = stmt.where(Report.period_end >= start_date)
        if end_date:
            stmt = stmt.where(Report.period_start <= end_date)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(Report.generated_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_by_id(self, report_id: int, account_id: int) -> Report | None:
        """按 ID 获取报告详情。

        ownership（G-3 §5.2）：跨账户 report_id → 查无 → None（路由转 404，不泄露存在性）。
        """
        result = await self._session.execute(
            select(Report).where(
                Report.id == report_id, Report.account_id == account_id
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------ 内部

    async def _insert_report(
        self,
        report_type: str,
        period_start: date,
        period_end: date,
        content: dict,
        summary: str,
        account_id: int,
    ) -> Report:
        """写入新报告记录。"""
        report = Report(
            account_id=account_id,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            content=content,
            summary=summary,
            generated_at=datetime.now(tz=timezone.utc),
        )
        self._session.add(report)
        await self._session.flush()
        await self._session.refresh(report)
        logger.info(
            "report_generated: type=%s period=%s~%s id=%d",
            report_type, period_start, period_end, report.id,
        )
        return report
