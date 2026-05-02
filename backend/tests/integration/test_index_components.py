"""INC-01~03: 指数成分股集成测试（需要真实 PostgreSQL）"""
from datetime import date

import pytest

from quantpilot.data.repository import MarketDataRepository


@pytest.fixture
def repo(db_session):
    return MarketDataRepository(db_session)


@pytest.mark.asyncio
async def test_inc_01_upsert_index_components_idempotent(repo: MarketDataRepository) -> None:
    """INC-01: upsert_index_components() 重复调用 → ON CONFLICT DO NOTHING，不报错"""
    ts_codes = ["000001.SZ", "000002.SZ", "000858.SZ"]
    count1 = await repo.upsert_index_components("000300.SH", date(2026, 1, 2), ts_codes)
    assert count1 == 3

    # 第二次相同数据 → DO NOTHING，返回 0
    count2 = await repo.upsert_index_components("000300.SH", date(2026, 1, 2), ts_codes)
    assert count2 == 0


@pytest.mark.asyncio
async def test_inc_02_get_index_components_exact_date(repo: MarketDataRepository) -> None:
    """INC-02: get_index_components() 精确日期查询 → 与插入数量一致"""
    ts_codes = ["600519.SH", "601318.SH", "300750.SZ", "000333.SZ"]
    await repo.upsert_index_components("000300.SH", date(2026, 1, 5), ts_codes)

    result = await repo.get_index_components("000300.SH", date(2026, 1, 5))
    assert len(result) == 4
    assert result == sorted(ts_codes)  # 返回升序列表


@pytest.mark.asyncio
async def test_inc_03_get_index_components_fallback(repo: MarketDataRepository) -> None:
    """INC-03: 当日无成分股数据 → 向前回溯最多 30 天返回最近一期"""
    # 先插入 2026-01-02 的成分股
    ts_codes = ["000001.SZ", "600519.SH"]
    await repo.upsert_index_components("000300.SH", date(2026, 1, 2), ts_codes)

    # 查询 2026-01-15（无数据，距 2026-01-02 为 13 天，在 30 天回溯窗口内）
    result = await repo.get_index_components("000300.SH", date(2026, 1, 15))
    assert len(result) == 2
    assert "000001.SZ" in result
    assert "600519.SH" in result
