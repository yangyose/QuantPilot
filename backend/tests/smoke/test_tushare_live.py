"""Tushare 接口冒烟测试 — 验证所有用到的 API 字段正确性。

运行条件：必须设置 TUSHARE_TOKEN 环境变量。
用法：
    TUSHARE_TOKEN=xxx uv run pytest tests/smoke/ -v

测试策略：
    - 使用稳定的历史日期（2024-12-31 为 A 股交易日）
    - 第一层：验证原始 Tushare API 返回的字段名（SMOKE-RAW-xx）
    - 第二层：验证 TushareAdapter 方法输出的字段和单位换算（SMOKE-ADAPTER-xx）
    - 不写入数据库，纯 IO 验证
    - adapter 层结果用 module-scope fixture 缓存，每个方法只调用一次，避免速率限制
"""
from __future__ import annotations

import asyncio
import os
from datetime import date

import pandas as pd
import pytest
import tushare as ts

from quantpilot.data.adapters.tushare import TushareAdapter

# ── 跳过条件 ─────────────────────────────────────────────────────────────────
TOKEN = os.getenv("TUSHARE_TOKEN", "")
pytestmark = pytest.mark.skipif(
    not TOKEN,
    reason="需要设置 TUSHARE_TOKEN 环境变量才能运行冒烟测试",
)

# 稳定历史日期（2024-12-31 为 A 股交易日，数据已落定）
TEST_DATE = date(2024, 12, 31)
TEST_DATE_STR = "20241231"
# 短区间：2024-12-16 ~ 2024-12-31（含若干交易日）
START_DATE = date(2024, 12, 16)
START_DATE_STR = "20241216"

# 测试用指数（沪深300，权重数据最完整）
TEST_INDEX = "000300.SH"

# 财务数据测试用季报期（TEST_DATE 所在季度末）
PERIOD_STR = "20240930"  # 2024-Q3

# 验证 fina_indicator 字段结构时用的单只股票（平安银行，数据完整）
SAMPLE_STOCK = "000001.SZ"


# ── 原始 API fixtures（module scope）─────────────────────────────────────────

@pytest.fixture(scope="module")
def pro() -> ts.pro_api:  # type: ignore[name-defined]
    """原始 Tushare Pro API 实例（用于 RAW 层测试）"""
    return ts.pro_api(TOKEN)


# ── TushareAdapter fixture + 缓存 fixtures（避免重复调用触发速率限制）────────

@pytest.fixture(scope="module")
def adapter() -> TushareAdapter:
    """TushareAdapter 实例（用于 ADAPTER 层测试）"""
    return TushareAdapter(token=TOKEN, max_concurrent=1)


@pytest.fixture(scope="module")
def stock_list_df(adapter: TushareAdapter) -> pd.DataFrame:
    """module-scope: fetch_stock_list 结果缓存"""
    return asyncio.run(adapter.fetch_stock_list())


@pytest.fixture(scope="module")
def daily_quotes_df(adapter: TushareAdapter) -> pd.DataFrame:
    """module-scope: fetch_daily_quotes 结果缓存（只调用一次）"""
    return asyncio.run(adapter.fetch_daily_quotes(TEST_DATE))


@pytest.fixture(scope="module")
def financial_data_df(adapter: TushareAdapter) -> pd.DataFrame:
    """module-scope: fetch_financial_data 结果缓存（只调用一次）"""
    return asyncio.run(adapter.fetch_financial_data(TEST_DATE))


@pytest.fixture(scope="module")
def index_history_df(adapter: TushareAdapter) -> pd.DataFrame:
    """module-scope: fetch_index_history 结果缓存"""
    return asyncio.run(adapter.fetch_index_history(TEST_INDEX, START_DATE, TEST_DATE))


@pytest.fixture(scope="module")
def trade_calendar_list(adapter: TushareAdapter) -> list[date]:
    """module-scope: fetch_trade_calendar 结果缓存"""
    return asyncio.run(adapter.fetch_trade_calendar(START_DATE, TEST_DATE))


@pytest.fixture(scope="module")
def index_components_list(adapter: TushareAdapter) -> list[str]:
    """module-scope: fetch_index_components 结果缓存"""
    return asyncio.run(adapter.fetch_index_components(TEST_INDEX, TEST_DATE))


@pytest.fixture(scope="module")
def namechange_df(adapter: TushareAdapter) -> pd.DataFrame:
    """module-scope: fetch_namechange 结果缓存"""
    return asyncio.run(adapter.fetch_namechange(START_DATE, TEST_DATE))


