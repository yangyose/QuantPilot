"""REPO-01~04: MarketDataRepository 集成测试（需要真实 PostgreSQL）"""
from datetime import date

import pandas as pd
import pytest

from quantpilot.data.repository import MarketDataRepository


@pytest.fixture
def repo(db_session):
    return MarketDataRepository(db_session)


@pytest.mark.asyncio
async def test_repo_01_upsert_stock_list(repo: MarketDataRepository) -> None:
    """REPO-01: upsert_stock_list 批量插入 → 查询确认行数"""
    df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["平安银行", "万科A"],
            "market": ["MAIN", "MAIN"],
            "sw_industry_l1": [None, None],
            "sw_industry_l2": [None, None],
            "list_date": [date(1991, 4, 3), date(1991, 1, 29)],
            "delist_date": [None, None],
            "is_active": [True, True],
        }
    )
    count = await repo.upsert_stock_list(df)
    assert count == 2

    codes = await repo.get_active_stock_codes()
    assert "000001.SZ" in codes
    assert "000002.SZ" in codes


@pytest.mark.asyncio
async def test_repo_02_upsert_daily_quotes_idempotent(repo: MarketDataRepository) -> None:
    """REPO-02: 重复 upsert 同一天数据 → 不报错，数据被更新（幂等性）"""
    df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": [date(2026, 1, 2)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "pre_close": [10.0],
            "pct_chg": [0.05],
            "vol": [100_000],
            "amount": [1_000_000.0],
            "turnover_rate": [0.01],
            "float_mkt_cap": [1e10],
            "adj_factor": [1.0],
            "is_suspended": [False],
            "is_st": [False],
            "limit_up": [False],
            "limit_down": [False],
        }
    )
    count1 = await repo.upsert_daily_quotes(df)
    assert count1 == 1

    # 第二次 upsert（更新 close）
    df2 = df.copy()
    df2["close"] = [10.8]
    count2 = await repo.upsert_daily_quotes(df2)
    assert count2 == 1  # 仍返回 1（upsert 行数）


@pytest.mark.asyncio
async def test_repo_03_get_latest_financial_pit(repo: MarketDataRepository) -> None:
    """REPO-03: get_latest_financial PIT 查询 → 不返回 as_of_date 之后的公告"""
    df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "report_period": [date(2025, 6, 30), date(2025, 9, 30)],
            "publish_date": [date(2025, 8, 30), date(2026, 1, 5)],  # 第二条晚于 as_of
            "pe_ttm": [12.0, 13.0],
            "pb": [1.0, 1.1],
            "roe": [0.12, 0.13],
            "net_profit_yoy": [0.1, 0.1],
            "revenue_yoy": [0.08, 0.09],
            "dividend_yield": [0.03, 0.03],
            "total_equity": [1e10, 1e10],
            "debt_to_asset": [0.5, 0.5],
        }
    )
    await repo.upsert_financial_data(df)

    as_of = date(2026, 1, 2)
    result = await repo.get_latest_financial(["000001.SZ"], as_of_date=as_of)

    assert len(result) == 1
    # ts_code 是索引（RM-17 修复后 set_index）；publish_date 仍是 column
    assert result.index[0] == "000001.SZ"
    # 只能拿到 publish_date=2025-08-30 的记录
    assert result.iloc[0]["publish_date"] == date(2025, 8, 30)


# ── 方案 A：get_latest_financial 基本面 LOCF（2026-08 生产缺陷修复）──────────────

_FIN_COLS = [
    "ts_code", "report_period", "publish_date", "pe_ttm", "pb", "roe",
    "net_profit_yoy", "revenue_yoy", "dividend_yield", "total_equity", "debt_to_asset",
]


def _fin_row(
    ts_code, report_period, publish_date, *,
    pe=float("nan"), pb=float("nan"), roe=float("nan"),
    teq=float("nan"), npyoy=float("nan"),
) -> dict:
    """构造一条 financial_data 行（数值缺省 NaN，与真实采集一致；非 None 避免 object 列）。"""
    return {
        "ts_code": ts_code, "report_period": report_period, "publish_date": publish_date,
        "pe_ttm": pe, "pb": pb, "roe": roe, "net_profit_yoy": npyoy,
        "revenue_yoy": float("nan"), "dividend_yield": float("nan"),
        "total_equity": teq, "debt_to_asset": float("nan"),
    }


