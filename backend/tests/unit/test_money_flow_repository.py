"""MF-REPO-01~03: MarketDataRepository 的 money_flow 写入（V1.5-C C4 / 设计 §6.3）。

设计 §6.3 点名的坑：`moneyflow` 单日 ~5500 行 × 8 列 ≈ 44000 占位符 > asyncpg 上限
32767 → **用真实日数据就会踩到**，不需要合成大数据。故本夹具用**真实规模**（5548 行，
2026-09-15 实测行数）喂 upsert，并按 postgresql 方言真编译每一批的语句数占位符。
只跑 ≤ 3000 行的 mock 会绕过这个 bug（CLAUDE.md §4.1）。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
from sqlalchemy.dialects import postgresql

from quantpilot.data.repository import MarketDataRepository

_ASYNCPG_PARAM_CAP = 32767
_REAL_DAY_ROWS = 5548


def _day_frame(n: int, trade_date: date = date(2026, 9, 15)) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    codes = [f"{i:06d}.SZ" for i in range(n)]
    df = pd.DataFrame({
        "ts_code": codes,
        "trade_date": [trade_date] * n,
        "net_mf_amount": rng.normal(0, 1e7, n),
        "buy_elg_amount": rng.uniform(0, 1e8, n),
        "sell_elg_amount": rng.uniform(0, 1e8, n),
        "buy_lg_amount": rng.uniform(0, 1e8, n),
        "sell_lg_amount": rng.uniform(0, 1e8, n),
    })
    # 真实数据无 NaN，但脏值路径（adapter 的 errors="coerce"）会产生；混一个进来验 NaN→NULL
    df.loc[0, "net_mf_amount"] = np.nan
    return df


def _make_repo() -> tuple[MarketDataRepository, list]:
    session = MagicMock()
    executed: list = []

    async def _execute(stmt):
        executed.append(stmt)
        res = MagicMock()
        res.rowcount = len(stmt.compile(dialect=postgresql.dialect()).params) // 8 or 1
        return res

    session.execute = AsyncMock(side_effect=_execute)
    return MarketDataRepository(session), executed


async def test_mf_repo_01_real_day_size_stays_under_asyncpg_param_cap() -> None:
    """5548 行真实规模：每一批编译后的占位符数必须 < 32767，且批数 = ceil(n/500)。"""
    repo, executed = _make_repo()
    df = _day_frame(_REAL_DAY_ROWS)

    total = await repo.upsert_money_flow(df)

    assert len(executed) == -(-_REAL_DAY_ROWS // 500)  # ceil
    for stmt in executed:
        n_params = len(stmt.compile(dialect=postgresql.dialect()).params)
        assert n_params < _ASYNCPG_PARAM_CAP, n_params
    assert total > 0


async def test_mf_repo_02_nan_becomes_none_not_nan_literal() -> None:
    """NaN 必须以 None（SQL NULL）进语句，不能是 float('nan')——后者进 NUMERIC 是 'NaN'≠NULL。"""
    repo, executed = _make_repo()
    df = _day_frame(3)

    await repo.upsert_money_flow(df)

    params = executed[0].compile(dialect=postgresql.dialect()).params
    nan_like = [k for k, v in params.items()
                if isinstance(v, float) and v != v]
    assert not nan_like, nan_like
    assert any(v is None for k, v in params.items() if k.startswith("net_mf_amount"))


async def test_mf_repo_03_upsert_is_on_conflict_update_all_amount_cols() -> None:
    """ON CONFLICT (ts_code, trade_date) DO UPDATE，且 5 个金额列都在 SET 白名单里
    （§4.1 白名单陷阱：漏一列则该列永远是首次写入的旧值，不报错）。"""
    repo, executed = _make_repo()
    await repo.upsert_money_flow(_day_frame(2))

    sql = str(executed[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (ts_code, trade_date) DO UPDATE" in sql
    for col in ("net_mf_amount", "buy_elg_amount", "sell_elg_amount",
                "buy_lg_amount", "sell_lg_amount"):
        assert f"{col} = excluded.{col}" in sql, col
    assert "updated_at = now()" in sql


async def test_mf_repo_04_empty_frame_is_noop() -> None:
    repo, executed = _make_repo()
    empty = pd.DataFrame(columns=["ts_code", "trade_date", "net_mf_amount"])
    assert await repo.upsert_money_flow(empty) == 0
    assert executed == []
