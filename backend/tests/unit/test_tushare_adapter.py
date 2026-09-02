"""ADP-01~06: TushareAdapter 单元测试（使用 Mock 数据，无真实网络调用）"""
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from quantpilot.data.adapters.tushare import TushareAdapter


@pytest.fixture
def adapter() -> TushareAdapter:
    with patch("quantpilot.data.adapters.tushare.ts") as mock_ts:
        mock_ts.pro_api.return_value = MagicMock()
        adp = TushareAdapter(token="test-token")
        yield adp


def _make_daily_mocks() -> tuple[pd.DataFrame, ...]:
    """返回 fetch_daily_quotes 所需的 5 个模拟 DataFrame（按调用顺序）"""
    daily = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260102"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "pre_close": [10.0],
            "pct_chg": [5.0],
            "vol": [1000.0],
            "amount": [10_000.0],
        }
    )
    basic = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260102"],
            "turnover_rate": [1.0],
            "circ_mv": [100_000.0],
        }
    )
    adj = pd.DataFrame(
        {"ts_code": ["000001.SZ"], "trade_date": ["20260102"], "adj_factor": [1.0]}
    )
    suspend = pd.DataFrame(columns=["ts_code"])
    limit = pd.DataFrame(columns=["ts_code", "limit_type"])
    return daily, basic, adj, suspend, limit


@pytest.mark.asyncio
async def test_adp_01_fetch_stock_list_fields(adapter: TushareAdapter) -> None:
    """ADP-01: fetch_stock_list() 映射正确，关键字段存在且 ts_code 值正确"""
    mock_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "name": ["平安银行"],
            "market": ["MAIN"],
            "industry": ["银行"],
            "list_date": ["19910403"],
            "delist_date": [None],
            "list_status": ["L"],
        }
    )
    with patch.object(adapter, "_call", new=AsyncMock(return_value=mock_df)):
        result = await adapter.fetch_stock_list()

    assert "ts_code" in result.columns
    assert "name" in result.columns
    assert "list_date" in result.columns
    assert result.iloc[0]["ts_code"] == "000001.SZ"


@pytest.mark.asyncio
async def test_adp_02_vol_unit_conversion(adapter: TushareAdapter) -> None:
    """ADP-02: vol 手 → 股（×100）"""
    daily, basic, adj, suspend, limit = _make_daily_mocks()
    with patch.object(
        adapter, "_call", new=AsyncMock(side_effect=[daily, basic, adj, suspend, limit])
    ):
        result = await adapter.fetch_daily_quotes(date(2026, 1, 2))

    assert result.iloc[0]["vol"] == 1000.0 * 100


@pytest.mark.asyncio
async def test_adp_03_amount_unit_conversion(adapter: TushareAdapter) -> None:
    """ADP-03: amount 千元 → 元（×1000）"""
    daily, basic, adj, suspend, limit = _make_daily_mocks()
    with patch.object(
        adapter, "_call", new=AsyncMock(side_effect=[daily, basic, adj, suspend, limit])
    ):
        result = await adapter.fetch_daily_quotes(date(2026, 1, 2))

    assert result.iloc[0]["amount"] == 10_000.0 * 1000


@pytest.mark.asyncio
async def test_adp_04_pct_chg_to_decimal(adapter: TushareAdapter) -> None:
    """ADP-04: pct_chg % → 小数（/100）"""
    daily, basic, adj, suspend, limit = _make_daily_mocks()
    with patch.object(
        adapter, "_call", new=AsyncMock(side_effect=[daily, basic, adj, suspend, limit])
    ):
        result = await adapter.fetch_daily_quotes(date(2026, 1, 2))

    assert abs(result.iloc[0]["pct_chg"] - 0.05) < 1e-9


@pytest.mark.asyncio
async def test_adp_05_roe_to_decimal(adapter: TushareAdapter) -> None:
    """ADP-05: fetch_financial_data() roe % → 小数（/100）；basic 为主表"""
    mock_basic = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "pe_ttm": [12.0],
            "pb": [1.2],
            "dv_ttm": [3.0],
        }
    )
    mock_fina = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "end_date": ["20250930"],
            "roe": [15.0],
            "netprofit_yoy": [10.0],
            "tr_yoy": [8.0],
            "total_hldr_eqy_exc_min_int": [100_000.0],
            "debt_to_assets": [50.0],
        }
    )
    # 调用顺序：先 basic（daily_basic），再 fina（fina_indicator）
    with patch.object(
        adapter, "_call", new=AsyncMock(side_effect=[mock_basic, mock_fina])
    ):
        result = await adapter.fetch_financial_data(date(2026, 1, 2))

    assert abs(result.iloc[0]["roe"] - 0.15) < 1e-9
    # publish_date = as_of_date，非 fina 的 ann_date
    assert result.iloc[0]["publish_date"] == date(2026, 1, 2)


