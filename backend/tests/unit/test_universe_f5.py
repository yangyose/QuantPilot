"""F-5「非连续亏损」：必须看最近 2 个**有值**的报告期，不是最近 2 个报告期。

## 缺陷

`get_latest_n_financials(n=2)` 取的是最近 2 个**报告期**。而 `financial_data` 对
**未披露**的当季报告期每天写一条基本面全 NULL 的占位行——该期照样占掉一个名额。
于是 87% 的股票只剩 1 期可用值，「连续两期亏损」退化成「最近一期同比为负就剔除」。

2026-08-25 于 5434 实测：

| 每只可用的非空 `net_profit_yoy` 期数 | 只数 |
|---|---|
| 1 期 | 4809（87%）|
| 2 期 | 701（13%）|

判为「连亏」的 2575 只里 **2393 只出自单期降级路径**，真·两期皆负仅 182 只。
而 F-5 剔掉全市场 35~44%，是选股面的绝对主导项。

⚠️ 后果不只是「偏严」，而是**口径随披露季摆动**：半年报/年报披露进行中的窗口
（8~9 月尤甚）额外压掉约 1000 只，披露完成后又放回来——同一只股票基本面没变，
却因为日历位置一会儿在选股面内、一会儿在外。

⚠️ 与 2026-07 生产事故（`repository.get_latest_n_financials` docstring 所记）
**同源而未尽**：当时修的是「同一报告期堆积多条重复行挤满 n=2 窗口」，
本次修的是「未披露报告期**本身**占掉一个名额」。同一个坑的两半。

## 修后口径

- 取最近 **2 个有值**期；两期皆负 → 剔除
- **不足 2 个有值期 → 不剔除**（数据不足时不做「疑罪从有」的推定）
- 金融股豁免不变
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from quantpilot.data.calendar import TradingCalendar
from quantpilot.engine.universe import UniverseFilter

TODAY = date(2025, 1, 31)
CODE = "000001.SZ"
# 由新到旧
P = [date(2024, 12, 31), date(2024, 9, 30), date(2024, 6, 30), date(2024, 3, 31)]


@pytest.fixture
def calendar() -> TradingCalendar:
    days, d = [], date(2019, 1, 1)
    while d <= date(2025, 2, 28):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return TradingCalendar(days)


def _hist(yoys: list[float]) -> pd.DataFrame:
    """按「由新到旧」给出各期 yoy；`float('nan')` 表示未披露占位行。"""
    rows = [
        {"ts_code": CODE, "report_period": P[i], "net_profit_yoy": y}
        for i, y in enumerate(yoys)
    ]
    return pd.DataFrame(rows).set_index(["ts_code", "report_period"])


def _run(uf: UniverseFilter, calendar: TradingCalendar, hist: pd.DataFrame) -> bool:
    """返回该股是否**通过** F-5（在 universe 里 = True）。"""
    idx = pd.Index([CODE], name="ts_code")
    info = pd.DataFrame({
        "is_st": [False], "list_date": [date(2020, 1, 1)],
        "is_suspended": [False], "sw_industry_l1": ["制造"],
    }, index=idx)
    fin = pd.DataFrame({
        "total_equity": [1e9], "net_profit_yoy": [float("nan")],
        "debt_to_asset": [0.5],
    }, index=idx)
    quotes = pd.DataFrame(
        {"amount": [1e7], "vol": [1e4], "limit_up": [False]}, index=idx
    )
    return CODE in uf.filter(info, fin, quotes, TODAY, calendar, financials_history=hist)


class TestF5UsesNewestTwoWithValues:
    def test_placeholder_newest_period_does_not_consume_a_slot(
        self, calendar: TradingCalendar
    ) -> None:
        """最新期是未披露占位（NaN）→ 应看其后两个有值期。

        这里两个有值期一负一正 → **不该剔除**。
        缺陷版本只看到 [NaN, -5] → dropna 后仅剩一负 → 误剔。
        """
        assert _run(UniverseFilter(), calendar, _hist([float("nan"), -5.0, 3.0])) is True

    def test_two_disclosed_negative_periods_are_excluded(
        self, calendar: TradingCalendar
    ) -> None:
        """最近两个有值期皆负 → 剔除（这是 F-5 本来要做的事）。"""
        assert _run(UniverseFilter(), calendar, _hist([float("nan"), -5.0, -3.0])) is False

    def test_single_available_period_does_not_exclude(
        self, calendar: TradingCalendar
    ) -> None:
        """只有 1 个有值期 → **不剔除**（数据不足不做推定）。

        这是与旧口径差异最大的一条：旧版「降级为单期」会直接剔掉，
        而生产上 87% 的股票正处于这个状态。
        """
        assert _run(UniverseFilter(), calendar, _hist([float("nan"), -5.0])) is True

    def test_order_matters_newest_two_not_any_two(
        self, calendar: TradingCalendar
    ) -> None:
        """必须取**最近**两个有值期，不是任意两个。

        ⚠️ 取数方 `get_latest_n_financials` 的 SELECT 无 ORDER BY，返回顺序不保证，
        所以判定必须自己按 report_period 排序。这条钉死它：
        最新期为正、更早两期为负——按最近两期看应通过，按「全部非空」或
        「随便两期」看则会被剔。
        """
        assert _run(UniverseFilter(), calendar, _hist([2.0, -5.0, -3.0])) is True

    def test_row_order_in_frame_does_not_change_result(
        self, calendar: TradingCalendar
    ) -> None:
        """把行顺序打乱，结论必须不变（同上条的姊妹判据）。"""
        h = _hist([2.0, -5.0, -3.0])
        assert _run(UniverseFilter(), calendar, h.iloc[::-1]) is True

    def test_all_four_negative_still_excluded(self, calendar: TradingCalendar) -> None:
        assert _run(UniverseFilter(), calendar, _hist([-1.0, -5.0, -3.0, -2.0])) is False


class TestNewestTwoIsNotAllNonNull:
    """「最近 2 个有值期」≠「全部非空值」——补足期数后两者会分道扬镳。

    ⚠️ 单独修「多取几期」而不改判定规则，会把缺陷从**过严**翻转成**过松**：
    现行规则要求全部非空值皆负，期数一多就几乎不可能全负 → F-5 近乎失效。
    这一族（改了一半、方向反转）比原缺陷更难发现，因为「数字变好看了」。
    """

    def test_older_positive_period_must_not_rescue_a_losing_stock(
        self, calendar: TradingCalendar
    ) -> None:
        """最近两个有值期皆负，但更早一期为正 → **仍须剔除**。

        「全部非空皆负」规则在此会放行（因为有个 +8），那是错的。
        """
        h = _hist([float("nan"), -5.0, -3.0, 8.0])
        assert _run(UniverseFilter(), calendar, h) is False, (
            "更早的一期盈利不该让最近两期连亏的股票通过"
        )


class TestServiceSuppliesEnoughPeriods:
    """取数方必须给够期数，否则 Engine 规则再对也拿不到两个有值期。

    ⚠️ §4.11：**只能在调用点上验**。生产 `n=2` 时，未披露占位期占掉一个名额，
    剩下至多 1 个有值期 → 永远走「不足 2 期」分支 → F-5 整条实质失效。
    这正是「改了 Engine 却没改取数」会留下的形态，且单测全绿。
    """

    def test_build_filter_snapshot_requests_enough_periods(self) -> None:
        import ast
        import inspect

        from quantpilot.services.strategy_service import ScoringService

        tree = ast.parse(
            inspect.getsource(ScoringService._build_filter_snapshot).lstrip()
        )
        ns = [
            kw.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "get_latest_n_financials"
            for kw in node.keywords
            if kw.arg == "n" and isinstance(kw.value, ast.Constant)
        ]
        assert ns, "未按关键字传 n，无法核验期数"
        assert min(ns) >= 4, (
            f"只取 {min(ns)} 期：未披露占位期会占掉名额，"
            "F-5 拿不到两个有值期 → 整条实质失效"
        )


class TestBacktestDivergenceIsExplicit:
    """回测走的是无历史的降级分支，口径与生产**不同**——把它钉成显式事实。

    `BacktestEngine` 调 `filter()` 时不传 `financials_history`，故走 `else` 分支
    「单期为负即剔」。2026-09-07 生产侧改为「最近 2 个有值期皆负才剔」之后，
    两者分歧变大：**回测的 universe 比生产更小**。

    不静默改回测（会改变所有历史回测结果），但也不能让这个分歧无声存在——
    没有这条测试，下一个人读 `filter()` 会以为回测和生产用的是同一条规则。
    已登记 roadmap V1.5-L 回测保真度。
    """

    def test_degraded_branch_still_excludes_on_single_negative(
        self, calendar: TradingCalendar
    ) -> None:
        """不传历史 → 单期为负即剔（回测口径）。"""
        idx = pd.Index([CODE], name="ts_code")
        info = pd.DataFrame({
            "is_st": [False], "list_date": [date(2020, 1, 1)],
            "is_suspended": [False], "sw_industry_l1": ["制造"],
        }, index=idx)
        fin = pd.DataFrame({
            "total_equity": [1e9], "net_profit_yoy": [-5.0], "debt_to_asset": [0.5],
        }, index=idx)
        quotes = pd.DataFrame(
            {"amount": [1e7], "vol": [1e4], "limit_up": [False]}, index=idx
        )
        got = UniverseFilter().filter(info, fin, quotes, TODAY, calendar)
        assert CODE not in got, "降级分支的口径被改了——所有历史回测结果会随之变化"

    def test_same_stock_passes_under_production_rule(
        self, calendar: TradingCalendar
    ) -> None:
        """同一只股票在生产口径下**通过**——分歧的具体形状。"""
        assert _run(UniverseFilter(), calendar, _hist([float("nan"), -5.0])) is True

    def test_backtest_engine_does_not_pass_history(self) -> None:
        """钉住「回测确实不传历史」这个前提；它一旦变了，上面两条的解读就失效。"""
        import ast
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parents[2]
            / "src" / "quantpilot" / "engine" / "backtest" / "engine.py"
        ).read_text(encoding="utf-8")
        kwargs = {
            kw.arg
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "filter"
            for kw in n.keywords
        }
        assert "financials_history" not in kwargs, (
            "回测开始传历史了 —— 请更新本组测试与 universe.py 里的分歧说明"
        )