@pytest.mark.asyncio
async def test_repo_03b_latest_financial_locf_vacuum(repo: MarketDataRepository) -> None:
    """方案A：真空期（Q2 日频行基本面全 NULL）→ 基本面 LOCF 回落 Q1，日频取最新交易日。"""
    q1, q2 = date(2025, 3, 31), date(2025, 6, 30)
    rows = [
        # Q1 已披露：roe + total_equity 落在日频行（4-6 月）
        _fin_row("000001.SZ", q1, date(2025, 4, 30),
                 pe=10.0, pb=1.0, roe=0.15, teq=1e10, npyoy=0.2),
        _fin_row("000001.SZ", q1, date(2025, 6, 27),
                 pe=10.5, pb=1.05, roe=0.15, teq=1e10, npyoy=0.2),
        # Q2 未披露：report_period 已滚到 Q2，但 roe/total_equity 全 NULL（日频快照）
        _fin_row("000001.SZ", q2, date(2025, 7, 31), pe=11.0, pb=1.1),
        _fin_row("000001.SZ", q2, date(2025, 8, 29), pe=11.5, pb=1.15),
    ]
    await repo.upsert_financial_data(pd.DataFrame(rows, columns=_FIN_COLS))

    res = await repo.get_latest_financial(["000001.SZ"], as_of_date=date(2025, 9, 1))
    assert len(res) == 1
    r = res.iloc[0]
    # 日频段：取最新交易日 08-29 的 pe/pb
    assert r["publish_date"] == date(2025, 8, 29)
    assert float(r["pe_ttm"]) == 11.5
    # 基本面段：LOCF 回落 Q1（真空期），A5b 据此判定 forecast(Q2) > 本期(Q1) = 真空
    assert r["report_period"] == q1
    assert float(r["roe"]) == pytest.approx(0.15)
    assert float(r["total_equity"]) == pytest.approx(1e10)


@pytest.mark.asyncio
async def test_repo_03c_latest_financial_locf_merges_split_rows(
    repo: MarketDataRepository,
) -> None:
    """方案A：同报告期 roe(日频行) 与 total_equity(公告日行) 分行 → max 聚合合成一条。"""
    q2 = date(2025, 6, 30)
    rows = [
        # 日频行：roe 有、total_equity NULL，publish_date=交易日
        _fin_row("000002.SZ", q2, date(2025, 8, 15), pe=12.0, pb=1.2, roe=0.18, npyoy=0.25),
        # 公告日行：total_equity 有、roe NULL，publish_date=ann_date（更早，选不中日频段）
        _fin_row("000002.SZ", q2, date(2025, 8, 10), teq=3e10),
    ]
    await repo.upsert_financial_data(pd.DataFrame(rows, columns=_FIN_COLS))

    res = await repo.get_latest_financial(["000002.SZ"], as_of_date=date(2025, 9, 1))
    r = res.iloc[0]
    assert r["report_period"] == q2
    assert float(r["roe"]) == pytest.approx(0.18)          # 来自日频行
    assert float(r["total_equity"]) == pytest.approx(3e10)  # 来自公告日行（分行经 max 合并）
    # 日频段取最新 publish_date=08-15
    assert r["publish_date"] == date(2025, 8, 15)
    assert float(r["pe_ttm"]) == 12.0


@pytest.mark.asyncio
async def test_repo_03d_latest_financial_locf_beyond_lookback(
    repo: MarketDataRepository,
) -> None:
    """方案A：基本面 publish_date 早于 LOCF 回看窗口 → 基本面 NaN，日频仍返回（降级）。"""
    rows = [
        # 唯一有基本面的行远早于 lookback（as_of-450d ≈ 2025-05）
        _fin_row("000003.SZ", date(2022, 12, 31), date(2023, 1, 1),
                 pe=9.0, pb=0.9, roe=0.1, teq=5e9, npyoy=0.05),
        # 近期日频行无基本面
        _fin_row("000003.SZ", date(2026, 6, 30), date(2026, 8, 3), pe=9.5, pb=0.95),
    ]
    await repo.upsert_financial_data(pd.DataFrame(rows, columns=_FIN_COLS))

    res = await repo.get_latest_financial(["000003.SZ"], as_of_date=date(2026, 8, 5))
    r = res.iloc[0]
    assert r["publish_date"] == date(2026, 8, 3)  # 日频段仍返回最新交易日行
    assert float(r["pe_ttm"]) == 9.5
    assert pd.isna(r["report_period"])            # 基本面超窗口 → 缺失
    assert pd.isna(r["roe"])
    assert pd.isna(r["total_equity"])