@pytest.mark.asyncio
async def test_adp_05b_financial_data_basic_is_main_table(
    adapter: TushareAdapter,
) -> None:
    """ADP-05b: basic 为主表 — 无 fina 记录的股票仍出现在结果中，pe_ttm/pb 正常"""
    mock_basic = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "pe_ttm": [12.0, 20.0],
            "pb": [1.2, 2.0],
            "dv_ttm": [3.0, 1.5],
        }
    )
    # fina 只有 000001.SZ
    mock_fina = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "end_date": ["20250930"],
            "roe": [15.0],
            "netprofit_yoy": [10.0],
            "tr_yoy": [8.0],
            "total_hldr_eqy_exc_min_int": [100_000.0],
            "debt_to_assets": [50.0],
        }
    )
    with patch.object(
        adapter, "_call", new=AsyncMock(side_effect=[mock_basic, mock_fina])
    ):
        result = await adapter.fetch_financial_data(date(2026, 1, 2))

    assert len(result) == 2  # 两只股票都在
    row2 = result[result["ts_code"] == "000002.SZ"].iloc[0]
    assert row2["pe_ttm"] == 20.0   # pe_ttm 有值
    assert pd.isna(row2["roe"])     # roe 为 NaN（无季报数据）


@pytest.mark.asyncio
async def test_adp_06_ts_codes_filter(adapter: TushareAdapter) -> None:
    """ADP-06: fetch_daily_quotes(ts_codes=['000001.SZ']) 只返回指定股票"""
    daily = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "trade_date": ["20260102", "20260102"],
            "open": [10.0, 20.0],
            "high": [11.0, 21.0],
            "low": [9.0, 19.0],
            "close": [10.5, 20.5],
            "pre_close": [10.0, 20.0],
            "pct_chg": [5.0, 3.0],
            "vol": [1000.0, 2000.0],
            "amount": [10_000.0, 20_000.0],
        }
    )
    basic = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "trade_date": ["20260102", "20260102"],
            "turnover_rate": [1.0, 1.0],
            "circ_mv": [100_000.0, 200_000.0],
        }
    )
    adj = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "trade_date": ["20260102", "20260102"],
            "adj_factor": [1.0, 1.0],
        }
    )
    suspend = pd.DataFrame(columns=["ts_code"])
    limit = pd.DataFrame(columns=["ts_code", "limit_type"])

    with patch.object(
        adapter, "_call", new=AsyncMock(side_effect=[daily, basic, adj, suspend, limit])
    ):
        result = await adapter.fetch_daily_quotes(
            date(2026, 1, 2), ts_codes=["000001.SZ"]
        )

    assert list(result["ts_code"]) == ["000001.SZ"]
    assert len(result) == 1


@pytest.mark.asyncio
async def test_adp_07_fetch_namechange(adapter: TushareAdapter) -> None:
    """ADP-07: fetch_namechange() 返回 start_date/end_date 为 date 类型"""
    mock_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["*ST 平安", "平安银行"],
            "start_date": ["20200101", "20210601"],
            "end_date": ["20210531", None],
        }
    )
    with patch.object(adapter, "_call", new=AsyncMock(return_value=mock_df)):
        result = await adapter.fetch_namechange(date(2020, 1, 1), date(2026, 1, 2))

    assert len(result) == 2
    assert isinstance(result.iloc[0]["start_date"], type(date.today()))
    # end_date=None 保持 None
    assert result.iloc[1]["end_date"] is None


# ── TD-01~TD-09：Phase 4 技术债修复方法单元测试 ────────────────────────────────

# ── TD-01~TD-03：fetch_stock_industry()（TD-3 申万行业分类）───────────────────

