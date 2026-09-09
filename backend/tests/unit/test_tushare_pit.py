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


class TestByStockSupportsPeriodPinnedCall:
    """⚠️ `fina_indicator` **按日期窗口调用有 100 行硬上限**（2026-09-09 实调确认）。

    实测：5 码 + `start_date/end_date` 跨 5.7 年 → **恰好 100 行**（每码 18~21 期，
    被截断）；同样 5 码改用 `period=` 定期调用 → 每期完整返回。

    回填用 50 码/批 → 100 ÷ 50 = **每股只拿到 2 期**，于是 C2 的 6 个新列
    只有最新一期有值、更早各期全空，而**回填脚本报告 ok=5515 fail=0**——
    调用成功、行数被静默截断，是 §4.3「日期类接口静默返错数据」的又一形态。

    ⚠️ 设计文档 2026-08-27 的「真调核对」验的是 `period=` 定期调用，
    **而回填实际走的是日期窗口调用**——验证做了，验的不是真正走的那条路径。

    故 `fetch_financial_by_stock` 增 `period` 参数：给了就走定期调用。
    """

    @staticmethod
    def _fina_period_row() -> pd.DataFrame:
        return pd.DataFrame({
            "ts_code": ["000001.SZ"], "ann_date": ["20240830"], "end_date": ["20240630"],
            "roe": [15.0], "netprofit_yoy": [10.0], "tr_yoy": [8.0],
            "debt_to_assets": [50.0], "roa": [7.5], "ocfps": [1.2], "eps": [0.9],
            "current_ratio": [2.5], "grossprofit_margin": [32.0], "assets_turn": [0.85],
        })

    async def test_period_argument_switches_call_shape(
        self, adapter: TushareAdapter
    ) -> None:
        """给了 `period` → 必须传 `period=` 且**不传** start/end（否则仍被 100 行截断）。"""
        calls = []

        async def _spy(fn, **kw):
            calls.append(kw)
            return self._fina_period_row()

        with patch.object(adapter, "_call", new=_spy):
            await adapter.fetch_financial_by_stock(
                ["000001.SZ"], date(2021, 1, 1), date(2026, 9, 8), period="20240630"
            )
        kw = calls[0]
        assert kw.get("period") == "20240630", "未走定期调用"
        assert "start_date" not in kw and "end_date" not in kw, (
            "同时传了日期窗口 —— 仍会命中 100 行上限"
        )

    async def test_without_period_keeps_date_range_shape(
        self, adapter: TushareAdapter
    ) -> None:
        """不给 `period` → 保持原日期窗口形态（向后兼容，既有调用点不受影响）。"""
        calls = []

        async def _spy(fn, **kw):
            calls.append(kw)
            return self._fina_period_row()

        with patch.object(adapter, "_call", new=_spy):
            await adapter.fetch_financial_by_stock(
                ["000001.SZ"], date(2024, 1, 1), date(2024, 12, 31)
            )
        kw = calls[0]
        assert "period" not in kw
        assert kw.get("start_date") == "20240101"

    async def test_period_path_still_maps_and_converts(
        self, adapter: TushareAdapter
    ) -> None:
        """定期路径的字段映射与单位换算必须与窗口路径一致——两条路各写一份必漂。"""
        with patch.object(
            adapter, "_call", new=AsyncMock(return_value=self._fina_period_row())
        ):
            res = await adapter.fetch_financial_by_stock(
                ["000001.SZ"], date(2021, 1, 1), date(2026, 9, 8), period="20240630"
            )
        r = res.iloc[0]
        assert r["publish_date"] == date(2024, 8, 30)
        assert r["report_period"] == date(2024, 6, 30)
        assert r["roa"] == pytest.approx(0.075)
        assert r["current_ratio"] == pytest.approx(2.5)


