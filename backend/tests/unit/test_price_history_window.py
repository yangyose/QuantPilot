"""V1.5-C C1-3：价格历史窗口必须按**交易日**而非日历天推导。

背景（2026-08-24 跑 C1 IC 面板对比时抓到）：
`ScoringService` 用 `_PRICE_WINDOW_DAYS = 180` 日历天取后复权价格窗口，注释写
「≈ 120 交易日（覆盖 MomentumStrategy 6M 窗口）」。实测 2026-07-17 往前 180 日
历天只有 **119 个交易日**，而 `_period_return(adj_prices, 120)` 要求 `shape[1] > 120`
（≥ 121 列）→ `rs_6m` **恒为全 NaN**。该常数自 Initial commit 起就在，意味着
momentum 三个因子里权重 0.35 的那个在生产每一次评分中都没参与过。

同一根因还有第二个静默降级：`index_adj_prices` 列数不足 `lookback_long + 1` 时，
`idx_return_6m` 回落 0.0 → `rs_6m` 退化成绝对收益，不再是「相对沪深300」。

CLAUDE.md §4.4 的 `calendar_days = int(history_days * 1.5)` 经验式在这里恰好不够：
120 × 1.5 = 180，而 A 股一年约 250 交易日 → 真实系数 365/250 ≈ 1.46，再叠上春节 /
国庆的假期聚集，120 交易日需要的日历天数会越过 180。修法不是把系数调大（下一次
窗口变深还会再撞），而是**用 TradingCalendar 精确回退 N 个交易日**。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import pytest

from quantpilot.core.config_defaults import MomentumStrategyConfig
from quantpilot.data.calendar import TradingCalendar
from quantpilot.engine.strategies.base import MarketSnapshot
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.momentum import MomentumStrategy
from quantpilot.engine.strategies.trend import TrendStrategy
from quantpilot.engine.strategies.value import ValueStrategy
from quantpilot.services.strategy_service import (
    PRICE_WINDOW_SLACK_DAYS,
    resolve_price_window_start,
)

_TRADE_DATE = date(2026, 7, 17)


# ============================================================
# 构造工具
# ============================================================

def _weekdays(start: date, end: date) -> list[date]:
    """[start, end] 之间的全部工作日（升序）。"""
    days: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _calendar_with_exact_span(
    trade_date: date,
    calendar_days: int,
    trading_days: int,
) -> TradingCalendar:
    """构造一份日历：`trade_date` 往前 `calendar_days` 日历天内恰好 `trading_days`
    个交易日。

    用于把真实观察到的场景（180 日历天 = 119 交易日）钉成确定性用例——直接用
    工作日日历复现不出来（180 天里有 ~128 个工作日），必须显式扣掉假期。
    """
    window_start = trade_date - timedelta(days=calendar_days)
    # 更早的历史给足，保证「按交易日回退」这条路径有料可取
    early = _weekdays(window_start - timedelta(days=900), window_start - timedelta(days=1))
    inside = _weekdays(window_start, trade_date)
    assert len(inside) >= trading_days, "工作日不足，无法扣成目标交易日数"
    # 从窗口**前段**扣假期：保留最近的交易日不动，贴近 A 股假期分布
    holidays = len(inside) - trading_days
    inside = inside[holidays:]
    assert len(inside) == trading_days
    return TradingCalendar(early + inside)


def _snapshot_with_n_columns(codes: list[str], n_cols: int) -> MarketSnapshot:
    """构造列数精确为 `n_cols` 的 adj_prices / index_adj_prices 快照。

    价格逐日递增且各股票斜率不同 —— 保证 return 有横截面离散度，不会被
    「全体相同」的退化护栏拦掉。
    """
    cols = [date(2026, 1, 1) + timedelta(days=i) for i in range(n_cols)]
    adj_prices = pd.DataFrame(
        {
            col: [100.0 * (1.0 + (0.001 * (j + 1)) * i) for j in range(len(codes))]
            for i, col in enumerate(cols)
        },
        index=pd.Index(codes, name="ts_code"),
    )
    index_adj_prices = pd.DataFrame(
        {col: [3000.0 * (1.0 + 0.0005 * i)] for i, col in enumerate(cols)},
        index=pd.Index(["000300.SH"], name="index_code"),
    )
    financials = pd.DataFrame(
        {"sw_industry_l1": ["电子"] * len(codes), "roe": [10.0] * len(codes)},
        index=pd.Index(codes, name="ts_code"),
    )
    return {
        "trade_date": _TRADE_DATE,
        "adj_prices": adj_prices,
        "index_adj_prices": index_adj_prices,
        "financials": financials,
    }


# ============================================================
# UT-C1-08：策略自报所需交易日数（required_history_days 契约）
# ============================================================

def test_ut_c1_08a_momentum_requires_lookback_long_plus_one() -> None:
    """UT-C1-08a: momentum 所需交易日数 = lookback_long + 1（_period_return 要 > n）。"""
    strategy = MomentumStrategy()
    assert strategy.required_history_days == strategy._cfg.lookback_long + 1


def test_ut_c1_08b_required_days_follows_config() -> None:
    """UT-C1-08b: 改 lookback_long → 所需交易日数随之变（防再次写死）。"""
    deep = MomentumStrategy(MomentumStrategyConfig(lookback_long=200))
    assert deep.required_history_days == 201


def test_ut_c1_08c_exactly_required_columns_yields_valid_rs_6m() -> None:
    """UT-C1-08c: 恰好给足 required_history_days 列 → rs_6m 全部有效。

    这是本批的锚点：它把「窗口够不够」变成可证伪的断言。
    """
    strategy = MomentumStrategy()
    codes = ["AAA", "BBB", "CCC"]
    snapshot = _snapshot_with_n_columns(codes, strategy.required_history_days)

    factors = strategy.compute_raw_factors(pd.Index(codes), snapshot)

    assert "rs_6m" in factors.columns
    assert factors["rs_6m"].notna().all(), (
        f"给足 {strategy.required_history_days} 列后 rs_6m 仍有 NaN：\n{factors['rs_6m']}"
    )


def test_ut_c1_08d_one_column_short_yields_all_nan_rs_6m() -> None:
    """UT-C1-08d: 少一列 → rs_6m 全 NaN。

    钉住边界本身：证明 required_history_days 不是随手取的富余值，而是真实临界点。
    少了这条，08c 里把 required 调大 10 倍也能过。
    """
    strategy = MomentumStrategy()
    codes = ["AAA", "BBB", "CCC"]
    snapshot = _snapshot_with_n_columns(codes, strategy.required_history_days - 1)

    factors = strategy.compute_raw_factors(pd.Index(codes), snapshot)

    assert factors["rs_6m"].isna().all()


def test_ut_c1_08e_exactly_required_columns_keeps_relative_to_benchmark() -> None:
    """UT-C1-08e: 给足列数时 rs_6m 必须真的减掉了指数收益，而非回落 idx=0.0。

    第二个静默降级：index_adj_prices 列数不足时 idx_return_6m = 0.0，rs_6m 退化
    成绝对收益。列数够了就不该再走该分支。
    """
    strategy = MomentumStrategy()
    codes = ["AAA", "BBB"]
    n = strategy.required_history_days
    snapshot = _snapshot_with_n_columns(codes, n)

    factors = strategy.compute_raw_factors(pd.Index(codes), snapshot)

    idx = snapshot["index_adj_prices"]
    cols = sorted(idx.columns)
    idx_return = float(idx[cols[-1]].mean()) / float(idx[cols[-(n)]].mean()) - 1.0
    assert idx_return > 0, "构造的指数应上涨，否则本用例证明不了任何事"

    prices = snapshot["adj_prices"]
    abs_return = prices[cols[-1]] / prices[cols[-(n)]] - 1.0
    assert factors["rs_6m"].sub(abs_return - idx_return).abs().max() < 1e-9


@pytest.mark.parametrize(
    "strategy",
    [MomentumStrategy(), TrendStrategy(), MeanReversionStrategy(), ValueStrategy()],
    ids=["momentum", "trend", "mean_reversion", "value"],
)
def test_ut_c1_08f_every_strategy_declares_history_need(strategy: object) -> None:
    """UT-C1-08f: 四个策略都要自报所需交易日数，且 momentum 是最深的那个。"""
    need = strategy.required_history_days  # type: ignore[attr-defined]
    assert isinstance(need, int) and need > 0
    assert need <= MomentumStrategy().required_history_days


# ============================================================
# UT-C1-09：窗口起点按交易日精确回退
# ============================================================

def test_ut_c1_09a_resolver_guarantees_required_trading_days() -> None:
    """UT-C1-09a: 解析出的起点，到 trade_date 之间的交易日数必须 ≥ 所需。"""
    calendar = _calendar_with_exact_span(_TRADE_DATE, calendar_days=180, trading_days=119)
    required = MomentumStrategy().required_history_days

    start = resolve_price_window_start(_TRADE_DATE, required, calendar)

    assert calendar.count_trade_days(start, _TRADE_DATE) >= required


def test_ut_c1_09b_legacy_180_calendar_days_was_insufficient() -> None:
    """UT-C1-09b: 回归钉子——180 日历天在真实日历下不足 121 交易日，新解析器足。

    这条把生产缺陷本身写成断言：任何把窗口改回「日历天近似」的改动都会红。
    """
    calendar = _calendar_with_exact_span(_TRADE_DATE, calendar_days=180, trading_days=119)
    required = MomentumStrategy().required_history_days

    legacy_start = _TRADE_DATE - timedelta(days=180)
    assert calendar.count_trade_days(legacy_start, _TRADE_DATE) < required

    fixed_start = resolve_price_window_start(_TRADE_DATE, required, calendar)
    assert calendar.count_trade_days(fixed_start, _TRADE_DATE) >= required


def test_ut_c1_09c_resolver_is_tight_not_wasteful() -> None:
    """UT-C1-09c: 起点恰好是 required + slack 个交易日，不多不少。

    窗口是全 universe × 全列的批量查询，起点每多退一天就是几千行 IO；而少一天
    就回到 rs_6m 全 NaN。用 `==` 而非 `<=` 上界钉死，off-by-one 才跑不掉。
    """
    calendar = _calendar_with_exact_span(_TRADE_DATE, calendar_days=180, trading_days=119)
    required = MomentumStrategy().required_history_days

    start = resolve_price_window_start(_TRADE_DATE, required, calendar)

    assert calendar.count_trade_days(start, _TRADE_DATE) == required + PRICE_WINDOW_SLACK_DAYS


def test_ut_c1_09d_short_calendar_falls_back_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-C1-09d: 日历深度不足时降级为日历天近似，且必须 WARNING 出声。

    CLAUDE.md C-4：降级可以，静默不行。早期历史 / 回填脚本会走到这条路径。
    """
    shallow = TradingCalendar(_weekdays(_TRADE_DATE - timedelta(days=30), _TRADE_DATE))
    required = MomentumStrategy().required_history_days

    with caplog.at_level(logging.WARNING):
        start = resolve_price_window_start(_TRADE_DATE, required, shallow)

    assert start < _TRADE_DATE
    assert any(
        "price_window" in record.message for record in caplog.records
    ), f"降级未告警，实际日志：{[r.message for r in caplog.records]}"