@pytest.mark.asyncio
async def test_td_01_fetch_stock_industry_fields(adapter: TushareAdapter) -> None:
    """TD-01: fetch_stock_industry() 经 index_classify+index_member 返回正确三列及映射"""
    # 第一次调用：index_classify(level='L1', src='SW2021') → 2 个 L1 行业
    l1_df = pd.DataFrame({
        "index_code": ["801010.SI", "801020.SI"],
        "industry_name": ["银行", "房地产"],
        "industry_code": ["1010", "1020"],
    })
    # 第二次调用：index_member(index_code='801010.SI') → 银行成分股
    members_bank = pd.DataFrame({"con_code": ["601398.SH", "600016.SH"]})
    # 第三次调用：index_member(index_code='801020.SI') → 房地产成分股
    members_re = pd.DataFrame({"con_code": ["000002.SZ"]})

    call_mock = AsyncMock(side_effect=[l1_df, members_bank, members_re])
    with patch.object(adapter, "_call", new=call_mock):
        result = await adapter.fetch_stock_industry()

    assert list(result.columns) == ["ts_code", "sw_industry_l1", "sw_industry_l2"]
    assert len(result) == 3
    bank_row = result[result["ts_code"] == "601398.SH"].iloc[0]
    assert bank_row["sw_industry_l1"] == "银行"
    assert bank_row["sw_industry_l2"] is None
    re_row = result[result["ts_code"] == "000002.SZ"].iloc[0]
    assert re_row["sw_industry_l1"] == "房地产"


@pytest.mark.asyncio
async def test_td_02_fetch_stock_industry_correct_api_params(
    adapter: TushareAdapter,
) -> None:
    """TD-02: fetch_stock_industry() 第一次调用 index_classify，参数 level='L1' src='SW2021'"""
    l1_df = pd.DataFrame({
        "index_code": ["801010.SI"],
        "industry_name": ["银行"],
        "industry_code": ["1010"],
    })
    members_df = pd.DataFrame({"con_code": ["601398.SH"]})
    call_mock = AsyncMock(side_effect=[l1_df, members_df])
    with patch.object(adapter, "_call", new=call_mock):
        await adapter.fetch_stock_industry()

    first_call_kwargs = call_mock.call_args_list[0][1]
    assert first_call_kwargs.get("level") == "L1"
    assert first_call_kwargs.get("src") == "SW2021"


@pytest.mark.asyncio
async def test_td_03_fetch_stock_industry_empty_returns_schema(
    adapter: TushareAdapter,
) -> None:
    """TD-03: index_classify 返回空表时，结果保持正确 schema 且行数为 0"""
    empty_l1 = pd.DataFrame(columns=["index_code", "industry_name", "industry_code"])
    with patch.object(adapter, "_call", new=AsyncMock(return_value=empty_l1)):
        result = await adapter.fetch_stock_industry()

    assert list(result.columns) == ["ts_code", "sw_industry_l1", "sw_industry_l2"]
    assert len(result) == 0


# ── TD-04~TD-06：fetch_financial_by_stock()（TD-1 ROE 及成长性）────────────────

@pytest.mark.asyncio
async def test_td_04_fetch_financial_by_stock_fields(adapter: TushareAdapter) -> None:
    """TD-04: fetch_financial_by_stock() 字段映射正确。
    输出列：roe/net_profit_yoy/revenue_yoy/debt_to_asset"""
    mock_df = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "ann_date": ["20250130"],
        "end_date": ["20241231"],
        "roe": [1500.0],           # Tushare 单位 %，需 /100
        "netprofit_yoy": [1200.0], # %
        "tr_yoy": [800.0],         # %
        "debt_to_assets": [4500.0],# %
    })
    with patch.object(adapter, "_call", new=AsyncMock(return_value=mock_df)):
        result = await adapter.fetch_financial_by_stock(
            ["000001.SZ"], date(2024, 1, 1), date(2025, 1, 31)
        )

    assert "roe" in result.columns
    assert "net_profit_yoy" in result.columns
    assert "revenue_yoy" in result.columns
    assert "debt_to_asset" in result.columns
    assert "publish_date" in result.columns
    assert "report_period" in result.columns
    # 百分比换算
    assert abs(result.iloc[0]["roe"] - 15.0) < 0.01
    assert abs(result.iloc[0]["net_profit_yoy"] - 12.0) < 0.01