@pytest.mark.asyncio
async def test_repo_03e_upsert_dedups_duplicate_conflict_keys(
    repo: MarketDataRepository,
) -> None:
    """回填路径回归：fina_indicator 对同一 (ts_code, report_period, publish_date)
    会返回重复行（原始 + 修订，ann_date 相同）。upsert 必须批内按冲突键去重，否则
    单条 INSERT...ON CONFLICT 触发 CardinalityViolationError（cannot affect row a
    second time）。2026-08-07 生产回填实证：全批 fail、total_equity 空转。"""
    period, pub = date(2025, 3, 31), date(2025, 4, 30)
    rows = [
        _fin_row("000009.SZ", period, pub, pe=10.0, pb=1.0, roe=0.10, teq=1e10),
        # 同一冲突键的重复行（修订值）→ 必须保留 last（keep="last"）
        _fin_row("000009.SZ", period, pub, pe=10.0, pb=1.0, roe=0.12, teq=2e10),
    ]
    # 未去重会抛 CardinalityViolationError；去重修复后正常返回
    await repo.upsert_financial_data(pd.DataFrame(rows, columns=_FIN_COLS))

    res = await repo.get_latest_financial(["000009.SZ"], as_of_date=date(2025, 9, 1))
    assert len(res) == 1
    r = res.iloc[0]
    # 保留 last：roe=0.12 / total_equity=2e10
    assert float(r["roe"]) == pytest.approx(0.12)
    assert float(r["total_equity"]) == pytest.approx(2e10)


@pytest.mark.asyncio
async def test_repo_03f_upsert_drops_null_conflict_key_rows(
    repo: MarketDataRepository,
) -> None:
    """回填路径回归：fina_indicator/balancesheet 按区间取时，部分报告期 ann_date 为
    NULL（未公告占位行）→ publish_date NULL。publish_date 是 NOT NULL 冲突键，直接 upsert
    触发 NotNullViolationError 整批 fail。upsert 必须先丢弃冲突键任一为 NULL 的行（无
    ann_date = 无 PIT 锚点，本就不可入库）。2026-08-10 生产 total_equity 回填实证暴露。"""
    period = date(2025, 3, 31)
    rows = [
        # 合法行：三个冲突键齐全 → 入库
        _fin_row("000010.SZ", period, date(2025, 4, 30), pe=10.0, pb=1.0, roe=0.11, teq=1e10),
        # publish_date=None（未公告）→ 必须被丢弃，不得触发 NotNullViolationError
        _fin_row("000010.SZ", period, None, pe=10.0, pb=1.0, roe=0.09, teq=9e9),
    ]
    # 未过滤会抛 NotNullViolationError；过滤修复后正常返回
    await repo.upsert_financial_data(pd.DataFrame(rows, columns=_FIN_COLS))

    res = await repo.get_latest_financial(["000010.SZ"], as_of_date=date(2025, 9, 1))
    assert len(res) == 1
    r = res.iloc[0]
    # 只有合法行入库：roe=0.11 / total_equity=1e10
    assert float(r["roe"]) == pytest.approx(0.11)
    assert float(r["total_equity"]) == pytest.approx(1e10)


@pytest.mark.asyncio
async def test_repo_03g_upsert_clamps_total_equity_overflow(
    repo: MarketDataRepository,
) -> None:
    """回填路径回归：total_equity 是 Numeric(18,2)（max abs 1e16 元）。Tushare 偶发脏值
    ≥1e16 会溢出整批 INSERT（NumericValueOutOfRangeError）。upsert 必须把越界 total_equity
    clamp 成 NULL（不丢整批）。2026-08-10 生产回填实证：含脏值股的整 50-批失败 ≈10% 覆盖损失。"""
    period, pub = date(2025, 3, 31), date(2025, 4, 30)
    rows = [
        # 正常大市值：total_equity=4e12（工行级）→ 保留
        _fin_row("000011.SZ", period, pub, pe=8.0, pb=0.8, roe=0.13, teq=4e12),
        # 脏值：total_equity=1e17 超 Numeric(18,2) 上界 → 必须 clamp 成 NULL，不得溢出整批
        _fin_row("000012.SZ", period, pub, pe=9.0, pb=0.9, roe=0.14, teq=1e17),
    ]
    # 未 clamp 会抛 NumericValueOutOfRangeError；clamp 后正常返回
    await repo.upsert_financial_data(pd.DataFrame(rows, columns=_FIN_COLS))

    # get_latest_financial index=ts_code，直接按 code 取
    full = await repo.get_latest_financial(
        ["000011.SZ", "000012.SZ"], as_of_date=date(2025, 9, 1)
    )
    assert len(full) == 2
    normal = full.loc["000011.SZ"]
    dirty = full.loc["000012.SZ"]
    assert float(normal["total_equity"]) == pytest.approx(4e12)  # 正常值保留
    assert pd.isna(dirty["total_equity"])                        # 脏值 clamp 成 NULL
    assert float(dirty["roe"]) == pytest.approx(0.14)            # 该行其他字段仍入库