class TestRowCapDetection:
    """接口返回行数**恰好等于整数上限** = 截断指纹。两次真实事故都是这个形态：

    | 事故 | 行数 | 后果 |
    |---|---|---|
    | `suspend_d` 参数名写错（2026-09-02）| **恰好 5000** | 818 只正常股被当停牌，持续 4 个月 |
    | `fina_indicator` 日期窗口（2026-09-09）| **恰好 100** | C2 回填只填最新一期 |

    两次都**不报错**，后者甚至报告 `ok=5515 fail=0`。

    §4.3 已有的判据是「验返回数据的日期是否落在入参窗口内」——**它拦不住这一类**：
    返回的数据确实落在窗口内，只是少了一大半。故在 `_call` 统一加一层行数指纹告警。

    ⚠️ 这是**告警不是拦截**：合法响应偶尔也可能恰好是整百行。告警的价值在于
    「有人看得见」，而此前是完全无声。
    """

    async def _call_n(self, adapter: TushareAdapter, n: int, caplog):
        df = pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(n)]})
        with caplog.at_level("WARNING"):
            await adapter._call(lambda **kw: df)
        return caplog.text

    async def test_exact_cap_logs_warning(self, adapter: TushareAdapter, caplog) -> None:
        """恰好 100 行（今天 `fina_indicator` 的指纹）→ 必须告警。"""
        assert "100" in await self._call_n(adapter, 100, caplog)

    async def test_five_thousand_cap_logs_warning(
        self, adapter: TushareAdapter, caplog
    ) -> None:
        """恰好 5000 行——2026-09-02 `suspend_d` 事故的指纹。"""
        txt = await self._call_n(adapter, 5000, caplog)
        assert "5000" in txt

    async def test_ordinary_row_count_is_silent(
        self, adapter: TushareAdapter, caplog
    ) -> None:
        """⚠️ 反向钉：普通行数不得告警，否则每天刷屏 → 被当噪声忽略 →
        等于没有告警（同 `universe_filter_low_coverage` 的阈值教训）。"""
        txt = await self._call_n(adapter, 5487, caplog)
        assert txt.strip() == ""

    async def test_empty_result_is_silent(
        self, adapter: TushareAdapter, caplog
    ) -> None:
        txt = await self._call_n(adapter, 0, caplog)
        assert txt.strip() == ""


class TestInterfaceNameResolution:
    """⚠️ Tushare SDK 的 `pro.xxx` 是 `functools.partial(DataApi.query, 'xxx')`，
    **没有 `__name__`**。而 `_call` 取的是 `func.__name__`，于是：

    - `TUSHARE_CALLS{interface}` 埋点自 Phase 13 上线起**恒为 `unknown`**，
      13 个接口全挤在一个标签下，完全没有区分度
    - 行数截断告警也不指名接口 → 排障时得靠猜

    「告警要有人看得见才算数」——不指名接口的告警只完成了一半。
    接口名在 `partial.args[0]`。
    """

    def test_resolves_partial_interface_name(self, adapter: TushareAdapter) -> None:
        import functools

        fake = functools.partial(lambda name, **kw: None, "fina_indicator")
        assert adapter._interface_name(fake) == "fina_indicator"

    def test_falls_back_to_dunder_name(self, adapter: TushareAdapter) -> None:
        def some_api(**kw):
            return None

        assert adapter._interface_name(some_api) == "some_api"

    def test_unknown_when_neither(self, adapter: TushareAdapter) -> None:
        assert adapter._interface_name(object()) == "unknown"

    async def test_row_cap_warning_names_the_interface(
        self, adapter: TushareAdapter, caplog
    ) -> None:
        """告警必须带接口名——否则排障要在 13 个接口里猜。"""
        import functools

        df = pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(100)]})
        fn = functools.partial(lambda name, **kw: df, "fina_indicator")
        with caplog.at_level("WARNING"):
            await adapter._call(fn)
        assert "fina_indicator" in caplog.text
        assert "unknown" not in caplog.text