@pytest.mark.asyncio
async def test_td_05_fetch_financial_by_stock_batches(adapter: TushareAdapter) -> None:
    """TD-05: 超过 50 只股票时分批调用，不会一次性提交超过 50 只"""
    codes = [f"{i:06d}.SZ" for i in range(110)]
    call_count = 0

    async def _batched_call(func, **kwargs):
        nonlocal call_count
        call_count += 1
        ts_code = kwargs.get("ts_code", "")
        return pd.DataFrame({
            "ts_code": [ts_code.split(",")[0]] if ts_code else [],
            "ann_date": ["20250130"] if ts_code else [],
            "end_date": ["20241231"] if ts_code else [],
            "roe": [1000.0] if ts_code else [],
            "netprofit_yoy": [500.0] if ts_code else [],
            "tr_yoy": [300.0] if ts_code else [],
            "debt_to_assets": [5000.0] if ts_code else [],
        })

    with patch.object(adapter, "_call", new=_batched_call):
        await adapter.fetch_financial_by_stock(
            codes, date(2024, 1, 1), date(2025, 1, 31)
        )

    # 110 只 / 50 只每批 = 3 次调用
    assert call_count == 3, f"期望 3 次批次调用，实际 {call_count}"


@pytest.mark.asyncio
async def test_td_06_fetch_financial_by_stock_empty_codes(
    adapter: TushareAdapter,
) -> None:
    """TD-06: ts_codes 为空列表时直接返回空 DataFrame，不调用 API"""
    call_mock = AsyncMock()
    with patch.object(adapter, "_call", new=call_mock):
        result = await adapter.fetch_financial_by_stock(
            [], date(2024, 1, 1), date(2025, 1, 31)
        )

    call_mock.assert_not_called()
    assert len(result) == 0


# ── TD-07~TD-09：fetch_balance_sheet()（TD-2 净资产）────────────────────────────

@pytest.mark.asyncio
async def test_td_07_fetch_balance_sheet_fields(adapter: TushareAdapter) -> None:
    """TD-07: fetch_balance_sheet() 返回 publish_date/report_period/total_equity"""
    mock_df = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "ann_date": ["20250130"],
        "end_date": ["20241231"],
        "total_hldr_eqy_exc_min_int": [1_000_000.0],  # 单位：万元，需 *10000 → 元
    })
    with patch.object(adapter, "_call", new=AsyncMock(return_value=mock_df)):
        result = await adapter.fetch_balance_sheet(
            ["000001.SZ"], date(2024, 1, 1), date(2025, 1, 31)
        )

    assert "total_equity" in result.columns
    assert "publish_date" in result.columns
    assert "report_period" in result.columns
    # 1_000_000 万元 × 10000 = 1e10 元
    assert abs(result.iloc[0]["total_equity"] - 1e10) < 1.0


@pytest.mark.asyncio
async def test_td_08_fetch_balance_sheet_per_single_code(adapter: TushareAdapter) -> None:
    """TD-08（2026-08-10 回归）：balancesheet **逐单码**调用，N 只 = N 次调用。
    历史 bug：曾按 50 码逗号分批（2 次调用），但 pro.balancesheet 不支持多码 → 空返回
    → total_equity 全 NULL。"""
    codes = [f"{i:06d}.SZ" for i in range(75)]
    call_count = 0

    async def _per_code_call(func, **kwargs):
        nonlocal call_count
        call_count += 1
        return pd.DataFrame(
            columns=["ts_code", "ann_date", "end_date", "total_hldr_eqy_exc_min_int"]
        )

    with patch.object(adapter, "_call", new=_per_code_call):
        await adapter.fetch_balance_sheet(codes, date(2024, 1, 1), date(2025, 1, 31))

    # 75 只 → 逐单码 75 次调用（不再是 50 码/批 = 2 次）
    assert call_count == 75, f"期望 75 次逐单码调用，实际 {call_count}"


