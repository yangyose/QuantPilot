"""PerformanceService：实盘绩效归因服务（Phase 8，SDD §12.1~12.4）。"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.account import Account, DailyPortfolioValue, FundFlow, TradeRecord
from quantpilot.models.business import SignalScoreSnapshot
from quantpilot.models.system import UserConfig

logger = logging.getLogger(__name__)


class PerformanceService:
    """实盘绩效归因（SDD §12.1~12.4）。读取 Phase 6/7 已有表，无新增表依赖。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # 辅助：获取账户 ID（取系统中 id 最小的账户）
    # ------------------------------------------------------------------

    async def _resolve_account_id(self, account_id: int | None) -> int | None:
        if account_id is not None:
            return account_id
        result = await self._session.execute(
            select(Account.id).order_by(Account.id).limit(1)
        )
        row = result.scalar_one_or_none()
        return row

    # ------------------------------------------------------------------
    # get_summary（SDD §12.1）
    # ------------------------------------------------------------------

    async def get_summary(self, account_id: int | None = None) -> dict | None:
        """7 项基础绩效指标（SDD §12.1）；账户无数据返回 None。"""

        aid = await self._resolve_account_id(account_id)
        if aid is None:
            return None

        # 净值曲线
        dpv_rows = (await self._session.execute(
            select(DailyPortfolioValue)
            .where(DailyPortfolioValue.account_id == aid)
            .order_by(DailyPortfolioValue.trade_date)
        )).scalars().all()

        if not dpv_rows:
            return None

        nav_values = [float(r.total_value) for r in dpv_rows]
        trade_dates = [r.trade_date for r in dpv_rows]
        first_val = nav_values[0]
        last_val = nav_values[-1]

        # net_invested（从资金流水中计算）
        ff_rows = (await self._session.execute(
            select(FundFlow).where(FundFlow.account_id == aid)
        )).scalars().all()
        net_invested = sum(
            float(r.amount) for r in ff_rows
            if r.flow_type in ("DEPOSIT", "WITHDRAW")
        )
        if net_invested <= 0:
            net_invested = first_val  # 降级：若无资金流水，使用首日总资产

        # 累计收益率
        cumulative_return = (last_val - net_invested) / net_invested if net_invested > 0 else 0.0

        # 年化收益率
        days = (trade_dates[-1] - trade_dates[0]).days if len(trade_dates) > 1 else 0
        if days > 0 and cumulative_return > -1:
            annualized_return = (1 + cumulative_return) ** (365 / days) - 1
        else:
            annualized_return = 0.0

        # 最大回撤
        max_drawdown = _calc_max_drawdown(nav_values)

        # 夏普比率（rf 从 user_config 读取，缺失时默认 0.03）
        rf = await self._get_risk_free_rate()
        sharpe = _calc_sharpe(nav_values, rf)

        # 胜率 / 盈亏比
        trade_rows = (await self._session.execute(
            select(TradeRecord).where(TradeRecord.account_id == aid)
        )).scalars().all()
        win_rate, profit_loss_ratio = _calc_win_rate_from_trades(trade_rows)

        # 基准收益（HS300 同期）
        benchmark_return = await self._get_benchmark_return(trade_dates[0], trade_dates[-1])

        return {
            "cumulative_return": round(cumulative_return, 6),
            "annualized_return": round(annualized_return, 6),
            "max_drawdown": round(max_drawdown, 6),
            "sharpe_ratio": round(sharpe, 6),
            "win_rate": win_rate,
            "profit_loss_ratio": profit_loss_ratio,
            "benchmark_return": benchmark_return,
        }

    async def _get_risk_free_rate(self) -> float:
        result = await self._session.execute(
            select(UserConfig.config_value).where(UserConfig.config_key == "risk_free_rate")
        )
        row = result.scalar_one_or_none()
        if row and isinstance(row, dict) and "value" in row:
            try:
                return float(row["value"])
            except (TypeError, ValueError):
                pass
        return 0.03

    async def _get_benchmark_return(self, start: date, end: date) -> float | None:
        """
        计算 HS300 区间收益。

        使用范围查询（>= start / <= end）而非精确匹配，避免区间端点为周末/节假日时
        无行情数据导致返回 None（C-02）。
        """
        from quantpilot.models.market import IndexHistory

        # 取 >= start 的第一个交易日收盘价
        start_row = (await self._session.execute(
            select(IndexHistory.close)
            .where(IndexHistory.index_code == "000300.SH")
            .where(IndexHistory.trade_date >= start)
            .order_by(IndexHistory.trade_date.asc())
            .limit(1)
        )).scalar_one_or_none()

        # 取 <= end 的最后一个交易日收盘价
        end_row = (await self._session.execute(
            select(IndexHistory.close)
            .where(IndexHistory.index_code == "000300.SH")
            .where(IndexHistory.trade_date <= end)
            .order_by(IndexHistory.trade_date.desc())
            .limit(1)
        )).scalar_one_or_none()

        if start_row is None or end_row is None:
            return None
        start_close = float(start_row)
        end_close = float(end_row)
        if start_close == 0:
            return None
        return round((end_close - start_close) / start_close, 6)

    # ------------------------------------------------------------------
    # get_history（净值曲线）
    # ------------------------------------------------------------------

    async def get_history(self, account_id: int | None = None, limit: int = 252) -> dict:
        """净值曲线历史 + HS300 基准序列。"""
        aid = await self._resolve_account_id(account_id)
        if aid is None:
            return {"nav_series": [], "benchmark_series": []}

        dpv_rows = (await self._session.execute(
            select(DailyPortfolioValue)
            .where(DailyPortfolioValue.account_id == aid)
            .order_by(DailyPortfolioValue.trade_date.desc())
            .limit(limit)
        )).scalars().all()
        dpv_rows = list(reversed(dpv_rows))

        if not dpv_rows:
            return {"nav_series": [], "benchmark_series": []}

        # 归一化净值（以第一日总资产为基准 = 1.0）
        base = float(dpv_rows[0].total_value) if dpv_rows[0].total_value else 1.0
        nav_series = [
            {
                "date": str(r.trade_date),
                "nav": round(float(r.total_value) / base, 6) if base > 0 else 1.0,
            }
            for r in dpv_rows
        ]

        # HS300 基准序列（同期）
        start_date = dpv_rows[0].trade_date
        end_date = dpv_rows[-1].trade_date
        benchmark_series = await self._get_benchmark_series(start_date, end_date)

        return {"nav_series": nav_series, "benchmark_series": benchmark_series}

    async def _get_benchmark_series(self, start: date, end: date) -> list[dict]:
        from quantpilot.models.market import IndexHistory
        result = await self._session.execute(
            select(IndexHistory)
            .where(IndexHistory.index_code == "000300.SH")
            .where(IndexHistory.trade_date >= start)
            .where(IndexHistory.trade_date <= end)
            .order_by(IndexHistory.trade_date)
        )
        rows = result.scalars().all()
        if not rows:
            return []
        base_close = float(rows[0].close) if rows[0].close else 1.0
        return [
            {
                "date": str(r.trade_date),
                "value": round(float(r.close) / base_close, 6) if base_close > 0 else 1.0,
            }
            for r in rows if r.close is not None
        ]

    # ------------------------------------------------------------------
    # get_attribution（SDD §12.2 三维归因）
    # ------------------------------------------------------------------

    async def get_attribution(
        self,
        account_id: int | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        # 接口变更说明（相对 system_design §5.6）：
        # period: DateRange → period_start/period_end（FastAPI 查询参数友好）
        # 返回类型 dict（Pydantic 序列化由 API 层负责）
    ) -> dict:
        """三维归因（SDD §12.2）：by_stock / by_industry / by_strategy。"""
        aid = await self._resolve_account_id(account_id)
        if aid is None:
            return {"by_stock": [], "by_industry": [], "by_strategy": []}

        query = select(TradeRecord).where(TradeRecord.account_id == aid)
        if period_start:
            query = query.where(TradeRecord.trade_date >= period_start)
        if period_end:
            query = query.where(TradeRecord.trade_date <= period_end)
        trade_rows = (await self._session.execute(query)).scalars().all()

        by_stock = _calc_by_stock(trade_rows)
        by_industry = await self._calc_by_industry(trade_rows)
        by_strategy = await self._calc_by_strategy(trade_rows)

        return {"by_stock": by_stock, "by_industry": by_industry, "by_strategy": by_strategy}

    async def _calc_by_industry(self, trade_rows: list) -> list[dict]:
        from quantpilot.models.market import StockInfo
        codes = list({r.ts_code for r in trade_rows})
        if not codes:
            return []
        result = await self._session.execute(
            select(StockInfo.ts_code, StockInfo.sw_industry_l1).where(StockInfo.ts_code.in_(codes))
        )
        industry_map = {row.ts_code: (row.sw_industry_l1 or "其他") for row in result}

        industry_pnl: dict[str, float] = {}
        industry_count: dict[str, int] = {}
        # 按 ts_code 配对 BUY/SELL
        buys: dict[str, float] = {}
        sells: dict[str, float] = {}
        for r in trade_rows:
            if r.trade_type == "BUY":
                buys[r.ts_code] = buys.get(r.ts_code, 0) + float(r.amount or 0)
            elif r.trade_type == "SELL":
                sells[r.ts_code] = sells.get(r.ts_code, 0) + float(r.amount or 0)

        for ts_code, sell_amt in sells.items():
            buy_amt = buys.get(ts_code, 0)
            pnl = sell_amt - buy_amt
            industry = industry_map.get(ts_code, "其他")
            industry_pnl[industry] = industry_pnl.get(industry, 0) + pnl
            industry_count[industry] = industry_count.get(industry, 0) + 1

        return [
            {"industry": k, "realized_pnl": round(v, 2), "trade_count": industry_count.get(k)}
            for k, v in sorted(industry_pnl.items(), key=lambda x: x[1], reverse=True)
        ]

    async def _calc_by_strategy(self, trade_rows: list) -> list[dict]:
        signal_ids = [r.signal_id for r in trade_rows if r.signal_id is not None]
        if not signal_ids:
            return []

        result = await self._session.execute(
            select(SignalScoreSnapshot.signal_id, SignalScoreSnapshot.score_breakdown)
            .where(SignalScoreSnapshot.signal_id.in_(signal_ids))
        )
        snapshot_map = {row.signal_id: (row.score_breakdown or {}) for row in result}

        # signal_id → ts_code（从 trade_rows）
        sig_to_trade: dict[int, list] = {}
        for r in trade_rows:
            if r.signal_id:
                sig_to_trade.setdefault(r.signal_id, []).append(r)

        # 主导策略 = argmax(score_breakdown)
        strategy_stats: dict[str, dict] = {}
        for sig_id, records in sig_to_trade.items():
            breakdown = snapshot_map.get(sig_id, {})
            if not breakdown:
                continue
            dominant = max(breakdown, key=lambda k: breakdown[k])
            sells = [r for r in records if r.trade_type == "SELL"]
            buys = [r for r in records if r.trade_type == "BUY"]
            sell_amt = sum(float(r.amount or 0) for r in sells)
            buy_amt = sum(float(r.amount or 0) for r in buys)
            pnl = sell_amt - buy_amt
            s = strategy_stats.setdefault(dominant, {"count": 0, "pnls": []})
            s["count"] += 1
            if sells:
                s["pnls"].append(pnl)

        result_list = []
        for strat, stats in strategy_stats.items():
            pnls = stats["pnls"]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            win_rate = round(len(wins) / len(pnls), 4) if pnls else None
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = abs(sum(losses) / len(losses)) if losses else 0
            pl_ratio = round(avg_win / avg_loss, 4) if avg_loss > 0 else None
            result_list.append({
                "strategy_name": strat,
                "trade_count": stats["count"],
                "win_rate": win_rate,
                "profit_loss_ratio": pl_ratio,
            })
        return result_list

    # ------------------------------------------------------------------
    # get_behavioral_analysis（SDD §12.4）
    # ------------------------------------------------------------------

    async def get_behavioral_analysis(self, account_id: int | None = None) -> dict:
        """行为分析 6 项指标（SDD §12.4）。"""
        aid = await self._resolve_account_id(account_id)
        if aid is None:
            return _empty_behavior()

        trade_rows = (await self._session.execute(
            select(TradeRecord).where(TradeRecord.account_id == aid)
        )).scalars().all()

        if not trade_rows:
            return _empty_behavior()

        # 平均持仓天数（已平仓标的 BUY→SELL 自然日）
        avg_holding_days = _calc_avg_holding(trade_rows)

        # 月均交易笔数
        monthly_trade_count = _calc_monthly_trade_count(trade_rows)

        # 信号遵守率（signal_id 非空比例）
        total = len(trade_rows)
        with_signal = sum(1 for r in trade_rows if r.signal_id is not None)
        signal_compliance_rate = round(with_signal / total, 4) if total > 0 else None

        # 止损执行率（SDD §12.4）
        # 【降级说明】止损执行率 V1.0 暂不实现，需 Signal.stop_loss_price + 行情对比，返回 None。
        # 恢复条件：V1.5 通过 Signal 表和 daily_quote 历史计算。
        stop_loss_execution_rate = None

        # 追涨率（EXPIRED 后 3 日内有对应 ts_code BUY）
        # 【降级说明】追涨率 V1.0 暂不实现（需要 Signal.status=EXPIRED 查询），返回 None。
        # 恢复条件：V1.5 通过 signal 表 status=EXPIRED 与 trade_record 关联计算。
        chase_up_rate = None

        # PnL 分桶（已平仓标的）
        pnl_distribution = _calc_pnl_distribution(trade_rows)

        return {
            "avg_holding_days": avg_holding_days,
            "monthly_trade_count": monthly_trade_count,
            "signal_compliance_rate": signal_compliance_rate,
            "stop_loss_execution_rate": stop_loss_execution_rate,
            "chase_up_rate": chase_up_rate,
            "pnl_distribution": pnl_distribution,
        }