# ══════════════════════════════════════════════════════════════════════════════
# SMOKE-RAW: 直接调用 Tushare API，验证字段存在性
# ══════════════════════════════════════════════════════════════════════════════


def test_raw_01_stock_basic_listed(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-01: stock_basic(list_status='L') 返回上市股票及必要字段"""
    df = pro.stock_basic(
        list_status="L",
        fields="ts_code,name,industry,market,list_date,list_status",
    )
    assert isinstance(df, pd.DataFrame), "stock_basic 应返回 DataFrame"
    assert len(df) > 0, "上市股票数量应大于 0"

    required = {"ts_code", "name", "industry", "market", "list_date", "list_status"}
    missing = required - set(df.columns)
    assert not missing, f"stock_basic(L) 缺少字段: {missing}"

    assert df["ts_code"].notna().all(), "ts_code 不应有空值"
    assert (df["list_status"] == "L").all(), "list_status 应全为 'L'"
    assert len(df) > 4000, f"上市股票应超过 4000 只，实际 {len(df)}"


def test_raw_02_stock_basic_delisted(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-02: stock_basic(list_status='D') 返回退市股票及必要字段"""
    df = pro.stock_basic(
        list_status="D",
        fields="ts_code,name,industry,market,list_date,delist_date,list_status",
    )
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, "应有退市股票记录"

    required = {"ts_code", "name", "list_date", "delist_date", "list_status"}
    missing = required - set(df.columns)
    assert not missing, f"stock_basic(D) 缺少字段: {missing}"

    assert (df["list_status"] == "D").all(), "list_status 应全为 'D'"


def test_raw_03_daily(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-03: daily(trade_date) 返回当日全量行情及价格字段"""
    df = pro.daily(trade_date=TEST_DATE_STR)
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, "日线行情不应为空"

    required = {"ts_code", "trade_date", "open", "high", "low", "close",
                "pre_close", "pct_chg", "vol", "amount"}
    missing = required - set(df.columns)
    assert not missing, f"daily 缺少字段: {missing}"

    assert df["close"].notna().any(), "close 不应全为空"
    assert df["vol"].notna().any(), "vol 不应全为空"
    # A股/深交所上限 ±20%，北交所新股上市首日允许 ±30%，用 ±31% 作为安全阈值
    valid_pct = df["pct_chg"].dropna()
    assert (valid_pct.abs() <= 31).all(), f"pct_chg 存在超出±31% 的异常值: {valid_pct.abs().max()}"


def test_raw_04_daily_basic_quotes(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-04: daily_basic 行情字段 — turnover_rate / circ_mv"""
    df = pro.daily_basic(
        trade_date=TEST_DATE_STR,
        fields="ts_code,turnover_rate,circ_mv",
    )
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0

    required = {"ts_code", "turnover_rate", "circ_mv"}
    missing = required - set(df.columns)
    assert not missing, f"daily_basic(行情字段) 缺少字段: {missing}"

    valid_tr = df["turnover_rate"].dropna()
    assert (valid_tr >= 0).all(), "turnover_rate 不应为负"
    assert (valid_tr <= 100).all(), f"turnover_rate 不应超过 100%，最大值: {valid_tr.max()}"


def test_raw_05_daily_basic_financial(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-05: daily_basic 财务字段 — pe_ttm / pb / dv_ttm"""
    df = pro.daily_basic(
        trade_date=TEST_DATE_STR,
        fields="ts_code,pe_ttm,pb,dv_ttm",
    )
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0

    required = {"ts_code", "pe_ttm", "pb", "dv_ttm"}
    missing = required - set(df.columns)
    assert not missing, f"daily_basic(财务字段) 缺少字段: {missing}"

    valid_pb = df["pb"].dropna()
    assert (valid_pb >= 0).all(), f"pb 不应为负，最小值: {valid_pb.min()}"


def test_raw_06_adj_factor(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-06: adj_factor 返回复权因子，ts_code + adj_factor 必须存在"""
    df = pro.adj_factor(trade_date=TEST_DATE_STR)
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, "复权因子不应为空"

    required = {"ts_code", "adj_factor"}
    missing = required - set(df.columns)
    assert not missing, f"adj_factor 缺少字段: {missing}"

    valid_af = df["adj_factor"].dropna()
    assert (valid_af > 0).all(), f"adj_factor 应全为正数，最小值: {valid_af.min()}"


def test_raw_07_suspend_d(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-07: suspend_d 停牌数据包含 ts_code 字段"""
    df = pro.suspend_d(suspend_date=TEST_DATE_STR)
    assert isinstance(df, pd.DataFrame)
    assert "ts_code" in df.columns, "suspend_d 应含 ts_code 列"


def test_raw_08_limit_list_d(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-08: limit_list_d 涨跌停包含 ts_code / limit，值域为 'U'/'D'/'Z'

    注意：Tushare 实际返回列名为 "limit"（非文档中的 "limit_type"），
    值域：U=涨停，D=跌停，Z=炸板（曾触及涨跌停后打开）。
    """
    df = pro.limit_list_d(trade_date=TEST_DATE_STR)
    assert isinstance(df, pd.DataFrame)
    if len(df) > 0:
        assert "ts_code" in df.columns, "limit_list_d 应含 ts_code 列"
        assert "limit" in df.columns, (
            f"limit_list_d 应含 'limit' 列（非 'limit_type'），实际列: {list(df.columns)}"
        )
        valid_types = {"U", "D", "Z"}
        actual_types = set(df["limit"].dropna().unique())
        assert actual_types.issubset(valid_types), (
            f"limit 只应含 'U'/'D'/'Z'，实际: {actual_types}"
        )


def test_raw_09_fina_indicator(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-09: fina_indicator 财务指标包含所有必要字段

    已知限制：fina_indicator 不支持仅凭 period 做全市场查询（必须传 ts_code）。
    此处用 SAMPLE_STOCK 验证字段结构；全市场批量方案留待 Phase 4 专项解决。
    """
    # 注：total_hldr_eqy_exc_min_int（总股东权益）属于 balancesheet API，
    # 不在 fina_indicator 返回列中，已从字段列表中移除。
    df = pro.fina_indicator(
        ts_code=SAMPLE_STOCK,
        period=PERIOD_STR,
        fields="ts_code,end_date,roe,netprofit_yoy,tr_yoy,debt_to_assets",
    )
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, f"fina_indicator({SAMPLE_STOCK}, period={PERIOD_STR}) 不应为空"

    required = {"ts_code", "end_date", "roe", "netprofit_yoy", "tr_yoy", "debt_to_assets"}
    missing = required - set(df.columns)
    assert not missing, f"fina_indicator 缺少字段: {missing}"


def test_raw_10_index_daily(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-10: index_daily 指数行情包含所有 OHLCV 字段"""
    df = pro.index_daily(
        ts_code=TEST_INDEX,
        start_date=START_DATE_STR,
        end_date=TEST_DATE_STR,
    )
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, f"index_daily({TEST_INDEX}) 不应为空"

    required = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"}
    missing = required - set(df.columns)
    assert not missing, f"index_daily 缺少字段: {missing}"

    valid_pct = df["pct_chg"].dropna()
    assert (valid_pct.abs() <= 15).all(), (
        f"index_daily pct_chg 存在异常值（超过±15%），最大: {valid_pct.abs().max()}"
    )


def test_raw_11_trade_cal(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-11: trade_cal 交易日历包含 cal_date / is_open 字段"""
    df = pro.trade_cal(
        exchange="SSE",
        start_date=START_DATE_STR,
        end_date=TEST_DATE_STR,
    )
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0

    required = {"cal_date", "is_open"}
    missing = required - set(df.columns)
    assert not missing, f"trade_cal 缺少字段: {missing}"

    assert df["is_open"].isin([0, 1]).all(), "is_open 应只含 0 或 1"
    row = df[df["cal_date"] == TEST_DATE_STR]
    assert len(row) == 1, f"trade_cal 中应有 {TEST_DATE_STR} 记录"
    assert row.iloc[0]["is_open"] == 1, f"{TEST_DATE_STR} 应为交易日"


def test_raw_12_index_weight(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-12: index_weight 成分股权重包含 con_code 字段"""
    df = pro.index_weight(
        index_code=TEST_INDEX,
        trade_date=TEST_DATE_STR,
    )
    assert isinstance(df, pd.DataFrame)
    if len(df) > 0:
        assert "con_code" in df.columns, "index_weight 应含 con_code 列"
        assert df["con_code"].notna().all(), "con_code 不应有空值"
        assert 200 <= len(df) <= 400, (
            f"沪深300 成分股数量异常，期望 200~400，实际 {len(df)}"
        )
    else:
        pytest.skip(f"index_weight({TEST_INDEX}, {TEST_DATE_STR}) 返回空（免费接口限制），跳过验证")


def test_raw_13_namechange(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-13: namechange 历史改名包含所有必要字段"""
    df = pro.namechange(
        start_date=START_DATE_STR,
        end_date=TEST_DATE_STR,
        fields="ts_code,name,start_date,end_date",
    )
    assert isinstance(df, pd.DataFrame)

    required = {"ts_code", "name", "start_date", "end_date"}
    missing = required - set(df.columns)
    assert not missing, f"namechange 缺少字段: {missing}"

    if len(df) > 0:
        assert df["ts_code"].notna().all(), "namechange ts_code 不应有空值"
        assert df["name"].notna().all(), "namechange name 不应有空值"
        assert df["start_date"].notna().all(), "namechange start_date 不应有空值"


# ══════════════════════════════════════════════════════════════════════════════
# SMOKE-ADAPTER: 验证 TushareAdapter 方法输出字段 + 单位换算正确性
# （使用 module-scope 缓存 fixture，每个方法只调用一次，避免速率限制）
# ══════════════════════════════════════════════════════════════════════════════


def test_adapter_01_fetch_stock_list(stock_list_df: pd.DataFrame) -> None:
    """ADAPTER-01: fetch_stock_list 输出标准列 + is_active 类型"""
    df = stock_list_df
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 4000, f"合并股票列表应超过 4000 只，实际 {len(df)}"

    required = {"ts_code", "name", "market", "sw_industry_l1", "list_date", "is_active"}
    missing = required - set(df.columns)
    assert not missing, f"fetch_stock_list 缺少标准列: {missing}"

    assert df["is_active"].dtype == bool, "is_active 应为 bool 类型"
    assert df["is_active"].any(), "应有上市股票（is_active=True）"

    valid_ld = df["list_date"].dropna()
    assert all(isinstance(d, date) for d in valid_ld), "list_date 应为 date 类型"


def test_adapter_02_fetch_daily_quotes_columns(daily_quotes_df: pd.DataFrame) -> None:
    """ADAPTER-02: fetch_daily_quotes 输出全部标准列"""
    df = daily_quotes_df
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0

    required = {
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pct_chg", "vol", "amount", "turnover_rate", "float_mkt_cap",
        "adj_factor", "is_suspended", "is_st", "limit_up", "limit_down",
    }
    missing = required - set(df.columns)
    assert not missing, f"fetch_daily_quotes 缺少标准列: {missing}"


def test_adapter_03_fetch_daily_quotes_unit_pct_chg(daily_quotes_df: pd.DataFrame) -> None:
    """ADAPTER-03: pct_chg 已从百分比换算为小数（值域 -1 ~ 1，北交所允许 ±0.31）"""
    valid_pct = daily_quotes_df["pct_chg"].dropna()
    assert (valid_pct.abs() < 1.0).all(), (
        f"pct_chg 应已换算为小数（<1），最大绝对值: {valid_pct.abs().max()}"
    )


def test_adapter_04_fetch_daily_quotes_unit_vol(daily_quotes_df: pd.DataFrame) -> None:
    """ADAPTER-04: vol 已从手换算为股（vol >= 100 for active stocks）"""
    valid_vol = daily_quotes_df["vol"].dropna()
    valid_vol = valid_vol[valid_vol > 0]
    assert (valid_vol >= 100).all(), (
        f"vol 已换算为股，应 >= 100，最小值: {valid_vol.min()}"
    )


def test_adapter_05_fetch_daily_quotes_unit_amount(daily_quotes_df: pd.DataFrame) -> None:
    """ADAPTER-05: amount 已从千元换算为元（大多数股票 amount > 1_000_000）"""
    valid_amt = daily_quotes_df["amount"].dropna()
    valid_amt = valid_amt[valid_amt > 0]
    large_trades = (valid_amt > 1_000_000).sum()
    assert large_trades > len(valid_amt) * 0.5, (
        "超过半数有成交的股票 amount 应大于 100 万元（千元→元换算后）"
    )


def test_adapter_06_fetch_daily_quotes_unit_turnover_rate(daily_quotes_df: pd.DataFrame) -> None:
    """ADAPTER-06: turnover_rate 已从百分比换算为小数（< 1.0）"""
    valid_tr = daily_quotes_df["turnover_rate"].dropna()
    valid_tr = valid_tr[valid_tr > 0]
    assert (valid_tr < 1.0).all(), (
        f"turnover_rate 应已换算为小数（<1），最大值: {valid_tr.max()}"
    )


def test_adapter_07_fetch_daily_quotes_unit_float_mkt_cap(daily_quotes_df: pd.DataFrame) -> None:
    """ADAPTER-07: float_mkt_cap 已从万元换算为元（大多数股票 > 1 亿元）"""
    valid_cap = daily_quotes_df["float_mkt_cap"].dropna()
    valid_cap = valid_cap[valid_cap > 0]
    large_cap = (valid_cap > 1e8).sum()
    assert large_cap > len(valid_cap) * 0.5, (
        "超过半数股票 float_mkt_cap 应大于 1 亿元（万元→元换算后）"
    )


def test_adapter_08_fetch_daily_quotes_adj_factor_positive(daily_quotes_df: pd.DataFrame) -> None:
    """ADAPTER-08: adj_factor 应全为正数"""
    valid_af = daily_quotes_df["adj_factor"].dropna()
    assert (valid_af > 0).all(), (
        f"adj_factor 应全为正数，最小值: {valid_af.min()}"
    )


def test_adapter_09_fetch_daily_quotes_bool_flags(daily_quotes_df: pd.DataFrame) -> None:
    """ADAPTER-09: is_suspended / is_st / limit_up / limit_down 应为 bool 类型"""
    for col in ("is_suspended", "is_st", "limit_up", "limit_down"):
        assert daily_quotes_df[col].dtype == bool, (
            f"{col} 应为 bool 类型，实际 {daily_quotes_df[col].dtype}"
        )


def test_adapter_10_fetch_financial_data_columns(financial_data_df: pd.DataFrame) -> None:
    """ADAPTER-10: fetch_financial_data 输出标准列（含单位换算后字段）"""
    df = financial_data_df
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0

    required = {
        "ts_code", "publish_date", "report_period",
        "pe_ttm", "pb", "dividend_yield",
        "roe", "net_profit_yoy", "revenue_yoy",
        "total_equity", "debt_to_asset",
    }
    missing = required - set(df.columns)
    assert not missing, f"fetch_financial_data 缺少标准列: {missing}"


def test_adapter_11_fetch_financial_data_daily_basic_units(
    financial_data_df: pd.DataFrame,
) -> None:
    """ADAPTER-11: daily_basic 来源字段（pe_ttm/pb）值域合理（已换算）"""
    df = financial_data_df
    # pb > 0（来自 daily_basic，直接使用，无换算）
    valid_pb = df["pb"].dropna()
    assert (valid_pb >= 0).all(), f"pb 不应为负，最小值: {valid_pb.min()}"

    # dividend_yield 已从百分比换算为小数（dv_ttm / 100）
    valid_dy = df["dividend_yield"].dropna()
    if len(valid_dy) > 0:
        median_abs = valid_dy.abs().median()
        assert median_abs < 0.5, (
            f"dividend_yield 中位数={median_abs:.4f}，应为小数形式（<0.5）"
        )


def test_adapter_12_fetch_financial_data_publish_date(
    financial_data_df: pd.DataFrame,
) -> None:
    """ADAPTER-12: publish_date 应等于 as_of_date（PIT 语义）"""
    assert (financial_data_df["publish_date"] == TEST_DATE).all(), (
        "publish_date 应全等于 as_of_date（PIT 入库时点）"
    )


def test_adapter_13_fetch_financial_data_fina_limitation(
    financial_data_df: pd.DataFrame,
) -> None:
    """ADAPTER-13: 确认 fina_indicator 字段（roe/net_profit_yoy 等）为 NaN（已知限制）

    Tushare fina_indicator 不支持仅凭 period 的全市场查询，适配器降级处理后
    这些字段为 NaN。Phase 4 前需专项解决（逐股批量查询或替代接口）。
    """
    df = financial_data_df
    # roe 等字段应存在（列存在）但全为 NaN（降级填充）
    assert "roe" in df.columns, "roe 列应存在"
    assert "net_profit_yoy" in df.columns, "net_profit_yoy 列应存在"
    assert df["roe"].isna().all(), (
        "fina_indicator 全市场查询不可用时 roe 应全为 NaN（已知限制）"
    )


def test_adapter_14_fetch_index_history_columns(index_history_df: pd.DataFrame) -> None:
    """ADAPTER-14: fetch_index_history 输出标准列 + 单位换算"""
    df = index_history_df
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0

    required = {"index_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"}
    missing = required - set(df.columns)
    assert not missing, f"fetch_index_history 缺少标准列: {missing}"

    assert "ts_code" not in df.columns, "index_code 列应已由 ts_code 重命名"

    valid_pct = df["pct_chg"].dropna()
    assert (valid_pct.abs() < 1.0).all(), (
        f"index pct_chg 应已换算为小数，最大绝对值: {valid_pct.abs().max()}"
    )


def test_adapter_15_fetch_index_history_sorted(index_history_df: pd.DataFrame) -> None:
    """ADAPTER-15: fetch_index_history 结果按 trade_date 升序排列"""
    dates = index_history_df["trade_date"].tolist()
    assert dates == sorted(dates), "fetch_index_history 应按 trade_date 升序排列"


def test_adapter_16_fetch_trade_calendar(trade_calendar_list: list[date]) -> None:
    """ADAPTER-16: fetch_trade_calendar 返回升序 date 列表，2024-12-31 应在其中"""
    result = trade_calendar_list
    assert isinstance(result, list)
    assert len(result) > 0

    assert all(isinstance(d, date) for d in result), "结果应全为 date 对象"
    assert result == sorted(result), "trade_calendar 应按升序排列"
    assert TEST_DATE in result, f"{TEST_DATE} 应为交易日"
    # 2024-12-21 是周六，不应为交易日
    saturday = date(2024, 12, 21)
    assert saturday not in result, f"{saturday}（周六）不应在交易日历中"


def test_adapter_17_fetch_index_components(index_components_list: list[str]) -> None:
    """ADAPTER-17: fetch_index_components 返回升序 ts_code 列表"""
    result = index_components_list
    if not result:
        pytest.skip(f"fetch_index_components({TEST_INDEX}) 返回空列表（免费接口限制），跳过")

    assert isinstance(result, list)
    assert all(isinstance(c, str) for c in result), "成分股列表应全为 str 类型"
    assert result == sorted(result), "成分股列表应按升序排列"
    assert 200 <= len(result) <= 400, (
        f"沪深300 成分股数量异常，期望 200~400，实际 {len(result)}"
    )
    for code in result[:5]:
        assert "." in code and code.split(".")[1] in ("SH", "SZ"), (
            f"ts_code 格式异常: {code}"
        )


def test_adapter_18_fetch_namechange(namechange_df: pd.DataFrame) -> None:
    """ADAPTER-18: fetch_namechange 输出标准列，date 字段已转换"""
    df = namechange_df
    assert isinstance(df, pd.DataFrame)

    required = {"ts_code", "name", "start_date", "end_date"}
    missing = required - set(df.columns)
    assert not missing, f"fetch_namechange 缺少标准列: {missing}"

    if len(df) > 0:
        valid_sd = df["start_date"].dropna()
        assert all(isinstance(d, date) for d in valid_sd), (
            "start_date 应已转换为 date 类型"
        )
        valid_ed = df["end_date"].dropna()
        assert all(isinstance(d, date) for d in valid_ed), (
            "end_date 非空值应已转换为 date 类型"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4 TD 修复冒烟测试
# RAW-14~16：验证 Tushare API 原始字段结构
# ADAPTER-19~21：验证 TushareAdapter Phase 4 新方法输出
# ══════════════════════════════════════════════════════════════════════════════

# ── 新增 module-scope fixtures（各只调用一次，避免速率限制）──────────────────────

@pytest.fixture(scope="module")
def stock_industry_df(adapter: TushareAdapter) -> pd.DataFrame:
    """module-scope: fetch_stock_industry 结果缓存（TD-3）"""
    return asyncio.run(adapter.fetch_stock_industry())


@pytest.fixture(scope="module")
def financial_by_stock_df(adapter: TushareAdapter) -> pd.DataFrame:
    """module-scope: fetch_financial_by_stock 结果缓存（TD-1，单股样本）"""
    return asyncio.run(
        adapter.fetch_financial_by_stock(
            [SAMPLE_STOCK],
            date(2023, 1, 1),
            TEST_DATE,
        )
    )


@pytest.fixture(scope="module")
def balance_sheet_df(adapter: TushareAdapter) -> pd.DataFrame:
    """module-scope: fetch_balance_sheet 结果缓存（TD-2，单股样本）"""
    return asyncio.run(
        adapter.fetch_balance_sheet(
            [SAMPLE_STOCK],
            date(2023, 1, 1),
            TEST_DATE,
        )
    )


# ── RAW-14：index_classify + index_member（TD-3 申万行业分类）──────────────────
# 注：Tushare `stock_industry` API 不存在（error 40101 "请指定正确的接口名"）。
# 正确路径：index_classify(level='L1', src='SW2021') → 31 个 L1 行业 index_code；
# index_member(index_code=L1_code, is_new='Y') → 当前成分股。

def test_raw_14a_index_classify_l1(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-14a: index_classify(level='L1', src='SW2021') 返回申万31个一级行业"""
    df = pro.index_classify(level="L1", src="SW2021")
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, "index_classify(L1) 不应为空"

    required = {"index_code", "industry_name", "industry_code"}
    missing = required - set(df.columns)
    assert not missing, f"index_classify 缺少字段: {missing}"

    assert df["index_code"].notna().all(), "index_code 不应有空值"
    assert df["industry_name"].notna().all(), "industry_name 不应有空值"
    # SW2021 共 31 个 L1 行业
    assert 28 <= len(df) <= 35, f"申万 L1 行业数量应为 28~35，实际 {len(df)}"


def test_raw_14b_index_member(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-14b: index_member(index_code=L1_code, is_new='Y') 返回成分股 con_code"""
    l1_df = pro.index_classify(level="L1", src="SW2021")
    sample_code = l1_df["index_code"].iloc[0]

    df = pro.index_member(index_code=sample_code, is_new="Y")
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, f"index_member({sample_code}) 不应为空"

    assert "con_code" in df.columns, "index_member 应含 con_code 列"
    assert df["con_code"].notna().all(), "con_code 不应有空值"
    # 验证 ts_code 格式
    for code in df["con_code"].iloc[:3]:
        assert "." in code, f"con_code 应为 ts_code 格式（含'.'），实际: {code}"


# ── RAW-15：fina_indicator 逐股查询（TD-1 ROE 验证）────────────────────────────

def test_raw_15_fina_indicator_per_stock(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-15: fina_indicator 按 ts_code + 日期区间查询，roe/netprofit_yoy 有实际数据"""
    df = pro.fina_indicator(
        ts_code=SAMPLE_STOCK,
        start_date="20230101",
        end_date=TEST_DATE_STR,
        fields="ts_code,ann_date,end_date,roe,netprofit_yoy,tr_yoy,debt_to_assets",
    )
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, f"fina_indicator({SAMPLE_STOCK}, 按日期区间) 不应为空"

    required = {"ts_code", "ann_date", "end_date", "roe", "netprofit_yoy", "tr_yoy",
                "debt_to_assets"}
    missing = required - set(df.columns)
    assert not missing, f"fina_indicator(逐股) 缺少字段: {missing}"

    # 逐股查询时 roe 应有实际数据（非全 NaN）
    valid_roe = df["roe"].dropna()
    assert len(valid_roe) > 0, (
        f"fina_indicator({SAMPLE_STOCK}) 逐股查询时 roe 应有有效数据，当前全为 NaN"
    )


# ── RAW-16：balancesheet API（TD-2 净资产验证）──────────────────────────────────

def test_raw_16_balancesheet_fields(pro: ts.pro_api) -> None:  # type: ignore[name-defined]
    """RAW-16: balancesheet 包含 total_hldr_eqy_exc_min_int（总股东权益）"""
    df = pro.balancesheet(
        ts_code=SAMPLE_STOCK,
        start_date="20230101",
        end_date=TEST_DATE_STR,
        fields="ts_code,ann_date,end_date,total_hldr_eqy_exc_min_int",
    )
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, f"balancesheet({SAMPLE_STOCK}) 不应为空"

    required = {"ts_code", "ann_date", "end_date", "total_hldr_eqy_exc_min_int"}
    missing = required - set(df.columns)
    assert not missing, f"balancesheet 缺少字段: {missing}"

    valid_equity = df["total_hldr_eqy_exc_min_int"].dropna()
    assert len(valid_equity) > 0, "total_hldr_eqy_exc_min_int 应有有效数据"
    assert (valid_equity > 0).any(), "平安银行净资产应为正数"


# ── ADAPTER-19：fetch_stock_industry（TD-3）─────────────────────────────────────

def test_adapter_19_fetch_stock_industry_columns(stock_industry_df: pd.DataFrame) -> None:
    """ADAPTER-19: fetch_stock_industry 输出 ts_code/sw_industry_l1/sw_industry_l2 三列

    实现使用 index_classify(L1) + index_member；sw_industry_l2 当前全为 None（L2 未使用）。
    """
    df = stock_industry_df
    if df.empty:
        pytest.skip("fetch_stock_industry 返回空，跳过验证")

    assert list(df.columns) == ["ts_code", "sw_industry_l1", "sw_industry_l2"], (
        f"列名不符，实际: {list(df.columns)}"
    )
    assert df["ts_code"].notna().all(), "ts_code 不应有空值"
    assert df["sw_industry_l1"].notna().all(), "sw_industry_l1 不应有空值"
    assert len(df) > 3000, f"申万行业分类记录应超过 3000 条，实际 {len(df)}"

    # 验证申万一级行业名称包含典型分类
    industries = set(df["sw_industry_l1"].unique())
    expected_industries = {"银行", "医药生物", "计算机", "电子"}
    found = expected_industries & industries
    assert len(found) >= 2, f"申万行业分类应包含典型分类，找到: {found}"


# ── ADAPTER-20：fetch_financial_by_stock（TD-1）──────────────────────────────────

def test_adapter_20_fetch_financial_by_stock_columns(
    financial_by_stock_df: pd.DataFrame,
) -> None:
    """ADAPTER-20: fetch_financial_by_stock 输出标准列（含单位换算）"""
    df = financial_by_stock_df
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, f"fetch_financial_by_stock({SAMPLE_STOCK}) 不应为空"

    required = {"ts_code", "publish_date", "report_period", "roe",
                "net_profit_yoy", "revenue_yoy", "debt_to_asset"}
    missing = required - set(df.columns)
    assert not missing, f"fetch_financial_by_stock 缺少标准列: {missing}"


def test_adapter_20b_fetch_financial_by_stock_unit_conversion(
    financial_by_stock_df: pd.DataFrame,
) -> None:
    """ADAPTER-20b: roe/net_profit_yoy 已从百分比换算为小数（中位数绝对值 < 1）"""
    df = financial_by_stock_df
    valid_roe = df["roe"].dropna()
    if len(valid_roe) == 0:
        pytest.skip("roe 全为空，跳过单位验证")

    # 换算后 roe 应为小数（平安银行近年 ROE 约 10%~15% → 换算后 0.10~0.15）
    median_roe = valid_roe.abs().median()
    assert median_roe < 1.0, (
        f"roe 中位数={median_roe:.4f}，应为小数形式（÷100 后 < 1），"
        f"若 > 1 说明未换算"
    )


def test_adapter_20c_fetch_financial_by_stock_publish_date_type(
    financial_by_stock_df: pd.DataFrame,
) -> None:
    """ADAPTER-20c: publish_date 和 report_period 应为 date 类型"""
    df = financial_by_stock_df
    valid_pd = df["publish_date"].dropna()
    assert all(isinstance(d, date) for d in valid_pd), (
        "publish_date 应为 date 类型"
    )
    valid_rp = df["report_period"].dropna()
    assert all(isinstance(d, date) for d in valid_rp), (
        "report_period 应为 date 类型"
    )


# ── ADAPTER-21：fetch_balance_sheet（TD-2）──────────────────────────────────────

def test_adapter_21_fetch_balance_sheet_columns(balance_sheet_df: pd.DataFrame) -> None:
    """ADAPTER-21: fetch_balance_sheet 输出 ts_code/publish_date/report_period/total_equity"""
    df = balance_sheet_df
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0, f"fetch_balance_sheet({SAMPLE_STOCK}) 不应为空"

    required = {"ts_code", "publish_date", "report_period", "total_equity"}
    missing = required - set(df.columns)
    assert not missing, f"fetch_balance_sheet 缺少标准列: {missing}"


def test_adapter_21b_fetch_balance_sheet_unit_conversion(
    balance_sheet_df: pd.DataFrame,
) -> None:
    """ADAPTER-21b: total_equity 已从万元换算为元（平安银行净资产应超过 1 万亿元）"""
    df = balance_sheet_df
    valid_equity = df["total_equity"].dropna()
    if len(valid_equity) == 0:
        pytest.skip("total_equity 全为空，跳过单位验证")

    # 平安银行总股东权益约 5000 亿元（5e11），换算后应远大于 1e10
    max_equity = valid_equity.max()
    assert max_equity > 1e10, (
        f"total_equity 最大值={max_equity:.2e}，"
        f"应 > 1e10（万元→元换算后，平安银行净资产应超过千亿）"
    )


def test_adapter_21c_fetch_balance_sheet_date_types(balance_sheet_df: pd.DataFrame) -> None:
    """ADAPTER-21c: publish_date 和 report_period 应为 date 类型"""
    df = balance_sheet_df
    valid_pd = df["publish_date"].dropna()
    assert all(isinstance(d, date) for d in valid_pd), "publish_date 应为 date 类型"
    valid_rp = df["report_period"].dropna()
    assert all(isinstance(d, date) for d in valid_rp), "report_period 应为 date 类型"