@pytest.mark.asyncio
async def test_td_08b_fetch_balance_sheet_single_code_no_comma(
    adapter: TushareAdapter,
) -> None:
    """TD-08b（2026-08-10 回归 · 核心契约）：每次调用 ts_code 必须是**单码、无逗号**。
    pro.balancesheet 对 comma-joined ts_code 静默返回空 → total_equity 零产出。此测试锁死
    绝不再拼逗号多码。"""
    codes = ["000001.SZ", "000002.SZ", "600000.SH"]
    passed_ts_codes = []

    async def _capture_call(func, **kwargs):
        passed_ts_codes.append(kwargs.get("ts_code"))
        return pd.DataFrame({
            "ts_code": [kwargs.get("ts_code")],
            "ann_date": ["20250130"], "end_date": ["20241231"],
            "total_hldr_eqy_exc_min_int": [1_000_000.0],
        })

    with patch.object(adapter, "_call", new=_capture_call):
        result = await adapter.fetch_balance_sheet(
            codes, date(2024, 1, 1), date(2025, 1, 31)
        )

    assert passed_ts_codes == codes, "每次调用必须传单个 ts_code（顺序逐股）"
    for tc in passed_ts_codes:
        assert "," not in tc, f"ts_code 不得含逗号（多码陷阱）：{tc!r}"
    # 三只单码各 1 行 → 合并 3 行，total_equity 正常换算
    assert len(result) == 3
    assert abs(result.iloc[0]["total_equity"] - 1e10) < 1.0


@pytest.mark.asyncio
async def test_td_09_fetch_balance_sheet_empty_codes(adapter: TushareAdapter) -> None:
    """TD-09: ts_codes 为空列表时直接返回空 DataFrame"""
    call_mock = AsyncMock()
    with patch.object(adapter, "_call", new=call_mock):
        result = await adapter.fetch_balance_sheet([], date(2024, 1, 1), date(2025, 1, 31))

    call_mock.assert_not_called()
    assert len(result) == 0


@pytest.mark.asyncio
async def test_td_10_fetch_dividend_data_uses_correct_api(adapter: TushareAdapter) -> None:
    """TD-10（Bug 15 回归）：fetch_dividend_data 必须调 `pro.dividend`，不是不存在的
    `pro.fina_dividend`。锁住 API 名契约——后者真机调用返回"请指定正确的接口名"。"""
    mock_df = pd.DataFrame(
        {"ts_code": ["000001.SZ"], "ex_date": ["20260102"], "cash_div_tax": [0.5]}
    )

    captured_func = []

    async def _capture_call(func, **kwargs):
        captured_func.append(func)
        return mock_df

    with patch.object(adapter, "_call", new=_capture_call):
        result = await adapter.fetch_dividend_data(date(2026, 1, 2))

    # 锁定调用的是 _pro.dividend 这个绑定方法（不是 fina_dividend 或其他名字）
    assert len(captured_func) == 1
    assert captured_func[0] is adapter._pro.dividend, (
        "fetch_dividend_data 必须调 _pro.dividend（Tushare 正确接口名），"
        "禁止调 fina_dividend（不存在的接口名）"
    )
    # 顺带验证字段映射正常
    assert len(result) == 1
    assert result.iloc[0]["cash_div"] == 0.5
    assert result.iloc[0]["ex_date"] == date(2026, 1, 2)


@pytest.mark.asyncio
async def test_td_12_fetch_financial_data_uses_ts_code_batches(
    adapter: TushareAdapter,
) -> None:
    """TD-12（RM-17 回归）：fetch_financial_data 必须按 ts_code 分批调 fina_indicator，
    禁止 period-only 调用（Tushare 不支持，会让 roe/yoy 全 NULL）。"""
    mock_basic = pd.DataFrame(
        {
            "ts_code": [f"{i:06d}.SZ" for i in range(1, 75)],  # 74 只 → 2 批
            "pe_ttm": [12.0] * 74,
            "pb": [1.2] * 74,
            "dv_ttm": [3.0] * 74,
        }
    )
    mock_fina_batch = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "end_date": ["20250930"],
            "roe": [15.0],
            "netprofit_yoy": [10.0],
            "tr_yoy": [8.0],
            "debt_to_assets": [50.0],
        }
    )

    captured_calls: list[dict] = []

    async def _capture(func, **kwargs):
        captured_calls.append({"func": func, **kwargs})
        if func is adapter._pro.daily_basic:
            return mock_basic
        elif func is adapter._pro.fina_indicator:
            return mock_fina_batch
        return pd.DataFrame()

    with patch.object(adapter, "_call", new=_capture):
        result = await adapter.fetch_financial_data(date(2026, 1, 2))

    # 1 次 daily_basic + 2 次 fina_indicator（74 只 / 50 = 2 批）
    daily_basic_calls = [c for c in captured_calls if c["func"] is adapter._pro.daily_basic]
    fina_calls = [c for c in captured_calls if c["func"] is adapter._pro.fina_indicator]
    assert len(daily_basic_calls) == 1
    assert len(fina_calls) == 2, f"应分 2 批调 fina_indicator，实际 {len(fina_calls)} 次"
    # 每次 fina_indicator 调用必须含 period + ts_code 双参数（不能 period-only）
    for c in fina_calls:
        assert "period" in c, "必须传 period"
        assert "ts_code" in c, "必须传 ts_code（不能 period-only，Tushare 不支持）"
        assert "," in c["ts_code"] or c["ts_code"].count(".") == 1
    # roe 单位换算（% → 小数）仍正常
    row1 = result[result["ts_code"] == "000001.SZ"].iloc[0]
    assert abs(row1["roe"] - 0.15) < 1e-9
    assert row1["publish_date"] == date(2026, 1, 2)
    assert len(result) == 74  # basic 为主表，全部 74 只在结果中


