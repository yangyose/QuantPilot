"""SGN-01~10: SignalGenerator 单元测试（纯函数，无 DB）。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.signal import RiskParams, SignalGenerator
from quantpilot.models.account import Position

TRADE_DATE = date(2026, 4, 8)
DEFAULT_PARAMS = RiskParams()


def _make_scores(ts_codes: list[str], score: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "composite_score": [score] * len(ts_codes),
            "trend_score": [score] * len(ts_codes),
            "momentum_score": [score] * len(ts_codes),
            "reversion_score": [score] * len(ts_codes),
            "value_score": [score] * len(ts_codes),
        },
        index=pd.Index(ts_codes, name="ts_code"),
    )


def _make_quotes(
    ts_codes: list[str],
    *,
    close: float = 10.0,
    is_suspended: bool = False,
    limit_up: bool = False,
    avg_amount: float = 10_000_000.0,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [close] * len(ts_codes),
            "is_suspended": [is_suspended] * len(ts_codes),
            "limit_up": [limit_up] * len(ts_codes),
            "avg_amount": [avg_amount] * len(ts_codes),
        },
        index=pd.Index(ts_codes, name="ts_code"),
    )


def _make_position(
    ts_code: str,
    *,
    pnl_pct: float = 0.0,
    cost_price: float = 10.0,
    market_value: float = 10_000.0,
) -> Position:
    p = MagicMock(spec=Position)
    p.ts_code = ts_code
    p.pnl_pct = pnl_pct
    p.cost_price = cost_price
    p.current_price = cost_price * (1 + pnl_pct)
    p.market_value = market_value
    return p


gen = SignalGenerator()


# ---------------------------------------------------------------------------
# SGN-01: 评分>80、非停牌、非涨停、无持仓 → BUY 信号
# ---------------------------------------------------------------------------
def test_sgn_01_buy_signal_basic() -> None:
    """SGN-01: 评分>80，条件满足，无持仓 → BUY 信号，price_low=close×0.99"""
    scores = _make_scores(["000001.SZ"], 85.0)
    quotes = _make_quotes(["000001.SZ"], close=10.0)
    signals = gen.generate(scores, [], MarketStateEnum.UPTREND, quotes, TRADE_DATE)

    assert len(signals) == 1
    s = signals[0]
    assert s.signal_type == "BUY"
    assert s.ts_code == "000001.SZ"
    assert s.trade_date == TRADE_DATE
    assert s.score == pytest.approx(85.0)
    assert s.suggested_price_low == pytest.approx(10.0 * 0.99)
    assert s.suggested_price_high == pytest.approx(10.0 * 1.02)
    assert s.t1_warning is not None and "T+1" in s.t1_warning


# ---------------------------------------------------------------------------
# SGN-02: 评分≤80 → 无 BUY 信号
# ---------------------------------------------------------------------------
def test_sgn_02_below_threshold_no_signal() -> None:
    """SGN-02: 评分=80（恰等于阈值，不超过）→ 无信号"""
    scores = _make_scores(["000001.SZ"], 80.0)
    quotes = _make_quotes(["000001.SZ"])
    signals = gen.generate(scores, [], MarketStateEnum.UPTREND, quotes, TRADE_DATE)
    assert len(signals) == 0


# ---------------------------------------------------------------------------
# SGN-03: 涨停（limit_up=True）→ 无 BUY
# ---------------------------------------------------------------------------
def test_sgn_03_limit_up_no_buy() -> None:
    """SGN-03: 涨停封死 → 不产生买入信号"""
    scores = _make_scores(["000001.SZ"], 90.0)
    quotes = _make_quotes(["000001.SZ"], limit_up=True)
    signals = gen.generate(scores, [], MarketStateEnum.UPTREND, quotes, TRADE_DATE)
    buy_signals = [s for s in signals if s.signal_type == "BUY"]
    assert len(buy_signals) == 0


# ---------------------------------------------------------------------------
# SGN-04: 已持仓且盈利（pnl_pct=0.05）→ 生成加仓 BUY
# ---------------------------------------------------------------------------
def test_sgn_04_add_position_when_profitable() -> None:
    """SGN-04: 持仓盈利 5% + 评分 > 80 → 允许加仓"""
    scores = _make_scores(["000001.SZ"], 85.0)
    quotes = _make_quotes(["000001.SZ"], close=10.5)
    position = _make_position("000001.SZ", pnl_pct=0.05, cost_price=10.0)
    signals = gen.generate(scores, [position], MarketStateEnum.UPTREND, quotes, TRADE_DATE)
    buy_signals = [s for s in signals if s.signal_type == "BUY"]
    assert len(buy_signals) == 1


# ---------------------------------------------------------------------------
# SGN-05: 已持仓价格偏离>10% 且下跌趋势 → 不加仓
# ---------------------------------------------------------------------------
def test_sgn_05_no_add_when_deviation_exceeds_in_downtrend() -> None:
    """SGN-05: 当前价偏离成本 >10% + 下跌趋势 → 不满足加仓条件"""
    scores = _make_scores(["000001.SZ"], 85.0)
    # cost=10, current=8.7 → pnl_pct=-0.13，偏离13%
    quotes = _make_quotes(["000001.SZ"], close=8.7)
    position = _make_position("000001.SZ", pnl_pct=-0.13, cost_price=10.0)
    signals = gen.generate(
        scores, [position], MarketStateEnum.DOWNTREND, quotes, TRADE_DATE
    )
    buy_signals = [s for s in signals if s.signal_type == "BUY"]
    assert len(buy_signals) == 0


# ---------------------------------------------------------------------------
# SGN-06: 持仓股评分<40 → SELL 信号
# ---------------------------------------------------------------------------
def test_sgn_06_sell_when_score_below_threshold() -> None:
    """SGN-06: 持仓股评分 35 < 卖出阈值 40 → SELL 信号"""
    scores = _make_scores(["000001.SZ"], 35.0)
    quotes = _make_quotes(["000001.SZ"])
    position = _make_position("000001.SZ", pnl_pct=0.02, cost_price=10.0)
    signals = gen.generate(scores, [position], MarketStateEnum.OSCILLATION, quotes, TRADE_DATE)
    sell_signals = [s for s in signals if s.signal_type == "SELL"]
    assert len(sell_signals) == 1
    assert sell_signals[0].ts_code == "000001.SZ"


# ---------------------------------------------------------------------------
# SGN-07: 持仓浮亏≥8% → SELL（硬止损）
# ---------------------------------------------------------------------------
def test_sgn_07_stop_loss_trigger() -> None:
    """SGN-07: 浮亏恰好达 -8% → 触发硬止损，产生 SELL 信号"""
    # 评分在安全区但触发止损
    scores = _make_scores(["000001.SZ"], 65.0)
    quotes = _make_quotes(["000001.SZ"])
    position = _make_position("000001.SZ", pnl_pct=-0.08, cost_price=10.0)
    signals = gen.generate(scores, [position], MarketStateEnum.UPTREND, quotes, TRADE_DATE)
    sell_signals = [s for s in signals if s.signal_type == "SELL"]
    assert len(sell_signals) == 1
    assert "止损" in sell_signals[0].reason.lower() or "stop" in sell_signals[0].reason.lower()


# ---------------------------------------------------------------------------
# SGN-08: 评分≥90 → STRONG；80-89 → MODERATE
# ---------------------------------------------------------------------------
def test_sgn_08_signal_strength() -> None:
    """SGN-08: signal_strength 随评分正确设置"""
    # STRONG
    scores_strong = _make_scores(["000001.SZ"], 92.0)
    quotes = _make_quotes(["000001.SZ"])
    sigs_strong = gen.generate(scores_strong, [], MarketStateEnum.UPTREND, quotes, TRADE_DATE)
    assert len(sigs_strong) == 1
    assert sigs_strong[0].signal_strength == "STRONG"

    # MODERATE
    scores_moderate = _make_scores(["000002.SZ"], 85.0)
    quotes2 = _make_quotes(["000002.SZ"])
    sigs_moderate = gen.generate(scores_moderate, [], MarketStateEnum.UPTREND, quotes2, TRADE_DATE)
    assert len(sigs_moderate) == 1
    assert sigs_moderate[0].signal_strength == "MODERATE"


# ---------------------------------------------------------------------------
# SGN-09: 流动性不足（avg_amount<500万）→ 无 BUY 信号
# ---------------------------------------------------------------------------
def test_sgn_09_low_liquidity_no_buy() -> None:
    """SGN-09: 20日均成交额<500万 → 不产生买入信号"""
    scores = _make_scores(["000001.SZ"], 88.0)
    quotes = _make_quotes(["000001.SZ"], avg_amount=3_000_000.0)
    signals = gen.generate(scores, [], MarketStateEnum.UPTREND, quotes, TRADE_DATE)
    assert len(signals) == 0


# ---------------------------------------------------------------------------
# SGN-10: 持仓股评分在[40,80]区间 → 无信号（持有区间）
# ---------------------------------------------------------------------------
def test_sgn_10_hold_zone_no_signal() -> None:
    """SGN-10: 持仓股评分处于[40,80]区间 → 不触发买卖，不产生信号"""
    scores = _make_scores(["000001.SZ"], 60.0)
    quotes = _make_quotes(["000001.SZ"])
    position = _make_position("000001.SZ", pnl_pct=0.01, cost_price=10.0)
    signals = gen.generate(scores, [position], MarketStateEnum.OSCILLATION, quotes, TRADE_DATE)
    assert len(signals) == 0