# ---------------------------------------------------------------------------
# 辅助纯函数
# ---------------------------------------------------------------------------

def _calc_max_drawdown(nav_values: list[float]) -> float:
    if len(nav_values) < 2:
        return 0.0
    running_max = nav_values[0]
    max_dd = 0.0
    for v in nav_values[1:]:
        if v > running_max:
            running_max = v
        dd = (running_max - v) / running_max if running_max > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _calc_sharpe(nav_values: list[float], rf: float = 0.03) -> float:
    import math

    if len(nav_values) < 2:
        return 0.0
    navs = [float(v) for v in nav_values]
    daily_returns = [(navs[i] - navs[i - 1]) / navs[i - 1] for i in range(1, len(navs))]
    if len(daily_returns) < 2:
        return 0.0
    n = len(daily_returns)
    ann_return = (navs[-1] / navs[0]) ** (252 / n) - 1
    import statistics
    ann_vol = statistics.stdev(daily_returns) * math.sqrt(252)
    if ann_vol == 0:
        return 0.0
    return (ann_return - rf) / ann_vol


def _calc_win_rate_from_trades(trade_rows: list) -> tuple[float | None, float | None]:
    """
    从 trade_record 计算胜率/盈亏比（仅已平仓标的）。

    【降级说明】V1.0 仅统计有完整 BUY+SELL 记录的标的；持仓中标的暂不计入。
    恢复条件：V1.5 可扩展为基于 WAC 的逐日浮动盈亏统计。
    """
    buys: dict[str, float] = {}
    sells: dict[str, float] = {}
    for r in trade_rows:
        amt = float(r.amount or 0)
        if r.trade_type == "BUY":
            buys[r.ts_code] = buys.get(r.ts_code, 0) + amt
        elif r.trade_type == "SELL":
            sells[r.ts_code] = sells.get(r.ts_code, 0) + amt

    pnls = [sells[c] - buys.get(c, 0) for c in sells]
    if not pnls:
        return None, None
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = round(len(wins) / len(pnls), 4)
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    pl_ratio = round(avg_win / avg_loss, 4) if avg_loss > 0 else None
    return win_rate, pl_ratio