@pytest.mark.asyncio
async def test_td_11_fetch_dividend_data_filters_other_dates(adapter: TushareAdapter) -> None:
    """TD-11：fetch_dividend_data 按 ex_date 精确过滤——Tushare 接口可能返回邻近日期
    的记录（取决于 server 行为），本方法必须只保留 ex_date == trade_date 的行。"""
    mock_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "ex_date": ["20260102", "20260103", "20260102"],
            "cash_div_tax": [0.5, 1.0, 0.0],  # 第 3 行 cash_div=0 应被过滤
        }
    )
    with patch.object(adapter, "_call", new=AsyncMock(return_value=mock_df)):
        result = await adapter.fetch_dividend_data(date(2026, 1, 2))

    assert len(result) == 1  # 仅 000001.SZ 满足 ex_date == 1/2 且 cash_div > 0
    assert result.iloc[0]["ts_code"] == "000001.SZ"


# ── V1.5-A A4（R13-P3-4）：TushareAdapter._call 统一 TUSHARE_CALLS Counter 埋点 ──


def _fresh_adapter() -> TushareAdapter:
    with patch("quantpilot.data.adapters.tushare.ts") as mock_ts:
        mock_ts.pro_api.return_value = MagicMock()
        return TushareAdapter(token="test-token")


async def test_a4_call_emits_tushare_calls_success() -> None:
    """A4-R13P3-4: _call 成功 → TUSHARE_CALLS{interface=func.__name__,status=success} +1。"""
    from quantpilot.core.metrics import TUSHARE_CALLS

    adp = _fresh_adapter()

    def fake_daily(**kwargs: object) -> pd.DataFrame:
        return pd.DataFrame({"ts_code": ["000001.SZ"]})

    fake_daily.__name__ = "daily"
    before = TUSHARE_CALLS.labels(interface="daily", status="success")._value.get()
    result = await adp._call(fake_daily, trade_date="20260102")
    after = TUSHARE_CALLS.labels(interface="daily", status="success")._value.get()
    assert after - before == 1
    assert not result.empty


async def test_a4_call_emits_tushare_calls_error_and_reraises() -> None:
    """A4-R13P3-4: _call 异常 → status=error +1 且原异常上抛（不吞）。"""
    import pytest

    from quantpilot.core.metrics import TUSHARE_CALLS

    adp = _fresh_adapter()

    def fake_err(**kwargs: object) -> pd.DataFrame:
        raise RuntimeError("boom")

    fake_err.__name__ = "fina_indicator"
    before = TUSHARE_CALLS.labels(interface="fina_indicator", status="error")._value.get()
    with pytest.raises(RuntimeError):
        await adp._call(fake_err)
    after = TUSHARE_CALLS.labels(interface="fina_indicator", status="error")._value.get()
    assert after - before == 1


async def test_a4_call_classifies_rate_limit() -> None:
    """A4-R13P3-4: Tushare 限流异常（含"每分钟"/"每天"/"最多访问"）→ status=rate_limit。"""
    import pytest

    from quantpilot.core.metrics import TUSHARE_CALLS

    adp = _fresh_adapter()

    def fake_rl(**kwargs: object) -> pd.DataFrame:
        raise Exception("抱歉，您每分钟最多访问该接口 500 次")

    fake_rl.__name__ = "daily_basic"
    before = TUSHARE_CALLS.labels(interface="daily_basic", status="rate_limit")._value.get()
    with pytest.raises(Exception):
        await adp._call(fake_rl)
    after = TUSHARE_CALLS.labels(interface="daily_basic", status="rate_limit")._value.get()
    assert after - before == 1


