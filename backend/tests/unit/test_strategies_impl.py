"""TRD/REV/MOM/VAL 策略实现单元测试（Phase 4 T-07~T-10）。"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from quantpilot.engine.strategies.base import MarketSnapshot

# ── 辅助：构建含技术指标数据的 MarketSnapshot ──────────────────────────────────────

def _prices_df(
    codes: list[str],
    series_by_code: dict[str, list[float]],
    base_date: date = date(2025, 1, 31),
) -> pd.DataFrame:
    """构建 adj_prices DataFrame。series_by_code: {ts_code: [oldest→newest]}。"""
    n = max(len(v) for v in series_by_code.values())
    # 生成 n 个工作日（最新为 base_date）
    trade_dates: list[date] = []
    d = base_date
    while len(trade_dates) < n:
        if d.weekday() < 5:
            trade_dates.append(d)
        d -= timedelta(days=1)
    trade_dates = list(reversed(trade_dates))  # oldest first

    data: dict[date, dict[str, float]] = {}
    for col_date, i in zip(trade_dates, range(n)):
        data[col_date] = {}
        for code in codes:
            vals = series_by_code.get(code, [])
            data[col_date][code] = vals[i] if i < len(vals) else float("nan")

    df = pd.DataFrame(data, index=pd.Index(codes, name="ts_code"))
    return df


def _make_snapshot_for_strategies(
    codes: list[str],
    close_values: list[float],
    price_series: dict[str, list[float]] | None = None,
    pe_values: list[float] | None = None,
    pb_values: list[float] | None = None,
    roe_values: list[float] | None = None,
    sw_industry: list[str] | None = None,
) -> MarketSnapshot:
    """构建策略测试用 MarketSnapshot。"""
    base_date = date(2025, 1, 31)
    idx = pd.Index(codes, name="ts_code")
    n = 130  # 覆盖 MomentumStrategy 120 交易日（6M）窗口

    if price_series is None:
        # 默认：每只股票维持 close_values 中的收盘价不变（130 天）
        price_series = {c: [v] * n for c, v in zip(codes, close_values)}

    adj_prices = _prices_df(codes, price_series, base_date)

    pe = pe_values or [20.0] * len(codes)
    pb = pb_values or [2.0] * len(codes)
    roe = roe_values or [10.0] * len(codes)
    industries = sw_industry or ["制造"] * len(codes)

    daily_quotes = pd.DataFrame(
        {
            "close": close_values,
            "pe_ttm": pe,
            "pb": pb,
            "amount": [1e7] * len(codes),
            "vol": [1e4] * len(codes),
            "limit_up": [False] * len(codes),
        },
        index=idx,
    )

    financials = pd.DataFrame(
        {
            "roe": roe,
            "net_profit_yoy": [5.0] * len(codes),
            "debt_to_asset": [0.5] * len(codes),
            "sw_industry_l1": industries,
        },
        index=idx,
    )

    # pe_pb_history：每只股票 5 年的 pe_ttm/pb 历史
    history_dates = pd.date_range(end=base_date, periods=5 * 250, freq="B").date.tolist()
    tuples = [(c, d) for c in codes for d in history_dates]
    mi = pd.MultiIndex.from_tuples(tuples, names=["ts_code", "trade_date"])
    pe_pb_history = pd.DataFrame(
        {
            "pe_ttm": [pe[codes.index(c)] * (0.8 + 0.4 * idx / (5 * 250))
                       for idx, (c, _) in enumerate(tuples)],
            "pb": [pb[codes.index(c)] for c, _ in tuples],
        },
        index=mi,
    )

    snapshot: MarketSnapshot = {
        "trade_date": base_date,
        "adj_prices": adj_prices,
        "daily_quotes": daily_quotes,
        "financials": financials,
        "pe_pb_history": pe_pb_history,
        "index_adj_prices": adj_prices.mean().to_frame(name="close").T,  # 简化版
    }
    return snapshot


# ── TrendStrategy 测试 ────────────────────────────────────────────────────────

class TestTrendStrategy:
    @pytest.fixture
    def strategy(self):
        from quantpilot.engine.strategies.trend import TrendStrategy
        return TrendStrategy()

    # TRD-01：均线多头排列
    def test_trd_01_ma_alignment_bullish(self, strategy) -> None:
        """MA5>MA10>MA20>MA60 时，多头排列的股票 ma_alignment 因子高于空头股票。"""
        # 构造两只股票：A 持续上升（多头），B 持续下降（空头）
        n = 130
        bull_prices = [100.0 + i * 0.5 for i in range(n)]   # 单调上升
        bear_prices = [100.0 - i * 0.5 for i in range(n)]   # 单调下降
        bear_prices = [max(p, 1.0) for p in bear_prices]

        snapshot = _make_snapshot_for_strategies(
            ["BULL", "BEAR"],
            close_values=[bull_prices[-1], bear_prices[-1]],
            price_series={"BULL": bull_prices, "BEAR": bear_prices},
        )
        results = strategy.score(pd.Index(["BULL", "BEAR"]), snapshot)

        scores = {r.ts_code: r.score for r in results}
        assert "BULL" in scores and "BEAR" in scores
        assert scores["BULL"] > scores["BEAR"], (
            f"多头排列(BULL={scores['BULL']:.1f}) 应高于空头排列(BEAR={scores['BEAR']:.1f})"
        )

    # TRD-02：MACD 金叉
    def test_trd_02_macd_golden_cross(self, strategy) -> None:
        """DIF>DEA>0 时，该股票得分高于 DIF<DEA 的股票。"""
        n = 130
        # 金叉股：价格从下方急速上升（DIF 会超过 DEA）
        golden = [80.0 + i * 1.0 for i in range(n)]
        # 死叉股：价格从上方持续下跌
        death = [120.0 - i * 0.8 for i in range(n)]
        death = [max(p, 1.0) for p in death]

        snapshot = _make_snapshot_for_strategies(
            ["GOLDEN", "DEATH"],
            close_values=[golden[-1], death[-1]],
            price_series={"GOLDEN": golden, "DEATH": death},
        )
        results = strategy.score(pd.Index(["GOLDEN", "DEATH"]), snapshot)
        scores = {r.ts_code: r.score for r in results}

        assert scores["GOLDEN"] > scores["DEATH"], (
            f"金叉(GOLDEN={scores['GOLDEN']:.1f}) 应高于死叉(DEATH={scores['DEATH']:.1f})"
        )

    # TRD-03：价格突破近期高点
    def test_trd_03_price_breakout(self, strategy) -> None:
        """close == 近20日高点 时，price_breakout 因子处于高位。"""
        n = 130
        # BREAKOUT：价格持续上升，收盘价就是近20日最高
        breakout = [50.0 + i * 0.3 for i in range(n)]
        # NO_BREAK：价格在震荡中下行，收盘价远低于近20日最高
        no_break = [80.0 - i * 0.2 for i in range(n)]
        no_break = [max(p, 1.0) for p in no_break]

        snapshot = _make_snapshot_for_strategies(
            ["BREAKOUT", "NO_BREAK"],
            close_values=[breakout[-1], no_break[-1]],
            price_series={"BREAKOUT": breakout, "NO_BREAK": no_break},
        )
        results = strategy.score(pd.Index(["BREAKOUT", "NO_BREAK"]), snapshot)
        scores = {r.ts_code: r.score for r in results}

        assert scores["BREAKOUT"] > scores["NO_BREAK"], (
            f"突破(BREAKOUT={scores['BREAKOUT']:.1f}) "
            f"应高于未突破(NO_BREAK={scores['NO_BREAK']:.1f})"
        )


# ── MeanReversionStrategy 测试 ─────────────────────────────────────────────────

class TestMeanReversionStrategy:
    @pytest.fixture
    def strategy(self):
        from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
        return MeanReversionStrategy()

    # REV-01：RSI 超卖
    def test_rev_01_rsi_oversold(self, strategy) -> None:
        """RSI=20（超卖）的股票得分高于 RSI=70（超买）的股票。"""
        n = 130
        # OVERSOLD：价格持续下跌，RSI 将极低
        oversold = [100.0 - i * 0.5 for i in range(n)]
        oversold = [max(p, 1.0) for p in oversold]
        # OVERBOUGHT：价格持续上涨，RSI 将极高
        overbought = [50.0 + i * 0.5 for i in range(n)]

        snapshot = _make_snapshot_for_strategies(
            ["OVERSOLD", "OVERBOUGHT"],
            close_values=[oversold[-1], overbought[-1]],
            price_series={"OVERSOLD": oversold, "OVERBOUGHT": overbought},
        )
        results = strategy.score(pd.Index(["OVERSOLD", "OVERBOUGHT"]), snapshot)
        scores = {r.ts_code: r.score for r in results}

        assert scores["OVERSOLD"] > scores["OVERBOUGHT"], (
            f"超卖(OVERSOLD={scores['OVERSOLD']:.1f}) "
            f"应高于超买(OVERBOUGHT={scores['OVERBOUGHT']:.1f})"
        )

    # REV-02：价格偏离均线
    def test_rev_02_price_below_ma20(self, strategy) -> None:
        """价格远低于 MA20 时，price_deviation 因子高分。"""
        n = 130
        # BELOW_MA：前 120 天高位震荡，最后 10 天急跌
        below_ma = [100.0] * 120 + [100.0 - i * 4.0 for i in range(1, 11)]
        # NEAR_MA：价格缓慢上行，始终接近均线
        near_ma = [50.0 + i * 0.05 for i in range(n)]

        snapshot = _make_snapshot_for_strategies(
            ["BELOW_MA", "NEAR_MA"],
            close_values=[below_ma[-1], near_ma[-1]],
            price_series={"BELOW_MA": below_ma, "NEAR_MA": near_ma},
        )
        results = strategy.score(pd.Index(["BELOW_MA", "NEAR_MA"]), snapshot)
        scores = {r.ts_code: r.score for r in results}

        assert scores["BELOW_MA"] > scores["NEAR_MA"], (
            f"偏离均线(BELOW_MA={scores['BELOW_MA']:.1f}) "
            f"应高于贴近均线(NEAR_MA={scores['NEAR_MA']:.1f})"
        )

    # REV-03：布林带下轨
    def test_rev_03_bb_lower_band(self, strategy) -> None:
        """接近布林带下轨时，bb_position 因子高分。"""
        # LOWER_BB：高位震荡后急跌，当前价接近下轨
        lower_bb = [100.0] * 115 + [100.0 - i * 6.0 for i in range(1, 16)]
        # UPPER_BB：低位震荡后急涨，当前价接近上轨
        upper_bb = [50.0] * 115 + [50.0 + i * 4.0 for i in range(1, 16)]

        snapshot = _make_snapshot_for_strategies(
            ["LOWER_BB", "UPPER_BB"],
            close_values=[lower_bb[-1], upper_bb[-1]],
            price_series={"LOWER_BB": lower_bb, "UPPER_BB": upper_bb},
        )
        results = strategy.score(pd.Index(["LOWER_BB", "UPPER_BB"]), snapshot)
        scores = {r.ts_code: r.score for r in results}

        assert scores["LOWER_BB"] > scores["UPPER_BB"], (
            f"下轨(LOWER_BB={scores['LOWER_BB']:.1f}) 应高于上轨(UPPER_BB={scores['UPPER_BB']:.1f})"
        )


# ── MomentumStrategy 测试 ──────────────────────────────────────────────────────

class TestMomentumStrategy:
    @pytest.fixture
    def strategy(self):
        from quantpilot.engine.strategies.momentum import MomentumStrategy
        return MomentumStrategy()

    # MOM-01：3 月涨幅排名
    def test_mom_01_return_3m_highest_wins(self, strategy) -> None:
        """近 60 交易日涨幅最高的标的得分最高。
        注：涨幅集中在 3M 窗口前段（近 1M 涨幅为零），避免触发追高剔除逻辑。
        """
        # HIGH_MOM：3M内涨幅大，但涨幅发生在近60日的前40天，近20日持平
        high_mom = [100.0] * 70 + [100.0 + i * 3.0 for i in range(1, 41)] + [220.0] * 20
        # LOW_MOM：3M内小涨，近20日也小涨
        low_mom = [100.0] * 70 + [100.0 + i * 0.1 for i in range(1, 61)]

        codes = ["HIGH_MOM", "LOW_MOM"]
        snapshot = _make_snapshot_for_strategies(
            codes,
            close_values=[high_mom[-1], low_mom[-1]],
            price_series={"HIGH_MOM": high_mom, "LOW_MOM": low_mom},
        )
        results = strategy.score(pd.Index(codes), snapshot)
        scores = {r.ts_code: r.score for r in results}

        assert scores["HIGH_MOM"] > scores["LOW_MOM"], (
            f"高动量(HIGH_MOM={scores['HIGH_MOM']:.1f}) "
            f"应高于低动量(LOW_MOM={scores['LOW_MOM']:.1f})"
        )

    # MOM-02：追高剔除
    def test_mom_02_anti_chasing_top5pct_score_zero(self, strategy) -> None:
        """近 1M（20 交易日）涨幅排名全市场前 5% 的股票，momentum_score 强制为 0。"""
        # 构造 21 只股票，前 20 只涨幅普通（1%~20%），第 21 只近1M暴涨 95%
        codes = [f"S{i:02d}" for i in range(21)]
        price_series = {}
        for i, code in enumerate(codes[:-1]):
            return_pct = 0.01 * (i + 1)  # 1%~20%
            price_series[code] = (
                [100.0] * 110 + [100.0 * (1 + return_pct / 20 * j) for j in range(1, 21)]
            )
        # 最后一只近1M暴涨95%（远超其他所有，进入前5%）
        chaser = codes[-1]
        price_series[chaser] = [100.0] * 110 + [100.0 * (1 + 0.95 / 20 * j) for j in range(1, 21)]

        close_vals = [price_series[c][-1] for c in codes]
        snapshot = _make_snapshot_for_strategies(
            codes, close_values=close_vals, price_series=price_series
        )
        results = strategy.score(pd.Index(codes), snapshot)
        scores = {r.ts_code: r.score for r in results}

        assert scores[chaser] == 0.0, (
            f"近1M涨幅前5%的股票 {chaser} 得分应为0，实际 {scores[chaser]}"
        )
        # 其他股票不应全部为0
        other_scores = [scores[c] for c in codes[:-1] if c in scores]
        assert any(s > 0 for s in other_scores), "普通股票也被置0，追高剔除逻辑有误"

    # MOM-03：TD-3 未修复时行业相对强度降级
    def test_mom_03_td3_placeholder_industry_rs(self, strategy) -> None:
        """sw_industry_l1 为占位值时，industry_rs 置 50（中性），不抛异常。"""
        codes = ["A", "B"]
        n = 130
        snapshot = _make_snapshot_for_strategies(
            codes,
            close_values=[110.0, 90.0],
            price_series={"A": [100.0 + i * 0.1 for i in range(n)],
                          "B": [100.0 - i * 0.1 for i in range(n)]},
            sw_industry=["SW占位", "SW占位"],   # 非真实申万行业
        )
        # 不应抛异常，能正常返回结果
        results = strategy.score(pd.Index(codes), snapshot)
        assert len(results) == 2
        for r in results:
            assert 0 <= r.score <= 100


# ── ValueStrategy 测试 ────────────────────────────────────────────────────────

class TestValueStrategy:
    @pytest.fixture
    def strategy(self):
        from quantpilot.engine.strategies.value import ValueStrategy
        return ValueStrategy()

    # VAL-01：PE 历史低分位得分高
    def test_val_01_low_pe_percentile_wins(self, strategy) -> None:
        """当前 PE 处于历史低位（低分位）时，得分高于处于历史高位的股票。"""
        codes = ["LOW_PE", "HIGH_PE"]
        close_vals = [20.0, 20.0]

        # LOW_PE：当前 pe=10，历史 pe 在 15~30 → 当前处于历史低位
        # HIGH_PE：当前 pe=30，历史 pe 在 8~20 → 当前处于历史高位
        base_date = date(2025, 1, 31)
        history_dates = pd.date_range(end=base_date, periods=5 * 250, freq="B").date.tolist()
        tuples = [(c, d) for c in codes for d in history_dates]
        mi = pd.MultiIndex.from_tuples(tuples, names=["ts_code", "trade_date"])
        pe_hist = {
            "LOW_PE": [15.0 + 15.0 * i / (5 * 250) for i in range(5 * 250)],   # 15~30
            "HIGH_PE": [8.0 + 12.0 * i / (5 * 250) for i in range(5 * 250)],   # 8~20
        }
        n_hist = len(history_dates)
        pe_ttm_vals = [pe_hist[c][i % n_hist] for i, (c, _) in enumerate(tuples)]

        pe_pb_history = pd.DataFrame(
            {"pe_ttm": pe_ttm_vals, "pb": [2.0] * len(tuples)},
            index=mi,
        )

        idx = pd.Index(codes, name="ts_code")
        snapshot: MarketSnapshot = {
            "trade_date": base_date,
            "adj_prices": pd.DataFrame({base_date: close_vals}, index=idx),
            "daily_quotes": pd.DataFrame(
                {"close": close_vals, "pe_ttm": [10.0, 30.0], "pb": [1.5, 3.0],
                 "amount": [1e7, 1e7], "vol": [1e4, 1e4], "limit_up": [False, False]},
                index=idx,
            ),
            "financials": pd.DataFrame(
                {"roe": [12.0, 12.0], "net_profit_yoy": [5.0, 5.0],
                 "debt_to_asset": [0.4, 0.4], "sw_industry_l1": ["制造", "制造"]},
                index=idx,
            ),
            "pe_pb_history": pe_pb_history,
            "index_adj_prices": pd.DataFrame(),
        }
        results = strategy.score(pd.Index(codes), snapshot)
        scores = {r.ts_code: r.score for r in results}

        assert scores["LOW_PE"] > scores["HIGH_PE"], (
            f"低PE分位(LOW_PE={scores['LOW_PE']:.1f}) "
            f"应高于高PE分位(HIGH_PE={scores['HIGH_PE']:.1f})"
        )

    # VAL-02：价值陷阱截断
    def test_val_02_value_trap_capped_at_50(self, strategy) -> None:
        """ROE < 行业中位数 ROE 时，最终得分 ≤ 50。"""
        codes = ["TRAP", "OK"]
        # TRAP：PE 低（高分）但 ROE 低于行业中值 → 截断到 ≤ 50
        # OK：PE 低且 ROE 高于行业中值 → 不截断
        base_date = date(2025, 1, 31)
        idx = pd.Index(codes, name="ts_code")
        history_dates = pd.date_range(end=base_date, periods=5 * 250, freq="B").date.tolist()
        tuples = [(c, d) for c in codes for d in history_dates]
        mi = pd.MultiIndex.from_tuples(tuples, names=["ts_code", "trade_date"])
        pe_pb_history = pd.DataFrame(
            {"pe_ttm": [20.0] * len(tuples), "pb": [2.0] * len(tuples)},
            index=mi,
        )

        snapshot: MarketSnapshot = {
            "trade_date": base_date,
            "adj_prices": pd.DataFrame({base_date: [10.0, 10.0]}, index=idx),
            "daily_quotes": pd.DataFrame(
                {"close": [10.0, 10.0], "pe_ttm": [5.0, 5.0], "pb": [0.5, 0.5],
                 "amount": [1e7, 1e7], "vol": [1e4, 1e4], "limit_up": [False, False]},
                index=idx,
            ),
            "financials": pd.DataFrame(
                {
                    "roe": [2.0, 20.0],             # TRAP=2%（低于行业中值15%），OK=20%（高于）
                    "net_profit_yoy": [5.0, 5.0],
                    "debt_to_asset": [0.4, 0.4],
                    "sw_industry_l1": ["制造", "制造"],  # 同一行业，行业中位 = (2+20)/2=11%
                },
                index=idx,
            ),
            "pe_pb_history": pe_pb_history,
            "index_adj_prices": pd.DataFrame(),
        }
        results = strategy.score(pd.Index(codes), snapshot)
        scores = {r.ts_code: r.score for r in results}

        assert scores["TRAP"] <= 50.0, (
            f"价值陷阱 TRAP 得分应≤50，实际 {scores['TRAP']}"
        )
        assert scores["OK"] > scores["TRAP"], (
            f"OK({scores['OK']:.1f}) 应高于 TRAP({scores['TRAP']:.1f})"
        )

    # VAL-03：TD-1 未修复时 ROE 权重降级
    def test_val_03_td1_roe_null_weight_redistribution(self, strategy) -> None:
        """roe=NaN 时跳过 roe_quality 因子，权重按比例重分配给 pe/pb，不抛异常。"""
        codes = ["A", "B"]
        base_date = date(2025, 1, 31)
        idx = pd.Index(codes, name="ts_code")
        history_dates = pd.date_range(end=base_date, periods=5 * 250, freq="B").date.tolist()
        tuples = [(c, d) for c in codes for d in history_dates]
        mi = pd.MultiIndex.from_tuples(tuples, names=["ts_code", "trade_date"])
        pe_pb_history = pd.DataFrame(
            {"pe_ttm": [20.0] * len(tuples), "pb": [2.0] * len(tuples)},
            index=mi,
        )

        snapshot: MarketSnapshot = {
            "trade_date": base_date,
            "adj_prices": pd.DataFrame({base_date: [10.0, 20.0]}, index=idx),
            "daily_quotes": pd.DataFrame(
                {"close": [10.0, 20.0], "pe_ttm": [10.0, 30.0], "pb": [1.0, 3.0],
                 "amount": [1e7, 1e7], "vol": [1e4, 1e4], "limit_up": [False, False]},
                index=idx,
            ),
            "financials": pd.DataFrame(
                {
                    "roe": [float("nan"), float("nan")],  # TD-1 未修复 → 全为 NaN
                    "net_profit_yoy": [5.0, 5.0],
                    "debt_to_asset": [0.4, 0.4],
                    "sw_industry_l1": ["制造", "制造"],
                },
                index=idx,
            ),
            "pe_pb_history": pe_pb_history,
            "index_adj_prices": pd.DataFrame(),
        }
        results = strategy.score(pd.Index(codes), snapshot)

        assert len(results) == 2
        for r in results:
            assert 0 <= r.score <= 100
        # A 的 PE=10 低于 B 的 PE=30，A 应得分更高
        score_map = {r.ts_code: r.score for r in results}
        assert score_map["A"] > score_map["B"], "低PE股票得分应高于高PE股票"
