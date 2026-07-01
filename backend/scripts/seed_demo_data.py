"""
植入最少演示数据，让所有前端功能页面都能正常显示。
运行方式：
  cd backend
  uv run python scripts/seed_demo_data.py
"""
import asyncio
import math
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from quantpilot.core.database import AsyncSessionLocal
from quantpilot.models.account import Account, DailyPortfolioValue, FundFlow, Position, TradeRecord
from quantpilot.models.business import (
    FactorICWindowState,
    MarketStateHistory,
    Report,
    Signal,
    SignalScoreSnapshot,
)
from quantpilot.models.market import DailyQuote, IndexHistory, StockInfo
from quantpilot.models.system import UserConfig
from quantpilot.models.user import User

ACCOUNT_ID = 1
INITIAL_CAPITAL = 1_000_000.0

# A 股法定节假日（含调休休市日）。覆盖演示数据窗口（最多回看 90 自然日）+ 短期前瞻。
# 维护到 2026 年；2027 年起需补充。
# 数据来源：上交所/深交所节假日公告对照中国国务院办公厅。
_CN_HOLIDAYS: frozenset[date] = frozenset({
    # 2025 元旦
    date(2025, 1, 1),
    # 2025 春节
    date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30), date(2025, 1, 31),
    date(2025, 2, 3), date(2025, 2, 4),
    # 2025 清明
    date(2025, 4, 4), date(2025, 4, 7),
    # 2025 劳动节
    date(2025, 5, 1), date(2025, 5, 2), date(2025, 5, 5),
    # 2025 端午
    date(2025, 5, 31), date(2025, 6, 2),
    # 2025 中秋+国庆（合并）
    date(2025, 10, 1), date(2025, 10, 2), date(2025, 10, 3),
    date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 8),
    # 2026 元旦
    date(2026, 1, 1), date(2026, 1, 2),
    # 2026 春节（除夕至初六）
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 23),
    date(2026, 2, 24),
    # 2026 清明
    date(2026, 4, 6),
    # 2026 劳动节
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),
    # 2026 端午
    date(2026, 6, 19), date(2026, 6, 22),
    # 2026 国庆+中秋（合并）
    date(2026, 9, 25),
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),
    date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8),
})


def _is_trade_date(d: date) -> bool:
    """A 股交易日：工作日且非法定节假日。"""
    return d.weekday() < 5 and d not in _CN_HOLIDAYS


def _today_or_prev_trade_date() -> date:
    """演示用 TODAY：若今日非交易日，回退到最近一个交易日（演示数据日期始终落在交易日）。"""
    d = date.today()
    while not _is_trade_date(d):
        d -= timedelta(days=1)
    return d


TODAY = _today_or_prev_trade_date()


def trade_days(n: int) -> list[date]:
    """返回 TODAY 往前 n 个 A 股交易日（含 TODAY；剔除节假日）。"""
    days = []
    d = TODAY
    while len(days) < n:
        if _is_trade_date(d):
            days.append(d)
        d -= timedelta(days=1)
    return days


