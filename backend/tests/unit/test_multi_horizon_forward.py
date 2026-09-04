"""K-3：多前向窗口（5/10/20/40 交易日）解析。V1.5-K §1.2。

## K-3 缺的不是「再写一遍 IC」

`compute_factor_ic` / `compute_decile_forward_return` 都已接受 `horizon` 参数，
`compute_forward_returns` 也已存在。缺的是**把 horizon 解析成 end_date** 这一步，
以及——更要紧的——**把「这个 horizon 算不出来」与「算出来是空」区分开**。

## 两种「不可用」必须可区分

| 情形 | 现有行为 | 若不区分会被读成 |
|---|---|---|
| 日历没有第 N 个交易日 | `get_next_trade_date` 抛 `ValueError` | 整个面板批次崩掉 |
| 日历有、行情数据没到那天 | `compute_forward_returns` 静默返回空 | 「该 horizon 没信号」 |

第二种是常态而非例外：`trade_calendar` 表比 `daily_quote` 多 **+90 天前瞻**
（`bootstrap_trade_calendar` 的 fill_end），所以窗口末端附近，日历给得出日期、
价格却还不存在。此时若只返回一个空 Series，下游看到的是「h=40 这天没有观测」，
与「h=40 这天因子确实无预测力」**完全无法分辨**。

这正是 §4.11 元判据的应用：**一个判据若在两种情况下给出相同结果，它就不是判据。**
故本模块显式返回「不可用 horizon → 原因」的映射。

## h=20 必须与生产逐字一致

生产 `backfill_daily_ic.py` 用 `calendar.get_next_trade_date(td, 20)`。
K-3 的 h=20 若与之有一丁点出入，跨 horizon 曲线就会在 20 这一点上出现口径断裂，
而那个断裂看起来会像「20 日窗口特别好/特别差」的真实发现。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from quantpilot.data.calendar import TradingCalendar
from quantpilot.engine.diagnostics.multi_horizon import (
    HORIZONS,
    resolve_forward_returns,
)

_CODES = ["A.SZ", "B.SZ", "C.SZ"]


def _calendar(n_days: int, start: date = date(2026, 1, 5)) -> TradingCalendar:
    """生成 n_days 个工作日（跳周末——交易日序列不含周末，§4.11）。"""
    days: list[date] = []
    d = start
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return TradingCalendar(days)


def _prices(cal: TradingCalendar, n_days: int) -> pd.DataFrame:
    """index=ts_code，columns=trade_date，后复权价逐日 +1%。"""
    dates = cal.get_trade_dates(date(2020, 1, 1), date(2030, 1, 1))[:n_days]
    data = {d: [10.0 * (1.01 ** i)] * len(_CODES) for i, d in enumerate(dates)}
    return pd.DataFrame(data, index=_CODES)


class TestMatchesProductionConvention:
    def test_h20_end_date_equals_backfill_daily_ic(self) -> None:
        """与 `backfill_daily_ic.py` 的 `get_next_trade_date(td, 20)` 逐字一致。

        差一天都会让跨 horizon 曲线在 20 这点出现口径断裂，
        而断裂看起来会像一个真实发现。
        """
        cal = _calendar(120)
        base = cal.get_trade_dates(date(2020, 1, 1), date(2030, 1, 1))[10]
        avail, _ = resolve_forward_returns(
            _prices(cal, 120), cal, base, horizons=(20,)
        )
        assert len(avail) == 1
        assert avail[0].end_date == cal.get_next_trade_date(base, 20)

    def test_default_horizons_are_5_10_20_40(self) -> None:
        assert HORIZONS == (5, 10, 20, 40)


class TestMultiHorizon:
    def test_each_horizon_gets_its_own_end_date_and_returns(self) -> None:
        """窗口越长收益越大（逐日 +1% 的构造下可手算）。"""
        cal = _calendar(200)
        base = cal.get_trade_dates(date(2020, 1, 1), date(2030, 1, 1))[10]
        avail, missing = resolve_forward_returns(_prices(cal, 200), cal, base)
        assert missing == {}
        assert [h.horizon for h in avail] == [5, 10, 20, 40]
        for h in avail:
            # 10*(1.01^(10+n)) / 10*(1.01^10) - 1 = 1.01^n - 1
            assert h.returns["A.SZ"] == pytest.approx(1.01 ** h.horizon - 1)

    def test_horizons_parameter_is_consumed(self) -> None:
        """改 horizons → 结果必须变（§4.11「参数是否真被消费」）。"""
        cal = _calendar(200)
        base = cal.get_trade_dates(date(2020, 1, 1), date(2030, 1, 1))[10]
        px = _prices(cal, 200)
        got = {
            hs: tuple(h.horizon for h in resolve_forward_returns(px, cal, base, horizons=hs)[0])
            for hs in ((5,), (5, 10), (10, 40))
        }
        assert got == {(5,): (5,), (5, 10): (5, 10), (10, 40): (10, 40)}


class TestUnavailableIsDistinguishable:
    """核心：两种「不可用」互不相同，且都**不同于**「算出来是空」。"""

    def test_calendar_too_short_reports_reason_not_raise(self) -> None:
        """日历不够长 → 记原因并跳过，**不得让整个面板批次崩掉**。

        `get_next_trade_date` 本身抛 ValueError（大声失败，方向对），
        但面板要跑 1244 个交易日，末端几天必然触发——不能因此中断整批。
        """
        cal = _calendar(30)
        base = cal.get_trade_dates(date(2020, 1, 1), date(2030, 1, 1))[10]
        avail, missing = resolve_forward_returns(_prices(cal, 30), cal, base)
        assert [h.horizon for h in avail] == [5, 10]
        assert set(missing) == {20, 40}
        assert all("日历" in r for r in missing.values()), missing

    def test_price_data_short_reports_a_different_reason(self) -> None:
        """日历有该日、价格数据没到 → **另一种**原因，不能与上一种混同。

        这是常态：`trade_calendar` 比 `daily_quote` 多 +90 天前瞻。
        """
        cal = _calendar(200)                      # 日历很长
        base = cal.get_trade_dates(date(2020, 1, 1), date(2030, 1, 1))[10]
        px = _prices(cal, 25)                     # 价格只到第 25 天
        avail, missing = resolve_forward_returns(px, cal, base)
        assert [h.horizon for h in avail] == [5, 10]
        assert set(missing) == {20, 40}
        assert all("行情" in r for r in missing.values()), missing
        # 两种原因文案必须不同——否则分析侧无从判断该补日历还是该补数据
        cal_short, missing_cal = resolve_forward_returns(
            _prices(_calendar(30), 30), _calendar(30),
            _calendar(30).get_trade_dates(date(2020, 1, 1), date(2030, 1, 1))[10],
        )
        assert missing[40] != missing_cal[40]

    def test_empty_result_is_not_reported_as_missing(self) -> None:
        """数据齐全但收益全 NaN → 属「算出来是空」，**不进 missing**。

        把它记成 missing 会让「因子无预测力」伪装成「数据不可用」。
        """
        cal = _calendar(200)
        base = cal.get_trade_dates(date(2020, 1, 1), date(2030, 1, 1))[10]
        px = _prices(cal, 200).astype(float)
        px.loc[:, :] = float("nan")
        avail, missing = resolve_forward_returns(px, cal, base)
        assert missing == {}, "数据可达就不算 missing"
        assert all(h.returns.empty or h.returns.isna().all() for h in avail)


class TestExcludedPropagates:
    def test_excluded_codes_removed_from_every_horizon(self) -> None:
        """涨跌停/停牌剔除须对每个 horizon 都生效，不能只在 h=20 上做。"""
        cal = _calendar(200)
        base = cal.get_trade_dates(date(2020, 1, 1), date(2030, 1, 1))[10]
        avail, _ = resolve_forward_returns(
            _prices(cal, 200), cal, base, excluded={"B.SZ"}
        )
        assert avail, "应有可用 horizon"
        for h in avail:
            assert "B.SZ" not in h.returns.index, f"h={h.horizon} 未剔除 B.SZ"