# ───────── ADP-07：suspend_d 参数名 + suspend_type 过滤（2026-09-02 生产缺陷回归） ─────────
#
# 缺陷：`suspend_d` 的参数是 `trade_date`，代码误写 `suspend_date` → Tushare
# **静默忽略未知参数**，返回全表最早 5000 行（1999~2001 年的停牌记录）。
# 后果：约 818 只正常交易股被当成停牌、自 Initial commit 起每日排除，
# universe 失真 −17%（`is_suspended` 假阳性 100% / 真阳性 0%，110 万行无一例外）。
# 详见 docs/reviews/universe_suspension_defect_2026-09-02.md
#
# ⚠️ 为什么本文件既有测试抓不到：`_call` 被 AsyncMock(side_effect=[...]) 替换，
# **不校验传入的 kwargs**，参数名写什么都能跑过；且 `suspend` 的 mock 连
# `suspend_type` 列都没有——替身比现实弱，那条分支在测试世界不可表达。
class TestSuspendParams:
    async def test_suspend_d_called_with_trade_date(self, adapter: TushareAdapter) -> None:
        """在**调用点**上验证参数名（§4.11：只能在调用点验证，spy 式测试是自证的）。"""
        daily, basic, adj, _suspend, limit = _make_daily_mocks()
        suspend = pd.DataFrame({"ts_code": [], "suspend_type": []})
        captured: list[dict] = []

        async def _spy(fn, **kwargs):
            captured.append({"fn": getattr(fn, "_mock_name", None) or str(fn), **kwargs})
            return [daily, basic, adj, suspend, limit][len(captured) - 1]

        with patch.object(adapter, "_call", new=_spy):
            await adapter.fetch_daily_quotes(date(2026, 1, 2))

        # 第 4 个调用是 suspend_d（顺序见 fetch_daily_quotes 的 asyncio.gather）
        kw = captured[3]
        assert "trade_date" in kw, f"suspend_d 必须用 trade_date，实得 {sorted(kw)}"
        assert "suspend_date" not in kw, "suspend_date 不是 suspend_d 的参数，会被静默忽略"
        assert kw["trade_date"] == "20260102"

    async def test_only_suspend_type_S_marks_suspended(
        self, adapter: TushareAdapter
    ) -> None:
        """S=停牌 / R=复牌，只有 S 才算停牌；复牌股必须可交易。"""
        daily, basic, adj, _s, limit = _make_daily_mocks()
        daily = pd.concat([daily, daily.assign(ts_code="000002.SZ")], ignore_index=True)
        basic = pd.concat([basic, basic.assign(ts_code="000002.SZ")], ignore_index=True)
        adj = pd.concat([adj, adj.assign(ts_code="000002.SZ")], ignore_index=True)
        suspend = pd.DataFrame(
            {"ts_code": ["000001.SZ", "000002.SZ"], "suspend_type": ["S", "R"]}
        )
        with patch.object(
            adapter, "_call",
            new=AsyncMock(side_effect=[daily, basic, adj, suspend, limit]),
        ):
            result = await adapter.fetch_daily_quotes(date(2026, 1, 2))

        flags = dict(zip(result["ts_code"], result["is_suspended"]))
        assert flags["000001.SZ"] is True or bool(flags["000001.SZ"]) is True
        assert bool(flags["000002.SZ"]) is False, "R=复牌被当成了停牌"

    async def test_missing_suspend_type_column_degrades_safely(
        self, adapter: TushareAdapter
    ) -> None:
        """接口若不返 suspend_type（旧版/降级），不得整体崩溃。

        【降级说明】此时无法区分 S/R → 保守起见**全部不标停牌**：
        误标停牌会把正常股票逐出 universe（本次缺陷的形态，代价已实证为 −17%），
        而漏标停牌无实际损害（真停牌股在 daily 接口里根本没有行情行，
        自然进不了 universe——评审报告 §4.1 已实证 BUY 落在零成交日 0 条）。
        """
        daily, basic, adj, _s, limit = _make_daily_mocks()
        suspend = pd.DataFrame({"ts_code": ["000001.SZ"]})  # 无 suspend_type
        with patch.object(
            adapter, "_call",
            new=AsyncMock(side_effect=[daily, basic, adj, suspend, limit]),
        ):
            result = await adapter.fetch_daily_quotes(date(2026, 1, 2))
        assert bool(result.iloc[0]["is_suspended"]) is False