async def seed():
    async with AsyncSessionLocal() as s:
        async with s.begin():
            # ── 清理旧演示数据 ──────────────────────────────────────────
            for model in [
                SignalScoreSnapshot, Signal, MarketStateHistory,
                DailyPortfolioValue, FundFlow, TradeRecord, Position,
                Account, FactorICWindowState, Report, UserConfig, IndexHistory,
                DailyQuote,  # 清除 Tushare 历史数据，确保回测宇宙仅含演示股票
            ]:
                await s.execute(delete(model))

            # ── 1. Account ──────────────────────────────────────────────
            # V1.5-G：account.user_id NOT NULL，归属 0018 种子的首用户。
            seed_user_id = (
                await s.execute(select(User.id).order_by(User.id).limit(1))
            ).scalar_one()
            account = Account(
                id=ACCOUNT_ID,
                user_id=seed_user_id,
                name="量化主账户",
                account_type="REAL",
                broker="华泰证券",
                total_assets=1_238_500.0,
                cash=488_500.0,
                synced_at=datetime(2026, 4, 15, 15, 0, 0, tzinfo=timezone.utc),
            )
            s.add(account)

            # ── 2. Positions ────────────────────────────────────────────
            positions = [
                Position(
                    account_id=ACCOUNT_ID,
                    ts_code="600519.SH",
                    shares=200,
                    cost_price=1650.00,
                    current_price=1721.50,
                    market_value=344_300.0,
                    pnl_pct=0.0433,
                    open_date=date(2026, 2, 10),
                    phase="HOLD",
                ),
                Position(
                    account_id=ACCOUNT_ID,
                    ts_code="000858.SZ",
                    shares=1000,
                    cost_price=168.50,
                    current_price=179.20,
                    market_value=179_200.0,
                    pnl_pct=0.0635,
                    open_date=date(2026, 3, 5),
                    phase="BUILD",
                ),
                Position(
                    account_id=ACCOUNT_ID,
                    ts_code="300750.SZ",
                    shares=500,
                    cost_price=420.00,
                    current_price=452.30,
                    market_value=226_150.0,
                    pnl_pct=0.0769,
                    open_date=date(2026, 3, 20),
                    phase="HOLD",
                ),
            ]
            s.add_all(positions)

            # ── 3. TradeRecords ─────────────────────────────────────────
            trades = [
                TradeRecord(
                    account_id=ACCOUNT_ID, ts_code="600519.SH", trade_type="BUY",
                    trade_date=date(2026, 2, 10), price=1650.00, shares=200,
                    amount=330_000.0, commission=99.0, stamp_tax=0.0, note="建仓茅台",
                ),
                TradeRecord(
                    account_id=ACCOUNT_ID, ts_code="000858.SZ", trade_type="BUY",
                    trade_date=date(2026, 3, 5), price=168.50, shares=1000,
                    amount=168_500.0, commission=50.55, stamp_tax=0.0, note="建仓五粮液",
                ),
                TradeRecord(
                    account_id=ACCOUNT_ID, ts_code="300750.SZ", trade_type="BUY",
                    trade_date=date(2026, 3, 20), price=420.00, shares=500,
                    amount=210_000.0, commission=63.0, stamp_tax=0.0, note="建仓宁德时代",
                ),
            ]
            s.add_all(trades)

            # ── 4. FundFlows ────────────────────────────────────────────
            flows = [
                FundFlow(
                    account_id=ACCOUNT_ID, flow_type="DEPOSIT",
                    amount=1_000_000.0, trade_date=date(2026, 1, 2), note="期初入金",
                ),
                FundFlow(
                    account_id=ACCOUNT_ID, flow_type="DEPOSIT",
                    amount=300_000.0, trade_date=date(2026, 3, 1), note="追加资金",
                ),
            ]
            s.add_all(flows)

            # ── 5. DailyPortfolioValue (30 交易日净值曲线) ──────────────
            tdays = trade_days(30)
            nav_base = INITIAL_CAPITAL
            # 模拟净值从 1.0 增长到 1.2386
            nav_values = [
                nav_base * (1.0 + 0.2386 * i / (len(tdays) - 1))
                for i in range(len(tdays))
            ]
            dpvs = []
            for d_date, nav_val in zip(reversed(tdays), nav_values):
                pos_val = nav_val * 0.61
                dpvs.append(DailyPortfolioValue(
                    account_id=ACCOUNT_ID,
                    trade_date=d_date,
                    total_value=round(nav_val, 2),
                    cash=round(nav_val - pos_val, 2),
                    position_value=round(pos_val, 2),
                ))
            s.add_all(dpvs)

            # ── 5b. IndexHistory HS300 (90 交易日，含前置历史窗口) ──────────────
            # 回测引擎加载 lookback_start(130 日历天前) 至 end_date 的 HS300 数据，
            # 需覆盖 90 交易日才能让市场状态引擎（ADX/MA）在回测开始时有足够历史。
            # 模拟沪深300从 3100 缓涨至 3350（+8.1%），跑输组合净值以体现 alpha。
            kline_tdays = trade_days(90)
            hs300_base = 3100.0
            hs300_end = 3350.0
            hs300_rows = []
            n_hs = len(kline_tdays)
            for i, d_date in enumerate(reversed(kline_tdays)):
                t = i / (n_hs - 1)
                close_val = round(hs300_base + (hs300_end - hs300_base) * t, 3)
                wave = 25.0 * math.sin(2 * math.pi * t * 4)
                close_val = round(close_val + wave, 3)
                hs300_rows.append(IndexHistory(
                    index_code="000300.SH",
                    trade_date=d_date,
                    open=round(close_val * 0.998, 3),
                    high=round(close_val * 1.005, 3),
                    low=round(close_val * 0.993, 3),
                    close=close_val,
                    vol=None,
                ))
            s.add_all(hs300_rows)

            # ── 5c. StockInfo — 信号股票名称（upsert，不影响已有数据）──────
            stock_info_rows = [
                {"ts_code": "601318.SH", "name": "中国平安", "market": "MAIN", "is_active": True},
                {"ts_code": "000001.SZ", "name": "平安银行", "market": "MAIN", "is_active": True},
                {"ts_code": "600519.SH", "name": "贵州茅台", "market": "MAIN", "is_active": True},
                {"ts_code": "000858.SZ", "name": "五粮液",   "market": "MAIN", "is_active": True},
                {"ts_code": "300750.SZ", "name": "宁德时代", "market": "GEM",  "is_active": True},
                {"ts_code": "002415.SZ", "name": "海康威视", "market": "MAIN", "is_active": True},
            ]
            si_stmt = pg_insert(StockInfo).values(stock_info_rows)
            si_stmt = si_stmt.on_conflict_do_update(
                index_elements=["ts_code"],
                set_={"name": si_stmt.excluded.name, "market": si_stmt.excluded.market,
                      "is_active": si_stmt.excluded.is_active},
            )
            await s.execute(si_stmt)

            # ── 5d. DailyQuote — 90 日 K 线（5 只股票，upsert）──────────
            # 延长至 90 交易日（≈ 130 日历天）供回测引擎前置历史窗口使用：
            #   TrendStrategy: 需要 65 日数据（MA60）
            #   MomentumStrategy: 需要 61 日数据（return_3m）
            # 新增 000858.SZ（五粮液）和 300750.SZ（宁德时代），与持仓数据一致。
            kline_specs = [
                # ts_code,     start_price, end_price, amplitude, avg_vol（手）
                ("601318.SH", 85.0,   91.0,   1.2,  600_000),  # 中国平安
                ("000001.SZ", 12.3,   13.2,   0.15, 1_500_000), # 平安银行
                ("600519.SH", 1680.0, 1722.0, 18.0, 25_000),   # 贵州茅台
                ("000858.SZ", 160.0,  179.0,  2.5,  150_000),  # 五粮液
                ("300750.SZ", 400.0,  452.0,  8.0,  80_000),   # 宁德时代
                # 海康威视：V形走势（先大跌40%触发超卖信号，后反弹），专用于回测演示
                # amp=25 使 t=0.25 附近出现大幅下探（超卖），MR评分近100，回测可生成BUY信号
                ("002415.SZ", 80.0,   65.0,   25.0, 300_000),
            ]
            quote_rows = []
            for ts_code, p_start, p_end, amp, avg_vol in kline_specs:
                n = len(kline_tdays)
                for i, d_date in enumerate(reversed(kline_tdays)):
                    t = i / (n - 1)
                    base = p_start + (p_end - p_start) * t
                    wave = amp * math.sin(2 * math.pi * t * 3)
                    close = round(base + wave, 3)
                    open_ = round(close * (1 - 0.003 + 0.006 * ((i * 7 + 3) % 10) / 10), 3)
                    high = round(max(open_, close) * (1 + 0.004 + 0.003 * ((i * 3 + 1) % 5) / 5), 3)
                    low  = round(min(open_, close) * (1 - 0.004 - 0.003 * ((i * 5 + 2) % 5) / 5), 3)
                    # 模拟成交量：基准量 × (0.6 ~ 1.4) 随机波动
                    vol_factor = 0.6 + 0.8 * ((i * 13 + 7) % 20) / 20
                    vol = int(avg_vol * vol_factor)
                    quote_rows.append({
                        "ts_code": ts_code, "trade_date": d_date,
                        "open": open_, "high": high, "low": low, "close": close,
                        "vol": vol,
                    })
            dq_stmt = pg_insert(DailyQuote).values(quote_rows)
            dq_stmt = dq_stmt.on_conflict_do_update(
                index_elements=["ts_code", "trade_date"],
                set_={
                    "open": dq_stmt.excluded.open, "high": dq_stmt.excluded.high,
                    "low": dq_stmt.excluded.low, "close": dq_stmt.excluded.close,
                    "vol": dq_stmt.excluded.vol,
                },
            )
            await s.execute(dq_stmt)

            # ── 6. MarketStateHistory (5 交易日) ────────────────────────
            states = ["UPTREND", "UPTREND", "OSCILLATION", "UPTREND", "UPTREND"]
            for i, d_date in enumerate(trade_days(5)):
                s.add(MarketStateHistory(
                    trade_date=d_date,
                    market_state=states[i],
                    trend_strength=round(0.65 + i * 0.03, 2),
                    adx_value=round(28.5 + i * 1.2, 3),
                    ma20=round(3280.0 + i * 15, 3),
                    ma60=round(3150.0 + i * 8, 3),
                    state_changed=(i == 2),
                    description="ADX 上行，均线多头排列，市场处于上升趋势",
                ))

            # ── 7. Signals (今日 3 条 + 历史 5 条) ──────────────────────
            today_signals = [
                Signal(
                    ts_code="601318.SH", signal_type="BUY", trade_date=TODAY,
                    score=88.5, suggested_pct=0.10,
                    suggested_price_low=88.5, suggested_price_high=91.0,
                    stop_loss_price=85.0, signal_strength="STRONG",
                    t1_warning="A股T+1，买入后次日方可卖出",
                    reason="动量因子强劲，估值合理，市场处于上升趋势",
                    status="NEW",
                ),
                Signal(
                    ts_code="000001.SZ", signal_type="BUY", trade_date=TODAY,
                    score=74.2, suggested_pct=0.08,
                    suggested_price_low=12.8, suggested_price_high=13.2,
                    stop_loss_price=12.0, signal_strength="MODERATE",
                    t1_warning="A股T+1，买入后次日方可卖出",
                    reason="价值因子触发，PE历史低位",
                    status="NEW",
                ),
                Signal(
                    ts_code="600519.SH", signal_type="SELL", trade_date=TODAY,
                    score=62.0, suggested_pct=0.05,
                    stop_loss_price=None, signal_strength=None,
                    reason="动量减弱，建议减仓",
                    status="VIEWED",
                ),
            ]
            s.add_all(today_signals)
            await s.flush()  # 获取 id

            # 历史信号
            hist_days = trade_days(10)[3:]  # 3-10 天前
            hist_data = [
                ("002415.SZ", "BUY", 82.0, "STRONG"),
                ("300059.SZ", "BUY", 71.5, "MODERATE"),
                ("601166.SH", "SELL", 65.0, None),
                ("000333.SZ", "BUY", 78.3, "MODERATE"),
                ("002594.SZ", "BUY", 69.8, "MODERATE"),
            ]
            for (ts, stype, score, strength), hist_d in zip(hist_data, hist_days):
                s.add(Signal(
                    ts_code=ts, signal_type=stype, trade_date=hist_d,
                    score=score, suggested_pct=0.08, signal_strength=strength,
                    status="ACTED" if score > 75 else "EXPIRED",
                    reason="历史信号",
                ))

            # ── 8. FactorIcHistory (3 因子 × 3 月) ──────────────────────
            factors = [
                ("TrendStrategy", "momentum_20d"),
                ("ValueStrategy", "pe_percentile"),
                ("ReversionStrategy", "rsi_reversal"),
            ]
            months = [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 15)]
            ic_data = {
                "momentum_20d":  [0.062, 0.058, 0.071],
                "pe_percentile": [0.041, -0.012, 0.038],
                "rsi_reversal":  [-0.008, -0.031, -0.045],
            }
            for strategy, factor in factors:
                for i, month in enumerate(months):
                    ic_val = ic_data[factor][i]
                    ic_vals = ic_data[factor][:i+1]
                    ic_mean = sum(ic_vals) / len(ic_vals)
                    ic_std = (sum((x - ic_mean)**2 for x in ic_vals) / max(len(ic_vals)-1, 1))**0.5
                    ir = ic_mean / ic_std if ic_std > 0 else None
                    # rsi_reversal 连续为负触发 DECAY 告警
                    alert = "DECAY" if factor == "rsi_reversal" and i >= 1 else None
                    # Phase 15 §15-7：月度因子质量归并进 factor_ic_window_state
                    # （row_type='monthly_quality'，state='ALL' 哨兵，复用统计列）
                    s.add(FactorICWindowState(
                        strategy=strategy,
                        factor=factor,
                        state="ALL",
                        trade_date=month,
                        ic_value=round(ic_val, 4),
                        ic_mean_state=round(ic_mean, 4),
                        ic_std_state=round(ic_std, 4) if ic_std > 0 else None,
                        icir=round(ir, 4) if ir else None,
                        half_life=round(15 + i * 2),
                        sample_size=0,
                        row_type="monthly_quality",
                        alert_status=alert,
                    ))

            # ── 9. Reports ───────────────────────────────────────────────
            weekly_content = {
                "summary": "本周组合净值 +1.82%，跑赢沪深300指数（+0.96%）",
                "performance": {
                    "weekly_return": 0.0182,
                    "benchmark_return": 0.0096,
                    "alpha": 0.0086,
                },
                "top_signals": [
                    {"ts_code": "601318.SH", "signal_type": "BUY", "score": 88.5},
                    {"ts_code": "000001.SZ", "signal_type": "BUY", "score": 74.2},
                ],
                "market_state": "UPTREND",
                "risk_notes": "持仓集中度偏高，建议分散至5支以上标的",
            }
            monthly_content = {
                "summary": "本月组合净值 +7.23%，最大回撤 -2.14%",
                "performance": {
                    "monthly_return": 0.0723,
                    "max_drawdown": -0.0214,
                    "sharpe_ratio": 1.85,
                    "win_rate": 0.625,
                },
                "factor_health": (
                    "momentum_20d IC均值 0.063（正常），"
                    "rsi_reversal 连续2月IC为负（告警）"
                ),
                "suggestions": ["减少 rsi_reversal 策略权重", "关注消费板块机会"],
            }
            s.add(Report(
                report_type="WEEKLY",
                period_start=date(2026, 4, 7),
                period_end=date(2026, 4, 11),
                content=weekly_content,
                summary="本周净值 +1.82%，跑赢基准",
            ))
            s.add(Report(
                report_type="MONTHLY",
                period_start=date(2026, 3, 1),
                period_end=date(2026, 3, 31),
                content=monthly_content,
                summary="3月净值 +7.23%，最大回撤 -2.14%",
            ))

            # ── 10. UserConfig ───────────────────────────────────────────
            # V1.5-G G-4a：user_level 规范化为 L1/L2/L3 枚举（旧 "USER" 非法字面会让
            # §6.3 字符串 <= 层级比较失效）。取值对齐 SDD §14 层级归属。
            configs = [
                UserConfig(
                    config_key="position_limit",
                    config_value={"max_single_position_pct": 0.15, "max_total_positions": 10},
                    user_level="L2",
                    description="单股最大仓位比例 & 最大持仓数量",
                ),
                UserConfig(
                    config_key="risk_params",
                    config_value={"stop_loss_pct": 0.08, "take_profit_pct": 0.25},
                    user_level="L2",
                    description="止损止盈参数",
                ),
                UserConfig(
                    config_key="strategy_weights",
                    config_value={
                        "TrendStrategy": 0.5,
                        "ValueStrategy": 0.3,
                        "ReversionStrategy": 0.2,
                    },
                    user_level="L3",
                    description="策略权重配置",
                ),
            ]
            s.add_all(configs)

    print("Demo data seeded OK")
    print("  Account: 1, total_assets=1238500")
    print("  Positions: 3 (600519/000858/300750)")
    print(f"  Signals: 3 today + 5 history (TODAY={TODAY})")
    print("  NAV: 30 trade days")
    print("  HS300 benchmark: 90 trade days (000300.SH) with high/low")
    print("  StockInfo: 6 stocks (upsert, incl. 002415.SZ V-shape for backtest demo)")
    print("  DailyQuote kline: 6 stocks x 90 days (upsert) [Tushare data cleared first]")
    print("  MarketState: 5 days")
    print("  FactorIC: 3 factors x 3 months = 9 rows")
    print("  Reports: 1 weekly + 1 monthly")
    print("  UserConfig: 3 rows")


if __name__ == "__main__":
    asyncio.run(seed())