async def test_repo_a5_forecast_upsert_and_pit(repo: MarketDataRepository) -> None:
    """REPO-A5（V1.5-A A5）：upsert_financial_forecast 幂等 + get_latest_forecast PIT。

    同 (ts_code, 20251231) 有 forecast(priority1) + express(priority2) →
    get_latest_forecast 取 data_priority 高者（express）；PIT 不返回 as_of 之后的发布。
    """
    df = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
        "report_period": [date(2025, 12, 31), date(2025, 12, 31), date(2025, 9, 30)],
        "pre_announce_date": [date(2026, 2, 20), date(2026, 2, 28), date(2025, 10, 30)],
        "est_net_profit": [9.0e7, 1.0e8, 8.0e7],
        "est_net_profit_yoy": [0.18, 0.20, 0.10],
        "data_priority": [1, 2, 1],
        "source_type": ["forecast", "express", "forecast"],
    })
    n1 = await repo.upsert_financial_forecast(df)
    assert n1 == 3
    # 幂等：再 upsert 同数据不新增行
    await repo.upsert_financial_forecast(df)

    # as_of 覆盖到 2/28（express 已发）→ 取 20251231 express（priority 2 优先）
    result = await repo.get_latest_forecast(["000001.SZ"], as_of_date=date(2026, 3, 1))
    assert len(result) == 1
    assert result.index[0] == "000001.SZ"
    assert result.iloc[0]["report_period"] == date(2025, 12, 31)
    assert result.iloc[0]["source_type"] == "express"
    assert float(result.iloc[0]["est_net_profit"]) == 1.0e8

    # PIT：as_of 在 express 发布前（2/25）→ 取 20251231 forecast（唯一已发布该期）
    result2 = await repo.get_latest_forecast(["000001.SZ"], as_of_date=date(2026, 2, 25))
    assert result2.iloc[0]["source_type"] == "forecast"

    # PIT：as_of 早于所有 20251231 发布（1/1）→ 回退到 20250930 forecast
    result3 = await repo.get_latest_forecast(["000001.SZ"], as_of_date=date(2026, 1, 1))
    assert result3.iloc[0]["report_period"] == date(2025, 9, 30)


@pytest.mark.asyncio
async def test_repo_05_active_codes_as_of_pit(repo: MarketDataRepository) -> None:
    """REPO-05: get_active_stock_codes_as_of PIT 过滤 — RM-18 修复。

    场景：stock_info 含 3 只股票：
      - A：2018 上市，未退市（应在所有 PIT 查询里）
      - B：2022 上市，未退市（2021-05-13 查不到，2023-06-01 能查到）
      - C：2015 上市，2020-12-31 退市（2019 能查到，2021 查不到）

    断言：
      - PIT(2021-05-13) → {A}
      - PIT(2019-06-01) → {A, C}
      - PIT(2023-06-01) → {A, B}
    """
    df = pd.DataFrame(
        {
            "ts_code": ["A.SZ", "B.SZ", "C.SZ"],
            "name": ["A", "B", "C"],
            "market": ["MAIN", "MAIN", "MAIN"],
            "sw_industry_l1": [None, None, None],
            "sw_industry_l2": [None, None, None],
            "list_date": [date(2018, 1, 1), date(2022, 1, 1), date(2015, 1, 1)],
            "delist_date": [None, None, date(2020, 12, 31)],
            "is_active": [True, True, False],
        }
    )
    await repo.upsert_stock_list(df)

    codes_2021 = set(await repo.get_active_stock_codes_as_of(date(2021, 5, 13)))
    assert codes_2021 == {"A.SZ"}, f"2021-05-13 应只 A（B 未上市/C 已退市），得 {codes_2021}"

    codes_2019 = set(await repo.get_active_stock_codes_as_of(date(2019, 6, 1)))
    assert codes_2019 == {"A.SZ", "C.SZ"}, f"2019-06-01 应 A+C，得 {codes_2019}"

    codes_2023 = set(await repo.get_active_stock_codes_as_of(date(2023, 6, 1)))
    assert codes_2023 == {"A.SZ", "B.SZ"}, f"2023-06-01 应 A+B，得 {codes_2023}"


@pytest.mark.asyncio
async def test_repo_04_upsert_index_history_ohlcv(repo: MarketDataRepository) -> None:
    """REPO-04: upsert_index_history + get_index_history 范围查询，含 high/low 字段"""
    df = pd.DataFrame(
        {
            "index_code": ["000300.SH", "000300.SH"],
            "trade_date": [date(2026, 1, 2), date(2026, 1, 5)],
            "open": [4000.0, 4010.0],
            "high": [4050.0, 4060.0],
            "low": [3980.0, 3990.0],
            "close": [4020.0, 4030.0],
            "vol": [1_000_000, 1_100_000],
            "pct_chg": [0.005, 0.0025],
        }
    )
    await repo.upsert_index_history(df)

    result = await repo.get_index_history("000300.SH", date(2026, 1, 1), date(2026, 1, 10))
    assert len(result) == 2
    assert "high" in result.columns
    assert "low" in result.columns
    assert float(result.iloc[0]["high"]) == pytest.approx(4050.0)
