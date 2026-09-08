"""`fetch_financial_data` 必须按公告日做 PIT 截断——否则回填历史即制造前视偏差。

## 缺陷（2026-09-07 实证）

`fina_indicator(period=最近季度末, ts_code=批量)` **不带公告日过滤**，且 `fields`
里根本没有 `ann_date`。同一段代码因运行时点不同而行为迥异：

- **实时**跑（as_of = 今天）：该期尚未披露 → Tushare 返空 → 行是 NULL → 正确
- **回填**跑（as_of = 2025-07-15）：该期早已披露 → Tushare 返**最终值** → 写进
  `publish_date=2025-07-15` 的行 → **PIT 查询在 7 月就看到 8 月才公布的中报**

真机实测（5434 与生产逐条一致）：`report_period=2025-06-30` 且
`publish_date=2025-07-15` 的 5408 行中，5391 行有值，其中 **5384 行等于该期最终
公布值**、等于上一期值的仅 2 行——即确实把未来值提前写了进去，不是「沿用上期」的误标。
污染 3,181,745 行 / 100,833 个 (股票, 报告期)，跨 2021-05 ~ 2026-05。

## 修法

`fields` 补 `ann_date`，并丢弃 `ann_date > as_of_date` 的行。

⚠️ **过滤必须在取缓存之后**：`_fina_cache` 按 `period` 缓存，而该期对不同 as_of_date
的可见性不同。在入缓存前过滤，会让同一 period 的第一个 as_of_date 决定后续所有日期
——`ingest_history` 恰恰按日推进，等于全窗口沿用第一天的可见性。

⚠️ **`ann_date` 缺失时 fail-closed**（丢弃基本面 + 记 ERROR）：CLAUDE.md §4.3 记着
「接口参数名写错 = 静默返全表前 5000 行」——若字段名将来失效，静默放行等于把前视偏差
原样放回来。宁可基本面全空（当天 `universe_filter_low_coverage` 会告警、看得见），
也不要悄悄泄漏。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from quantpilot.data.adapters.tushare import TushareAdapter


@pytest.fixture
def adapter() -> TushareAdapter:
    return TushareAdapter(token="test-token")


def _basic() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["000001.SZ", "000002.SZ"],
        "pe_ttm": [12.0, 20.0], "pb": [1.2, 2.0], "dv_ttm": [3.0, 1.0],
    })


def _fina(ann_dates: list[str] | None) -> pd.DataFrame:
    cols = {
        "ts_code": ["000001.SZ", "000002.SZ"],
        "end_date": ["20250630", "20250630"],
        "roe": [15.0, 20.0],
        "netprofit_yoy": [10.0, -5.0],
        "tr_yoy": [8.0, 3.0],
        "debt_to_assets": [50.0, 60.0],
    }
    if ann_dates is not None:
        cols["ann_date"] = ann_dates
    return pd.DataFrame(cols)


async def _run(adapter: TushareAdapter, as_of: date, fina: pd.DataFrame) -> pd.DataFrame:
    with patch.object(adapter, "_call", new=AsyncMock(side_effect=[_basic(), fina])):
        return await adapter.fetch_financial_data(as_of)


class TestAnnDateTruncation:
    async def test_future_announcement_is_dropped(self, adapter: TushareAdapter) -> None:
        """公告日晚于 as_of → 基本面必须丢弃，行情字段保留。"""
        res = await _run(adapter, date(2025, 7, 15), _fina(["20250828", "20250830"]))
        assert len(res) == 2, "行本身不该消失——pe/pb 是当日真实行情"
        assert res["roe"].isna().all(), "未公告的 roe 泄漏了"
        assert res["net_profit_yoy"].isna().all(), "未公告的 yoy 泄漏了"
        assert res["pe_ttm"].notna().all(), "行情字段被误删"

    async def test_already_announced_is_kept(self, adapter: TushareAdapter) -> None:
        res = await _run(adapter, date(2025, 9, 1), _fina(["20250828", "20250830"]))
        assert res.set_index("ts_code").loc["000001.SZ", "roe"] == pytest.approx(0.15)

    async def test_mixed_only_drops_the_future_one(self, adapter: TushareAdapter) -> None:
        """一只已公告、一只未公告 → 只丢未公告那只。

        全丢或全留都能让上面两条单独通过，这条才区分得开。
        """
        res = await _run(adapter, date(2025, 8, 29), _fina(["20250828", "20250930"]))
        r = res.set_index("ts_code")
        assert r.loc["000001.SZ", "roe"] == pytest.approx(0.15)
        assert pd.isna(r.loc["000002.SZ", "roe"])


class TestCacheDoesNotFreezeVisibility:
    async def test_same_period_different_as_of_sees_different_rows(
        self, adapter: TushareAdapter
    ) -> None:
        """⚠️ 本文件最关键的一条：`_fina_cache` 按 period 缓存，
        过滤若发生在**入缓存之前**，同一 period 的首个 as_of_date 会决定后续所有日期。
        而 `ingest_history` 正是按日推进——等于整个窗口沿用第一天的可见性，
        缺陷从「全窗口泄漏」变成「全窗口缺失」，方向反了但一样错。
        """
        fina = _fina(["20250828", "20250830"])
        early = await _run(adapter, date(2025, 7, 15), fina)
        assert early["roe"].isna().all(), "7 月不该看到 8 月的公告"

        # 同一 period，第二次调用应命中缓存但**按新的 as_of 重新过滤**
        with patch.object(adapter, "_call", new=AsyncMock(side_effect=[_basic()])):
            late = await adapter.fetch_financial_data(date(2025, 9, 1))
        assert late["roe"].notna().any(), (
            "缓存把首个 as_of 的可见性冻结了 —— 过滤必须在取缓存之后"
        )


class TestMissingAnnDateFailsClosed:
    async def test_absent_column_drops_fundamentals_and_logs_error(
        self, adapter: TushareAdapter, caplog
    ) -> None:
        """`ann_date` 缺失（字段名失效 / 接口变更）→ fail-closed + ERROR。

        §4.3 记着「接口参数名写错 = 静默返全表前 5000 行」：静默放行等于把
        前视偏差原样放回来，且不会有人发现。宁可基本面全空——那当天就会被
        `universe_filter_low_coverage` 告警照出来。
        """
        with caplog.at_level("ERROR"):
            res = await _run(adapter, date(2025, 9, 1), _fina(None))
        assert res["roe"].isna().all(), "缺公告日时仍放行 = 前视偏差原样回来"
        assert any("ann_date" in r.getMessage() for r in caplog.records), "未记 ERROR"

    async def test_fields_actually_request_ann_date(self, adapter: TushareAdapter) -> None:
        """钉住 `fields` 真的问了 `ann_date`——不问就永远走 fail-closed 分支。"""
        calls = []

        async def _spy(fn, **kw):
            calls.append(kw)
            return _basic() if len(calls) == 1 else _fina(["20250828", "20250830"])

        with patch.object(adapter, "_call", new=_spy):
            await adapter.fetch_financial_data(date(2025, 9, 1))
        fina_kw = [c for c in calls if "period" in c]
        assert fina_kw, "未调用 fina_indicator"
        assert "ann_date" in fina_kw[0].get("fields", ""), "fields 未请求 ann_date"


# ── V1.5-C C2：Piotroski 7 字段的采集与单位换算 ────────────────────────────────


def _fina_c2(ann: str = "20250828") -> pd.DataFrame:
    """fina_indicator 真实返回形态（含 C2 新增 6 列，Tushare 侧多为百分数）。"""
    return pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "end_date": ["20250630"],
        "ann_date": [ann],
        "roe": [15.0],                    # %
        "netprofit_yoy": [10.0],          # %
        "tr_yoy": [8.0],                  # %
        "debt_to_assets": [50.0],         # %
        "roa": [7.5],                     # % → 0.075
        "ocfps": [1.2345],                # 元/股，不换算
        "eps": [0.9876],                  # 元/股，不换算
        "current_ratio": [2.5],           # 倍数，不换算
        "grossprofit_margin": [32.0],     # % → 0.32
        "assets_turn": [0.85],            # 次，不换算
    })


def _basic_c2() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["000001.SZ"], "pe_ttm": [12.0], "pb": [1.2], "dv_ttm": [3.0],
        "total_share": [194_405.9], "float_share": [194_405.9],  # 万股
    })


class TestPiotroskiFieldsIngestion:
    """C2 的 7 个字段必须真的入库，且单位换算正确。

    ⚠️ 单位错了不会报错、也不会让测试变红——F-Score 的 9 项里有 5 项是**同比比较**，
    两端同样缩放时比较结果不变，错误会一路潜伏到与阈值有关的地方才发作。
    故这里逐字段钉死数值，而不是只断言「列存在」。
    """

    @staticmethod
    async def _run(adapter: TushareAdapter) -> pd.Series:
        with patch.object(
            adapter, "_call", new=AsyncMock(side_effect=[_basic_c2(), _fina_c2()])
        ):
            res = await adapter.fetch_financial_data(date(2025, 9, 1))
        return res.iloc[0]

    async def test_percentage_fields_converted(self, adapter: TushareAdapter) -> None:
        r = await self._run(adapter)
        assert r["roa"] == pytest.approx(0.075), "roa 是百分数，须 /100"
        assert r["grossprofit_margin"] == pytest.approx(0.32), "毛利率是百分数，须 /100"

    async def test_ratio_and_per_share_fields_not_converted(
        self, adapter: TushareAdapter
    ) -> None:
        """倍数/每股类**不得**除以 100——多除一次会让 F-Score 的阈值判定失真。"""
        r = await self._run(adapter)
        assert r["current_ratio"] == pytest.approx(2.5)
        assert r["assets_turn"] == pytest.approx(0.85)
        assert r["ocfps"] == pytest.approx(1.2345)
        assert r["eps"] == pytest.approx(0.9876)

    async def test_total_share_comes_from_daily_basic_in_shares(
        self, adapter: TushareAdapter
    ) -> None:
        """`total_share` 取自 `daily_basic`，单位**万股 → 股**（×10000）。

        选 `daily_basic` 而非 `balancesheet` 的理由见设计 §4.1：后者不支持逗号多码，
        全市场两期约需 11000 次调用。
        """
        r = await self._run(adapter)
        assert r["total_share"] == pytest.approx(194_405.9 * 10_000)

    async def test_fields_string_requests_all_seven(
        self, adapter: TushareAdapter
    ) -> None:
        """钉住 `fields` 真的问了这些列——不问就永远是 NULL，而 F-Score 会
        安静地判成「不可判」，看起来像数据还没回填。"""
        calls = []

        async def _spy(fn, **kw):
            calls.append(kw)
            return _basic_c2() if len(calls) == 1 else _fina_c2()

        with patch.object(adapter, "_call", new=_spy):
            await adapter.fetch_financial_data(date(2025, 9, 1))
        basic_kw = calls[0].get("fields", "")
        fina_kw = calls[1].get("fields", "")
        assert "total_share" in basic_kw, "daily_basic 未请求 total_share"
        for f in ("roa", "ocfps", "eps", "current_ratio",
                  "grossprofit_margin", "assets_turn"):
            assert f in fina_kw, f"fina_indicator 未请求 {f}"


class TestByStockCarriesPiotroskiFields:
    """`fetch_financial_by_stock`（回填路径，`publish_date = ann_date`）也要带 C2 六列。

    ⚠️ 两条采集路径都得改：日频快照（`fetch_financial_data`）供每日增量，
    逐股路径供历史回填。只改一条 → 回填出来的历史里这 6 列全 NULL →
    F-Score 全历史「不可判」→ 门控在回测/面板里永不生效，且**没有任何报错**。
    """

    async def test_fields_and_unit_conversion(self, adapter: TushareAdapter) -> None:
        raw = pd.DataFrame({
            "ts_code": ["000001.SZ"], "ann_date": ["20250828"], "end_date": ["20250630"],
            "roe": [15.0], "netprofit_yoy": [10.0], "tr_yoy": [8.0],
            "debt_to_assets": [50.0],
            "roa": [7.5], "ocfps": [1.2345], "eps": [0.9876],
            "current_ratio": [2.5], "grossprofit_margin": [32.0], "assets_turn": [0.85],
        })
        calls = []

        async def _spy(fn, **kw):
            calls.append(kw)
            return raw

        with patch.object(adapter, "_call", new=_spy):
            res = await adapter.fetch_financial_by_stock(
                ["000001.SZ"], date(2025, 1, 1), date(2025, 12, 31)
            )
        for f in ("roa", "ocfps", "eps", "current_ratio",
                  "grossprofit_margin", "assets_turn"):
            assert f in calls[0].get("fields", ""), f"fields 未请求 {f}"
            assert f in res.columns, f"返回缺列 {f}"
        r = res.iloc[0]
        assert r["roa"] == pytest.approx(0.075), "roa 是百分数，须 /100"
        assert r["grossprofit_margin"] == pytest.approx(0.32)
        assert r["current_ratio"] == pytest.approx(2.5), "倍数不得再除 100"
        assert r["ocfps"] == pytest.approx(1.2345)
        assert r["publish_date"] == date(2025, 8, 28), "本路径用 ann_date 作 publish_date"
