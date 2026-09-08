"""前视偏差的**数据层**检测：采集侧堵住之后，还要能查出「污染有没有回来」。

## 为什么源头修了还要这个

`_truncate_to_announced` 让采集不再写入未公告的基本面（`5f7d57a`），
但它挡不住：换个人跑旧版脚本、恢复一份旧备份、Tushare 改字段名导致
fail-closed 之外的路径、或某次手工 SQL。这些都会让 318 万行那种污染悄悄回来，
而**代码是对的、测试是绿的、没有任何告警**。

C-6 的判据是「下次同样的错误会被哪一处拦住」。源头拦的是**产生**，
本检测拦的是**存在**——两者缺一不可。

判据取「公告日之前的行是否已有基本面值」，公告日由 `total_equity` 锚点推得
（该代理已用实时采集的那一期校准到 0.00 天误差，见
`docs/reviews/financial_data_lookahead_2026-09-07.md` §5）。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from quantpilot.data.repository import MarketDataRepository

_COLS = [
    "ts_code", "report_period", "publish_date", "pe_ttm", "pb", "roe",
    "net_profit_yoy", "revenue_yoy", "dividend_yield", "total_equity", "debt_to_asset",
]


@pytest.fixture
def repo(db_session):
    return MarketDataRepository(db_session)


def _row(code, period, pub, *, roe=float("nan"), yoy=float("nan"), teq=float("nan")):
    return {
        "ts_code": code, "report_period": period, "publish_date": pub,
        "pe_ttm": 10.0, "pb": 1.0, "roe": roe, "net_profit_yoy": yoy,
        "revenue_yoy": float("nan"), "dividend_yield": float("nan"),
        "total_equity": teq, "debt_to_asset": float("nan"),
    }


async def _count(session) -> int:
    from scripts.audit_data_integrity import lookahead_violations  # noqa: PLC0415

    return await lookahead_violations(session)


async def test_la_01_clean_data_reports_zero(repo, db_session) -> None:
    """公告日当天才出现基本面 → 0 违规（这是修复后的正确形态）。"""
    q = date(2025, 6, 30)
    await repo.upsert_financial_data(pd.DataFrame([
        # 公告前：只有行情，基本面 NULL
        _row("000801.SZ", q, date(2025, 7, 15)),
        # 公告日：total_equity 锚点 + 基本面同时出现
        _row("000801.SZ", q, date(2025, 8, 28), roe=0.15, yoy=10.0, teq=1e10),
        _row("000801.SZ", q, date(2025, 8, 29), roe=0.15, yoy=10.0),
    ], columns=_COLS))
    await db_session.flush()
    assert await _count(db_session) == 0


async def test_la_02_detects_value_before_announcement(repo, db_session) -> None:
    """公告日之前就有基本面值 → 必须被查出来。

    这正是 2021-05 ~ 2026-05 那 318 万行的形态。
    """
    q = date(2025, 6, 30)
    await repo.upsert_financial_data(pd.DataFrame([
        # ⚠️ 7-15 就带着 8-28 才公布的值——污染本体
        _row("000802.SZ", q, date(2025, 7, 15), roe=0.15, yoy=10.0),
        _row("000802.SZ", q, date(2025, 8, 28), roe=0.15, yoy=10.0, teq=1e10),
    ], columns=_COLS))
    await db_session.flush()
    assert await _count(db_session) == 1


async def test_la_03_no_anchor_is_not_flagged(repo, db_session) -> None:
    """无 `total_equity` 锚点 → 无法判定公告日 → **不报**（不猜）。

    与修复脚本「原样保留」的口径必须一致，否则审计会对着修复脚本
    有意不动的那 7974 个 (股票,报告期) 长期报警，然后被当成噪声忽略——
    一个恒亮的告警等于没有告警。
    """
    q = date(2025, 6, 30)
    await repo.upsert_financial_data(pd.DataFrame([
        _row("000803.SZ", q, date(2025, 7, 15), roe=0.15, yoy=10.0),
    ], columns=_COLS))
    await db_session.flush()
    assert await _count(db_session) == 0


async def test_la_04_only_pre_announcement_rows_count(repo, db_session) -> None:
    """公告日**当天及之后**的行有值是正常的，不得计入。"""
    q = date(2025, 6, 30)
    await repo.upsert_financial_data(pd.DataFrame([
        _row("000804.SZ", q, date(2025, 8, 28), roe=0.15, yoy=10.0, teq=1e10),
        _row("000804.SZ", q, date(2025, 9, 1), roe=0.15, yoy=10.0),
        _row("000804.SZ", q, date(2025, 9, 2), roe=0.15, yoy=10.0),
    ], columns=_COLS))
    await db_session.flush()
    assert await _count(db_session) == 0