def _calc_by_stock(trade_rows: list) -> list[dict]:
    buys: dict[str, dict] = {}
    sells: dict[str, list] = {}
    for r in trade_rows:
        if r.trade_type == "BUY":
            e = buys.setdefault(r.ts_code, {"amount": 0.0, "date": r.trade_date})
            e["amount"] += float(r.amount or 0)
            if r.trade_date < e["date"]:
                e["date"] = r.trade_date
        elif r.trade_type == "SELL":
            sells.setdefault(r.ts_code, []).append(r)

    result = []
    for ts_code, sell_list in sells.items():
        buy_info = buys.get(ts_code)
        sell_amt = sum(float(r.amount or 0) for r in sell_list)
        buy_amt = buy_info["amount"] if buy_info else 0.0
        pnl = sell_amt - buy_amt
        first_buy = buy_info["date"] if buy_info else None
        last_sell = max(r.trade_date for r in sell_list)
        holding_days = (last_sell - first_buy).days if first_buy else None
        pnl_pct = round(pnl / buy_amt, 4) if buy_amt > 0 else None
        result.append({
            "ts_code": ts_code,
            "holding_days": holding_days,
            "realized_pnl": round(pnl, 2),
            "realized_pnl_pct": pnl_pct,
        })
    return sorted(result, key=lambda x: x["realized_pnl"], reverse=True)


