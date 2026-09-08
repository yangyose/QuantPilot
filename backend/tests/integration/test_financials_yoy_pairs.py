"""`get_financials_yoy_pairs`：F-Score 所需的**同比**配对取数（V1.5-C C2）。

## 为什么不能复用 `get_latest_n_financials(n=2)`

那个取的是最近两个**报告期** = **环比**（如 2025-06-30 vs 2025-03-31）。
而 Piotroski 的 5 个 Δ 项一律是**同比上年同期**（2025-06-30 vs 2024-06-30）——
季报口径下环比有季节性偏误：H1 与 Q1 本就不可比，用环比会把季节性读成基本面变化。

## PIT

两端都必须满足 `publish_date <= as_of_date`。同比期取不到 → 该股只返回 current 行，
`compute_f_score` 会把 5 个同比项记 NaN（**不是 0**），缺 ≥3 项即判「不可判」。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from quantpilot.data.repository import MarketDataRepository

_COLS = [
    "ts_code", "report_period", "publish_date", "pe_ttm", "pb", "roe",
    "net_profit_yoy", "revenue_yoy", "dividend_yield", "total_equity",
    "debt_to_asset", "roa", "ocfps", "eps", "current_ratio",
    "grossprofit_margin", "assets_turn", "total_share",
]


@pytest.fixture
def repo(db_session):
    return MarketDataRepository(db_session)


def _row(code, period, pub, roa):
    return {
        "ts_code": code, "report_period": period, "publish_date": pub,
        "pe_ttm": 10.0, "pb": 1.0, "roe": 0.1, "net_profit_yoy": 0.1,
        "revenue_yoy": 0.1, "dividend_yield": float("nan"), "total_equity": 1e10,
        "debt_to_asset": 0.4, "roa": roa, "ocfps": 1.0, "eps": 0.9,
        "current_ratio": 2.0, "grossprofit_margin": 0.3, "assets_turn": 0.8,
        "total_share": 1e9,
    }


async def test_yoy_01_pairs_same_month_day_previous_year(repo, db_session) -> None:
    """current=2025-06-30 → yoy 必须是 **2024-06-30**，不是 2025-03-31。"""
    await repo.upsert_financial_data(pd.DataFrame([
        _row("000901.SZ", date(2024, 6, 30), date(2024, 8, 28), 0.05),
        _row("000901.SZ", date(2025, 3, 31), date(2025, 4, 28), 0.06),
        _row("000901.SZ", date(2025, 6, 30), date(2025, 8, 28), 0.08),
    ], columns=_COLS))
    await db_session.flush()

    df = await repo.get_financials_yoy_pairs(["000901.SZ"], date(2025, 9, 1))
    assert set(df.index.get_level_values("period_tag")) == {"current", "yoy"}
    assert float(df.loc[("000901.SZ", "current"), "roa"]) == pytest.approx(0.08)
    assert float(df.loc[("000901.SZ", "yoy"), "roa"]) == pytest.approx(0.05), (
        "取成了环比（2025-03-31）—— Piotroski 的 Δ 一律是同比"
    )


async def test_yoy_02_pit_excludes_unpublished(repo, db_session) -> None:
    """`publish_date > as_of_date` 的期不得进入任何一端。"""
    await repo.upsert_financial_data(pd.DataFrame([
        _row("000902.SZ", date(2024, 6, 30), date(2024, 8, 28), 0.05),
        _row("000902.SZ", date(2025, 6, 30), date(2025, 8, 28), 0.08),
    ], columns=_COLS))
    await db_session.flush()

    # as_of 在 2025 半年报公告之前 → current 应回落到 2024-06-30
    df = await repo.get_financials_yoy_pairs(["000902.SZ"], date(2025, 7, 15))
    assert float(df.loc[("000902.SZ", "current"), "roa"]) == pytest.approx(0.05)
    assert ("000902.SZ", "yoy") not in df.index, "2023 同比期不存在，不应凭空出现"


async def test_yoy_03_missing_prior_year_returns_current_only(repo, db_session) -> None:
    """次新股无上年同期 → 只返回 current，**不报错、不补零**。"""
    await repo.upsert_financial_data(pd.DataFrame([
        _row("000903.SZ", date(2025, 6, 30), date(2025, 8, 28), 0.08),
    ], columns=_COLS))
    await db_session.flush()

    df = await repo.get_financials_yoy_pairs(["000903.SZ"], date(2025, 9, 1))
    assert ("000903.SZ", "current") in df.index
    assert ("000903.SZ", "yoy") not in df.index


async def test_yoy_04_carries_all_seven_piotroski_columns(repo, db_session) -> None:
    """7 个 C2 字段必须都在返回里——少一列，对应的 F-Score 项就永远不可判。"""
    await repo.upsert_financial_data(pd.DataFrame([
        _row("000904.SZ", date(2024, 6, 30), date(2024, 8, 28), 0.05),
        _row("000904.SZ", date(2025, 6, 30), date(2025, 8, 28), 0.08),
    ], columns=_COLS))
    await db_session.flush()

    df = await repo.get_financials_yoy_pairs(["000904.SZ"], date(2025, 9, 1))
    for c in ("roa", "ocfps", "eps", "current_ratio", "grossprofit_margin",
              "assets_turn", "total_share", "debt_to_asset"):
        assert c in df.columns, f"缺列 {c}"


async def test_yoy_05_empty_codes_returns_empty(repo, db_session) -> None:
    df = await repo.get_financials_yoy_pairs([], date(2025, 9, 1))
    assert df.empty


def _placeholder(code, period, pub):
    """未披露报告期的**占位行**：`pe_ttm`/`pb` 有值（当日真实行情），基本面全 NULL。

    这是 `financial_data` 的真实形态，也是今天两处缺陷的共同成因
    （F-5「连续两期」名存实亡 / `total_equity` 共用期被挡）。
    """
    r = _row(code, period, pub, float("nan"))
    for c in ("roe", "net_profit_yoy", "revenue_yoy", "total_equity",
              "debt_to_asset", "ocfps", "eps", "current_ratio",
              "grossprofit_margin", "assets_turn"):
        r[c] = float("nan")
    return r


async def test_yoy_06_placeholder_period_must_not_be_taken_as_current(
    repo, db_session
) -> None:
    """⚠️ 未披露期的占位行**不得**被当成 current。

    这条是补写的：首版测试没有占位行这种输入，导致「去掉 HAVING」的变异
    **没有被拦住**——5 条测试全绿。而占位行恰恰是生产里的常态形态
    （2026-08-25 实测：87% 的股票最近一期就是占位行）。
    又一次「测试替身/输入比现实更配合」（CLAUDE.md §4.11 第 7 类）。

    没有 HAVING 的话，current 会取到 2025-09-30 那条全 NULL 的占位行 →
    F-Score 全项不可判 → 门控永远不生效，而**没有任何报错**。
    """
    await repo.upsert_financial_data(pd.DataFrame([
        _row("000905.SZ", date(2024, 6, 30), date(2024, 8, 28), 0.05),
        _row("000905.SZ", date(2025, 6, 30), date(2025, 8, 28), 0.08),
        # 期已滚到 Q3、尚未披露：每天一条基本面全 NULL 的占位行
        _placeholder("000905.SZ", date(2025, 9, 30), date(2025, 10, 9)),
        _placeholder("000905.SZ", date(2025, 9, 30), date(2025, 10, 10)),
    ], columns=_COLS))
    await db_session.flush()

    df = await repo.get_financials_yoy_pairs(["000905.SZ"], date(2025, 10, 15))
    cur = df.loc[("000905.SZ", "current")]
    assert cur["report_period"] == date(2025, 6, 30), (
        f"current 取到了未披露的占位期 {cur['report_period']} —— F-Score 会全项不可判"
    )
    assert float(cur["roa"]) == pytest.approx(0.08)
    assert float(df.loc[("000905.SZ", "yoy"), "roa"]) == pytest.approx(0.05)


async def test_yoy_07_upsert_actually_persists_all_seven_columns(
    repo, db_session
) -> None:
    """⚠️ 7 个 C2 列必须真的写进库——`_FINANCIAL_UPDATE_COLS` 是**白名单**。

    不加进白名单时：列在表里、值在 DataFrame 里、SQL 就是不写它，
    **没有任何报错**。C2 的 7 列第一次跑就撞上（同日适配器 `fina_cols` 同形态，
    今天第三次遇到这个模式）。

    判据必须是「查库读回来的值」，不是「upsert 没抛异常」——后者在缺陷仍在时恒真。
    """
    from sqlalchemy import text

    await repo.upsert_financial_data(pd.DataFrame([
        _row("000906.SZ", date(2025, 6, 30), date(2025, 8, 28), 0.0777)
    ], columns=_COLS))
    await db_session.flush()

    got = (await db_session.execute(text(
        "SELECT roa, ocfps, eps, current_ratio, grossprofit_margin,"
        " assets_turn, total_share FROM financial_data"
        " WHERE ts_code='000906.SZ' AND report_period='2025-06-30'"
    ))).one()
    assert float(got[0]) == pytest.approx(0.0777), "roa 被白名单静默丢弃"
    assert float(got[1]) == pytest.approx(1.0)      # ocfps
    assert float(got[2]) == pytest.approx(0.9)      # eps
    assert float(got[3]) == pytest.approx(2.0)      # current_ratio
    assert float(got[4]) == pytest.approx(0.3)      # grossprofit_margin
    assert float(got[5]) == pytest.approx(0.8)      # assets_turn
    assert float(got[6]) == pytest.approx(1e9)      # total_share
