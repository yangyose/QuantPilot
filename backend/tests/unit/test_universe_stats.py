"""UniverseFilter 逐规则剔除计数（可观测性缺口，CLAUDE.md §6）。

## 为什么需要它

生产**没有任何表持久化每日 universe 规模**，容器重启后日志只剩当日一行。
2026-09-03 `is_suspended` 修复上线后，「universe 到底扩大了百分之几」无法回溯实证；
2026-09-07 F-4 净资产过滤修复同样面临这个问题——改动了选股面，却度量不了。

比"总数"更有用的是**逐条规则剔除了多少**：只看总数，F-4 从"实质失效"变成"生效"
与"当天恰好没有负净资产股"长得一样（§4.11 元判据：判据在两种情况下给出相同结果，
它就不是判据）。

## 不变量

`filter_with_stats()` 返回的 Index 必须与 `filter()` **逐值相等** ——
可观测性改造不得改变选股结果。本文件第一条就钉这个。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from quantpilot.data.calendar import TradingCalendar
from quantpilot.engine.universe import UniverseFilter

TODAY = date(2025, 1, 31)


@pytest.fixture
def calendar() -> TradingCalendar:
    days, d = [], date(2019, 1, 1)
    while d <= date(2025, 2, 28):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return TradingCalendar(days)


def _frames(n: int = 20):
    """n 只股票，默认全部可通过；逐条规则各污染 1~2 只。"""
    codes = [f"{i:06d}.SZ" for i in range(n)]
    info = pd.DataFrame({
        "is_st": [False] * n,
        "list_date": [date(2020, 1, 1)] * n,
        "is_suspended": [False] * n,
        "sw_industry_l1": ["制造"] * n,
    }, index=pd.Index(codes, name="ts_code"))
    fin = pd.DataFrame({
        "total_equity": [1e9] * n,
        "net_profit_yoy": [10.0] * n,
        "debt_to_asset": [0.5] * n,
    }, index=pd.Index(codes, name="ts_code"))
    quotes = pd.DataFrame({
        "amount": [1e7] * n, "vol": [1e4] * n, "limit_up": [False] * n,
    }, index=pd.Index(codes, name="ts_code"))
    return codes, info, fin, quotes


class TestStatsDoNotChangeSelection:
    def test_index_identical_to_plain_filter(
        self, calendar: TradingCalendar
    ) -> None:
        """⚠️ 首要不变量：加了统计，选出来的股票必须一模一样。"""
        uf = UniverseFilter()
        codes, info, fin, quotes = _frames()
        info.loc[codes[0], "is_st"] = True
        fin.loc[codes[1], "total_equity"] = -1.0
        quotes.loc[codes[2], "amount"] = 1.0

        plain = uf.filter(info, fin, quotes, TODAY, calendar)
        idx, _stats = uf.filter_with_stats(info, fin, quotes, TODAY, calendar)
        assert list(idx) == list(plain)


class TestPerRuleCounts:
    def test_each_rule_reports_what_it_excluded(
        self, calendar: TradingCalendar
    ) -> None:
        """逐条规则各污染已知只数 → 统计必须逐条对上。"""
        uf = UniverseFilter()
        codes, info, fin, quotes = _frames()
        info.loc[codes[0], "is_st"] = True                      # F-1: 1
        info.loc[codes[1], "list_date"] = date(2025, 1, 20)     # F-2: 1
        info.loc[codes[2], "is_suspended"] = True               # F-3: 1
        fin.loc[codes[3], "total_equity"] = -1.0                # F-4: 1
        fin.loc[codes[4], "net_profit_yoy"] = -5.0              # F-5: 1
        fin.loc[codes[5], "debt_to_asset"] = 0.95               # F-6: 1
        quotes.loc[codes[6], "amount"] = 1.0                    # F-7: 1
        quotes.loc[codes[7], ["limit_up", "vol"]] = [True, 0]   # F-8: 1

        idx, stats = uf.filter_with_stats(info, fin, quotes, TODAY, calendar)
        assert stats.total_in == 20
        assert stats.total_out == 12
        assert len(idx) == 12
        assert stats.excluded == {
            "F-1": 1, "F-2": 1, "F-3": 1, "F-4": 1,
            "F-5": 1, "F-6": 1, "F-7": 1, "F-8": 1,
        }

    def test_zero_exclusions_are_reported_as_zero_not_omitted(
        self, calendar: TradingCalendar
    ) -> None:
        """剔除 0 只也必须出现在 dict 里。

        ⚠️ 缺省即省略的话，「F-4 生效但没命中」与「F-4 整条没跑」在数据里
        长得一模一样——那正是这次要根治的形态（§4.11 元判据）。
        """
        uf = UniverseFilter()
        _codes, info, fin, quotes = _frames()
        _idx, stats = uf.filter_with_stats(info, fin, quotes, TODAY, calendar)
        assert set(stats.excluded) == {f"F-{i}" for i in range(1, 9)}
        assert all(v == 0 for v in stats.excluded.values())
        assert stats.total_out == 20

    def test_counts_are_marginal_not_cumulative(
        self, calendar: TradingCalendar
    ) -> None:
        """同一只股票同时违反两条规则时，只记在**先执行**的那条上。

        否则各条之和会超过实际剔除数，「F-4 剔了多少」就不可解读。
        """
        uf = UniverseFilter()
        codes, info, fin, quotes = _frames()
        info.loc[codes[0], "is_st"] = True          # F-1
        fin.loc[codes[0], "total_equity"] = -1.0    # 同一只也违反 F-4
        _idx, stats = uf.filter_with_stats(info, fin, quotes, TODAY, calendar)
        assert stats.excluded["F-1"] == 1
        assert stats.excluded["F-4"] == 0, "重复计数会让各条之和超过实际剔除数"
        assert sum(stats.excluded.values()) == stats.total_in - stats.total_out

    def test_financial_exemption_visible(self, calendar: TradingCalendar) -> None:
        """金融股豁免 F-4/F-5/F-6 —— 豁免掉的不算剔除。"""
        uf = UniverseFilter()
        codes, info, fin, quotes = _frames()
        info.loc[codes[0], "sw_industry_l1"] = "银行"
        fin.loc[codes[0], "total_equity"] = -1.0
        _idx, stats = uf.filter_with_stats(info, fin, quotes, TODAY, calendar)
        assert stats.excluded["F-4"] == 0
        assert stats.total_out == 20


class TestBacktestDoesNotWriteDailyStats:
    """回测**不得**写 `universe_daily_stat`。

    ⚠️ 这是「现在对、改一下就错」的不变量：回测按历史日期逐日跑，若它也写这张表，
    一次 5 年回测会把生产的每日统计整片覆盖成回测时点的值——而两者的 universe
    本就不同（回测用的是当时的配置与数据快照）。覆盖后无从分辨哪行来自真实管线，
    这张表的全部价值随之归零。

    现状是对的（`engine/backtest/engine.py` 调的是 `filter()` 薄封装），
    但没有任何东西阻止后来的人把回测改走 `score_universe_for_date`。
    """

    def test_backtest_engine_uses_plain_filter(self) -> None:
        import ast
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parents[2]
            / "src" / "quantpilot" / "engine" / "backtest" / "engine.py"
        ).read_text(encoding="utf-8")
        calls = {
            n.func.attr
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "filter_with_stats" not in calls, (
            "回测改走了统计版 —— 一次 5 年回测会覆盖生产的每日 universe 统计"
        )
        assert "score_universe_for_date" not in calls, (
            "回测改走了会写统计的评分入口，同上"
        )
