"""RSK-01~04: RiskChecker 单元测试（纯函数，无 DB）。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from quantpilot.engine.risk import RiskChecker
from quantpilot.engine.signal import TradeSignal
from quantpilot.models.account import Position

TRADE_DATE = date(2026, 4, 8)


def _make_buy_signal(ts_code: str, suggested_pct: float = 0.10) -> TradeSignal:
    return TradeSignal(
        ts_code=ts_code,
        signal_type="BUY",
        trade_date=TRADE_DATE,
        score=85.0,
        suggested_pct=suggested_pct,
    )


def _make_position(ts_code: str, *, market_value: float) -> Position:
    p = MagicMock(spec=Position)
    p.ts_code = ts_code
    p.market_value = market_value
    return p


def _make_industry_df(ts_codes: list[str], industries: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"sw_industry_l1": industries},
        index=pd.Index(ts_codes, name="ts_code"),
    )


checker = RiskChecker()


# ---------------------------------------------------------------------------
# RSK-01: 买入后单股超过20%上限 → CONCENTRATION_STOCK BLOCK
# ---------------------------------------------------------------------------
def test_rsk_01_stock_concentration_block() -> None:
    """RSK-01: 买入后 000001.SZ 占比 22% > 20% → CONCENTRATION_STOCK BLOCK"""
    # 总资产 100,000，建议仓位 10% = 10,000；现有持仓 12,000 → 合计 22,000 = 22%
    signal = _make_buy_signal("000001.SZ", suggested_pct=0.10)
    existing = _make_position("000001.SZ", market_value=12_000.0)
    industry_df = _make_industry_df(["000001.SZ"], ["银行"])

    warnings = checker.check(
        signals=[signal],
        current_positions=[existing],
        account_total_assets=100_000.0,
        stock_industry=industry_df,
        max_single_stock_pct=0.20,
    )

    assert len(warnings) >= 1
    w = next(w for w in warnings if w.warning_type == "CONCENTRATION_STOCK")
    assert w.severity == "BLOCK"
    assert w.ts_code == "000001.SZ"


# ---------------------------------------------------------------------------
# RSK-02: 同行业持仓已 28%，买入后超 30% → CONCENTRATION_INDUSTRY BLOCK
# ---------------------------------------------------------------------------
def test_rsk_02_industry_concentration_block() -> None:
    """RSK-02: 行业持仓 28%，买入后 32% > 30% → CONCENTRATION_INDUSTRY BLOCK"""
    # 总资产 100,000，建议仓位 10% = 10,000
    # 同行业已有 28,000 = 28% → 买入后 38,000 = 38% > 30%
    signal = _make_buy_signal("000003.SZ", suggested_pct=0.10)
    existing1 = _make_position("000001.SZ", market_value=14_000.0)
    existing2 = _make_position("000002.SZ", market_value=14_000.0)
    industry_df = _make_industry_df(
        ["000001.SZ", "000002.SZ", "000003.SZ"],
        ["电子", "电子", "电子"],
    )

    warnings = checker.check(
        signals=[signal],
        current_positions=[existing1, existing2],
        account_total_assets=100_000.0,
        stock_industry=industry_df,
        max_industry_pct=0.30,
    )

    assert len(warnings) >= 1
    w = next(w for w in warnings if w.warning_type == "CONCENTRATION_INDUSTRY")
    assert w.severity == "BLOCK"
    assert w.ts_code == "000003.SZ"


# ---------------------------------------------------------------------------
# RSK-03: 账户最大回撤 22% > 阈值 20% → DRAWDOWN WARN
# ---------------------------------------------------------------------------
def test_rsk_03_drawdown_warn() -> None:
    """RSK-03: account_max_drawdown_pct=0.22 > max_drawdown_pct=0.20 → DRAWDOWN WARN"""
    signal = _make_buy_signal("000001.SZ", suggested_pct=0.05)  # 不触发集中度
    industry_df = _make_industry_df(["000001.SZ"], ["医药生物"])

    warnings = checker.check(
        signals=[signal],
        current_positions=[],
        account_total_assets=100_000.0,
        stock_industry=industry_df,
        account_max_drawdown_pct=0.22,
        max_drawdown_pct=0.20,
    )

    drawdown_warnings = [w for w in warnings if w.warning_type == "DRAWDOWN"]
    assert len(drawdown_warnings) == 1
    w = drawdown_warnings[0]
    assert w.severity == "WARN"
    assert w.ts_code == "ACCOUNT"


# ---------------------------------------------------------------------------
# RSK-04: 无超标，account_max_drawdown_pct=None → 返回空列表
# ---------------------------------------------------------------------------
def test_rsk_04_no_warning_when_within_limits() -> None:
    """RSK-04: 所有指标在阈值内，account_max_drawdown_pct=None → 无告警"""
    signal = _make_buy_signal("000001.SZ", suggested_pct=0.05)
    industry_df = _make_industry_df(["000001.SZ"], ["计算机"])

    warnings = checker.check(
        signals=[signal],
        current_positions=[],
        account_total_assets=100_000.0,
        stock_industry=industry_df,
        account_max_drawdown_pct=None,
    )

    assert len(warnings) == 0
