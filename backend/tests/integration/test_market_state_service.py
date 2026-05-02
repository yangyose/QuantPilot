"""MSTS-01~05: MarketStateService 集成测试（需真实 PostgreSQL）"""
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEngine, MarketStateEnum
from quantpilot.services.market_state_service import MarketStateService

_INDEX_CODE = "000300.SH"
_TRADE_DATE = date(2025, 6, 30)  # 周一，合成数据的最后一个交易日


def _make_trade_dates(n: int, end: date = _TRADE_DATE) -> list[date]:
    """从 end 往前数 n 个交易日（跳过周末，不含节假日调整，但足以反映生产规律）。"""
    dates: list[date] = []
    d = end
    while len(dates) < n:
        if d.weekday() < 5:  # 0=Mon … 4=Fri
            dates.append(d)
        d -= timedelta(days=1)
    return list(reversed(dates))


def _make_index_ohlcv_rows(n: int = 100) -> list[dict]:
    """生成 n 行 000300.SH 合成 OHLCV（仅交易日，平稳上涨，保证触发 UPTREND）。"""
    trade_dates = _make_trade_dates(n)
    rows = []
    for i, td in enumerate(trade_dates):
        close = 3000.0 + i * 20  # 每日+20，线性上涨
        rows.append(
            {
                "index_code": _INDEX_CODE,
                "trade_date": td,
                "open": close * 0.995,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "vol": 100_000_000,
                "pct_chg": 0.67,
            }
        )
    return rows


def _make_service(session: AsyncSession) -> MarketStateService:
    engine = MarketStateEngine()
    repo = MarketDataRepository(session)
    return MarketStateService(engine=engine, repo=repo, index_code=_INDEX_CODE, history_days=100)


async def test_msts_01_identify_and_save_writes_db(db_session: AsyncSession) -> None:
    """MSTS-01: identify_and_save() 调用后 market_state_history 中有对应 trade_date 行"""
    repo = MarketDataRepository(db_session)
    # 插入 100 天合成 OHLCV
    rows = _make_index_ohlcv_rows(100)
    df = pd.DataFrame(rows)
    await repo.upsert_index_history(df)

    svc = _make_service(db_session)
    record = await svc.identify_and_save(_TRADE_DATE)

    assert record is not None, "数据充足时不应返回 None"
    assert record.trade_date == _TRADE_DATE

    # 验证 DB 中确有该行
    saved = await repo.get_latest_market_state()
    assert saved is not None
    assert saved.trade_date == _TRADE_DATE
    assert saved.market_state in ("UPTREND", "DOWNTREND", "OSCILLATION")


async def test_msts_02_get_current_state_returns_latest(db_session: AsyncSession) -> None:
    """MSTS-02: get_current_state() 返回的 trade_date 与最后插入一致"""
    repo = MarketDataRepository(db_session)
    rows = _make_index_ohlcv_rows(100)
    df = pd.DataFrame(rows)
    await repo.upsert_index_history(df)

    svc = _make_service(db_session)
    await svc.identify_and_save(_TRADE_DATE)

    current = await svc.get_current_state()
    assert current is not None
    assert current.trade_date == _TRADE_DATE


async def test_msts_03_state_changed_true_on_switch(db_session: AsyncSession) -> None:
    """MSTS-03: debounce 在 _TRADE_DATE 当天触发 → engine 自然产生 state_changed=True

    设计原理：MA60 暖启动需 59 行，ADX 约 27 行，两者取 max = 59，
    故 62 行总数恰好产生 3 个有效行。prev_confirmed=OSCILLATION（DB 无历史记录），
    3 个有效行均为 UPTREND raw state → apply_debounce 在第 3 有效行（_TRADE_DATE）触发
    切换 → state_changed=True 完全由引擎自然产生，无需外部覆写。
    """
    repo = MarketDataRepository(db_session)
    # 62 行强趋势数据（仅交易日）→ 3 个有效行
    rows = _make_index_ohlcv_rows(62)
    df = pd.DataFrame(rows)
    await repo.upsert_index_history(df)

    svc = _make_service(db_session)
    record = await svc.identify_and_save(_TRADE_DATE)

    assert record is not None
    assert record.market_state == MarketStateEnum.UPTREND
    assert record.state_changed is True

    # 验证 DB 中保存的值与引擎返回值一致
    saved = await repo.get_latest_market_state()
    assert saved is not None
    assert saved.state_changed is True


async def test_msts_04_identify_and_save_idempotent(db_session: AsyncSession) -> None:
    """MSTS-04: 同一 trade_date 调用两次不报错，DB 中仍只有一行"""
    repo = MarketDataRepository(db_session)
    rows = _make_index_ohlcv_rows(100)
    df = pd.DataFrame(rows)
    await repo.upsert_index_history(df)

    svc = _make_service(db_session)
    record1 = await svc.identify_and_save(_TRADE_DATE)
    record2 = await svc.identify_and_save(_TRADE_DATE)

    assert record1 is not None
    assert record2 is not None
    assert record1.trade_date == record2.trade_date

    # DB 中只有一行（latest = _TRADE_DATE）
    history = await repo.get_market_state_history(_TRADE_DATE, _TRADE_DATE)
    assert len(history) == 1


async def test_msts_05_insufficient_data_returns_none(db_session: AsyncSession) -> None:
    """MSTS-05: index_history 只有 50 行时返回 None，DB 无新增行"""
    repo = MarketDataRepository(db_session)
    rows = _make_index_ohlcv_rows(50)
    df = pd.DataFrame(rows)
    await repo.upsert_index_history(df)

    svc = _make_service(db_session)
    result = await svc.identify_and_save(_TRADE_DATE)

    assert result is None

    history = await repo.get_market_state_history(_TRADE_DATE, _TRADE_DATE)
    assert len(history) == 0
