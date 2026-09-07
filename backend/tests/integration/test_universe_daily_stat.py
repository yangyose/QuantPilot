"""universe_daily_stat 落库（CLAUDE.md §6 可观测性缺口）。

判据不是「代码里调了 upsert」，而是**库里真的有那一行、且数字对得上**——
§4.11 元判据：一个机制若"设计上应当生效"，就去查它生效时留下的痕迹。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from quantpilot.data.repository import MarketDataRepository


@pytest.fixture
def repo(db_session):
    return MarketDataRepository(db_session)


async def test_uds_01_upsert_writes_row(repo: MarketDataRepository, db_session) -> None:
    await repo.upsert_universe_daily_stat(
        trade_date=date(2026, 9, 7),
        total_in=5500, total_out=3212,
        excluded={"F-1": 100, "F-2": 30, "F-3": 0, "F-4": 18,
                  "F-5": 900, "F-6": 40, "F-7": 1200, "F-8": 0},
        after_blacklist=3210,
    )
    await db_session.flush()
    row = (await db_session.execute(text(
        "SELECT total_in, total_out, after_blacklist, excluded "
        "FROM universe_daily_stat WHERE trade_date = '2026-09-07'"
    ))).one()
    assert row[0] == 5500
    assert row[1] == 3212
    assert row[2] == 3210
    assert row[3]["F-4"] == 18
    # 边际计数的不变量：各条之和 == 剔除总数
    assert sum(row[3].values()) == 5500 - 3212


async def test_uds_02_upsert_overwrites_same_day(
    repo: MarketDataRepository, db_session
) -> None:
    """同日重跑覆盖而非报错——回填与生产都会重复写同一天。"""
    for out in (3000, 3212):
        await repo.upsert_universe_daily_stat(
            trade_date=date(2026, 9, 8),
            total_in=5500, total_out=out,
            excluded={"F-1": 5500 - out},
        )
    await db_session.flush()
    rows = (await db_session.execute(text(
        "SELECT total_out FROM universe_daily_stat WHERE trade_date = '2026-09-08'"
    ))).all()
    assert len(rows) == 1, "同日必须只有一行"
    assert rows[0][0] == 3212, "后写的应覆盖先写的"


async def test_uds_03_zero_counts_are_persisted_not_dropped(
    repo: MarketDataRepository, db_session
) -> None:
    """剔除 0 的规则必须**存下来**。

    ⚠️ 这条是本表存在的理由：只有「F-4 生效但没命中」被显式记为 0，
    才能与「F-4 整条静默失效」区分开——而后者恰恰真实发生过
    （2026-09-07 实测 F-4 在约半数交易日实质未生效）。
    """
    await repo.upsert_universe_daily_stat(
        trade_date=date(2026, 9, 9),
        total_in=100, total_out=100,
        excluded={f"F-{i}": 0 for i in range(1, 9)},
    )
    await db_session.flush()
    exc = (await db_session.execute(text(
        "SELECT excluded FROM universe_daily_stat WHERE trade_date = '2026-09-09'"
    ))).scalar_one()
    assert set(exc) == {f"F-{i}" for i in range(1, 9)}
    assert all(v == 0 for v in exc.values())


async def test_uds_04_write_failure_does_not_poison_the_transaction(
    repo: MarketDataRepository, db_session
) -> None:
    """统计写失败后，**调用方的事务必须还能用**。

    ⚠️ 这条来自 2026-09-07 真机实测的一次翻车：`_record_universe_stats` 里
    `except Exception` 确实吞掉了「表不存在」的异常，但 PostgreSQL 已把整个事务置为
    aborted，紧接着的 `get_latest_market_state` 直接炸
    `current transaction is aborted`。

    **「不抛」只保证这一行不抛，没保证调用方还能继续。** 判据必须是
    「失败之后下一条查询仍然成功」，而不是「这一行没抛异常」——后者在缺陷仍在时
    照样为真（§4.11 元判据）。
    """
    from types import SimpleNamespace

    from quantpilot.services.strategy_service import ScoringService

    await db_session.execute(text("DROP TABLE universe_daily_stat"))

    svc = ScoringService.__new__(ScoringService)  # 只测这一个方法，不构造全依赖
    svc._repo = repo
    stats = SimpleNamespace(total_in=100, total_out=90, excluded={"F-1": 10})
    await svc._record_universe_stats(date(2026, 9, 10), stats, 90)

    # 关键断言：事务没被毒化，后续查询照常
    assert (await db_session.execute(text("SELECT 1"))).scalar_one() == 1