def _calc_avg_holding(trade_rows: list) -> float | None:
    buy_dates: dict[str, date] = {}
    holding_days_list: list[int] = []
    for r in sorted(trade_rows, key=lambda x: x.trade_date):
        if r.trade_type == "BUY" and r.ts_code not in buy_dates:
            buy_dates[r.ts_code] = r.trade_date
        elif r.trade_type == "SELL" and r.ts_code in buy_dates:
            days = (r.trade_date - buy_dates.pop(r.ts_code)).days
            holding_days_list.append(days)
    if not holding_days_list:
        return None
    return round(sum(holding_days_list) / len(holding_days_list), 1)


def _calc_monthly_trade_count(trade_rows: list) -> float | None:
    if not trade_rows:
        return None
    months: set = {(r.trade_date.year, r.trade_date.month) for r in trade_rows}
    return round(len(trade_rows) / len(months), 1) if months else None


def _calc_pnl_distribution(trade_rows: list) -> list[dict]:
    buys: dict[str, float] = {}
    sells: dict[str, float] = {}
    for r in trade_rows:
        amt = float(r.amount or 0)
        if r.trade_type == "BUY":
            buys[r.ts_code] = buys.get(r.ts_code, 0) + amt
        elif r.trade_type == "SELL":
            sells[r.ts_code] = sells.get(r.ts_code, 0) + amt

    pnl_pcts = []
    for ts_code, sell_amt in sells.items():
        buy_amt = buys.get(ts_code, 0)
        if buy_amt > 0:
            pnl_pcts.append((sell_amt - buy_amt) / buy_amt)

    buckets = [
        ("< -30%", lambda p: p < -0.30),
        ("-30%~-20%", lambda p: -0.30 <= p < -0.20),
        ("-20%~-10%", lambda p: -0.20 <= p < -0.10),
        ("-10%~0%", lambda p: -0.10 <= p < 0),
        ("0%~10%", lambda p: 0 <= p < 0.10),
        ("10%~20%", lambda p: 0.10 <= p < 0.20),
        ("20%~30%", lambda p: 0.20 <= p < 0.30),
        ("> 30%", lambda p: p >= 0.30),
    ]
    return [{"label": label, "count": sum(1 for p in pnl_pcts if fn(p))} for label, fn in buckets]


def _empty_behavior() -> dict:
    return {
        "avg_holding_days": None,
        "monthly_trade_count": None,
        "signal_compliance_rate": None,
        "stop_loss_execution_rate": None,
        "chase_up_rate": None,
        "pnl_distribution": [],
    }
