"""INT-PPP-01~05：PE/PB 历史分位 SQL 下推与 Python 实现的**等价性**。

## 为什么下推

`get_pe_pb_history_bulk` 为算约 3212 个分位数，要从 `financial_data` 拉约
**380 万行**（5 年窗口 × universe），且 `result.all()` 会先把这些行全部
实例化成 SQLAlchemy Row（内含 Decimal 对象）再转 DataFrame——这是每日管线
内存峰值的主项，2026-09-03 生产 OOM 的直接推手（见 `docs/ops/deploy_log.md`）。

下推后返回约 3212 行，降三个数量级。

## 判据：等价，而不是「跑通」

value 策略占 composite 权重 0.57~0.87，分位算错会**静默改变选股**且不报错。
所以本文件的核心不是「SQL 能跑」，而是**逐股对照现有 Python 实现
`_compute_historical_percentile`，要求完全相等**——包括它那些容易被 SQL
写漏的边角：

| 语义 | SQL 写错成什么样也不会报错 |
|---|---|
| 严格 `<` | 写 `<=` → 等值样本分位整体偏移 |
| 分母 = 非 NULL 条数 | `count(*)` 含 NULL → 分位系统性偏低 |
| `1 - pct_rank` | 方向反转 → 低估变高估 |
| 逐股独立 | 漏 `GROUP BY ts_code` → 全市场混算，仍得 0~1 的合理数值 |
| 无历史 → NaN | 返回 0.0 → 无数据的股票变「最贵」或「最便宜」 |

`tests/unit/test_value_percentile_semantics.py` 已把 Python 侧这些语义钉死；
本文件负责保证 SQL 侧与之**逐股一致**，两者合起来才构成完整验收。

⚠️ 本文件只读不写业务表，seed 行在 finally 清干净（§4.11 副作用泄漏）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.strategies.value import _compute_historical_percentile
from quantpilot.models.market import FinancialData

_START = date(2026, 1, 1)
_END = date(2026, 6, 30)
_CODES = ["PPP001.SZ", "PPP002.SZ", "PPP003.SZ"]


async def _seed_history(
    session: AsyncSession, series: dict[str, list[float | None]]
) -> None:
    """按 ts_code 写入若干 publish_date 递增的 pe_ttm/pb 行。

    pb 故意取 pe 的一半：若实现把列名写死成 pe_ttm，pb 的用例会露馅。
    """
    for ts_code, values in series.items():
        for i, v in enumerate(values):
            session.add(FinancialData(
                ts_code=ts_code,
                report_period=date(2026, 3, 31),
                publish_date=_START + timedelta(days=i),
                pe_ttm=v,
                pb=None if v is None else v / 2.0,
            ))
    await session.flush()


async def _cleanup(session: AsyncSession) -> None:
    for row in (
        await session.execute(
            select(FinancialData).where(FinancialData.ts_code.in_(_CODES))
        )
    ).scalars().all():
        await session.delete(row)
    await session.flush()


def _python_reference(
    hist: dict[str, list[float | None]],
    current: dict[str, float],
    col: str,
) -> pd.Series:
    """用现有 Python 实现算同一批分位，作为对照基准。"""
    universe = pd.Index(list(current.keys()), name="ts_code")
    tuples: list[tuple[str, date]] = []
    vals: list[float | None] = []
    for ts_code, values in hist.items():
        for i, v in enumerate(values):
            tuples.append((ts_code, _START + timedelta(days=i)))
            vals.append(v if v is None else (v if col == "pe_ttm" else v / 2.0))
    mi = pd.MultiIndex.from_tuples(tuples, names=["ts_code", "trade_date"])
    df = pd.DataFrame({col: vals}, index=mi)
    return _compute_historical_percentile(
        universe, pd.Series(current), df, col, inverse=True
    )


# ============================================================
# INT-PPP-01：与 Python 实现逐股相等（含等值、NULL、边界）
# ============================================================
@pytest.mark.parametrize("col", ["pe_ttm", "pb"])
async def test_int_ppp_01_matches_python_implementation(
    db_session: AsyncSession, col: str
) -> None:
    """同一批数据上，SQL 与 Python 必须给出**完全相同**的分位。

    三只股票各自覆盖一类边角：
    - PPP001：当前值**等于**某个历史值 → 检验严格 `<`
    - PPP002：历史含 NULL → 检验分母是非 NULL 条数
    - PPP003：当前值低于全部历史 → 检验方向（应得 1.0 满分）
    """
    hist = {
        "PPP001.SZ": [5.0, 10.0, 15.0],
        "PPP002.SZ": [5.0, None, 15.0, None],
        "PPP003.SZ": [10.0, 20.0, 30.0],
    }
    # pb 列的值是 pe 的一半，当前值也要跟着减半，才是同一个相对位置
    scale = 1.0 if col == "pe_ttm" else 0.5
    current = {
        "PPP001.SZ": 10.0 * scale,
        "PPP002.SZ": 10.0 * scale,
        "PPP003.SZ": 1.0 * scale,
    }
    repo = MarketDataRepository(db_session)
    try:
        await _seed_history(db_session, hist)
        got = await repo.get_pe_pb_percentile_bulk(current, _START, _END, col)
        expected = _python_reference(hist, current, col)

        assert sorted(got.index) == sorted(expected.index)
        for ts_code in expected.index:
            assert got[ts_code] == pytest.approx(expected[ts_code]), (
                f"{col} / {ts_code}：SQL={got[ts_code]} vs Python={expected[ts_code]}"
            )
        # 顺带把解析值也钉一遍，避免"两边一起错"
        assert got["PPP001.SZ"] == pytest.approx(2.0 / 3.0)   # 严格小于的只有 5
        assert got["PPP002.SZ"] == pytest.approx(0.5)         # dropna 后 [5,15]
        assert got["PPP003.SZ"] == pytest.approx(1.0)         # 低于全部历史
    finally:
        await _cleanup(db_session)


# ============================================================
# INT-PPP-02：无历史 → NaN，而不是 0
# ============================================================
async def test_int_ppp_02_missing_history_is_nan_not_zero(
    db_session: AsyncSession,
) -> None:
    """0 意味着「算出来就是最贵」，NaN 意味着「算不出来」，两者不能混。

    混了的后果：一只没有历史数据的股票会被当成估值极端值参与横截面排序。
    """
    repo = MarketDataRepository(db_session)
    try:
        await _seed_history(db_session, {"PPP001.SZ": [5.0, 10.0, 15.0]})
        got = await repo.get_pe_pb_percentile_bulk(
            {"PPP001.SZ": 10.0, "PPP002.SZ": 8.0}, _START, _END, "pe_ttm"
        )
        assert got["PPP001.SZ"] == pytest.approx(2.0 / 3.0)
        assert pd.isna(got["PPP002.SZ"]), "无历史必须是 NaN"
    finally:
        await _cleanup(db_session)


# ============================================================
# INT-PPP-03：窗口边界（publish_date 落在 [start, end] 之外的行不参与）
# ============================================================
async def test_int_ppp_03_respects_window_bounds(db_session: AsyncSession) -> None:
    """窗口外的历史不得参与——否则 5 年窗口的语义就没了。"""
    repo = MarketDataRepository(db_session)
    try:
        await _seed_history(db_session, {"PPP001.SZ": [5.0, 10.0, 15.0]})
        # 只取前 2 天 → 历史变成 [5, 10]，当前 10 → 严格小于 1 个 → 1/2 → 0.5
        narrow_end = _START + timedelta(days=1)
        got = await repo.get_pe_pb_percentile_bulk(
            {"PPP001.SZ": 10.0}, _START, narrow_end, "pe_ttm"
        )
        assert got["PPP001.SZ"] == pytest.approx(0.5)
        # 全窗口下同一当前值应是 2/3——两者不同才证明边界真的生效
        wide = await repo.get_pe_pb_percentile_bulk(
            {"PPP001.SZ": 10.0}, _START, _END, "pe_ttm"
        )
        assert wide["PPP001.SZ"] == pytest.approx(2.0 / 3.0)
    finally:
        await _cleanup(db_session)


# ============================================================
# INT-PPP-04：逐股独立，不跨股票混算
# ============================================================
async def test_int_ppp_04_per_stock_isolation(db_session: AsyncSession) -> None:
    """漏掉 GROUP BY ts_code 时结果仍是 0~1 的合理数值，只能靠数值分辨。"""
    repo = MarketDataRepository(db_session)
    try:
        await _seed_history(db_session, {
            "PPP001.SZ": [1.0, 2.0, 3.0],        # 低位
            "PPP002.SZ": [100.0, 200.0, 300.0],  # 高位
        })
        got = await repo.get_pe_pb_percentile_bulk(
            {"PPP001.SZ": 2.5, "PPP002.SZ": 250.0}, _START, _END, "pe_ttm"
        )
        # 各自跟自己比：都是「严格小于 2 个 / 共 3 个」→ 1/3
        assert got["PPP001.SZ"] == pytest.approx(1.0 / 3.0)
        assert got["PPP002.SZ"] == pytest.approx(1.0 / 3.0)
        # 若混算，PPP001 的 2.5 在 6 个值里只小于 1.0 → 1/6 → inverse 5/6
        assert got["PPP001.SZ"] != pytest.approx(5.0 / 6.0)
    finally:
        await _cleanup(db_session)


# ============================================================
# INT-PPP-05：空入参与非法列名
# ============================================================
async def test_int_ppp_05_empty_input_and_bad_column(
    db_session: AsyncSession,
) -> None:
    repo = MarketDataRepository(db_session)
    empty = await repo.get_pe_pb_percentile_bulk({}, _START, _END, "pe_ttm")
    assert list(empty.index) == []

    # 列名会被拼进 SQL（不能走 bind 参数）→ 必须白名单校验，否则是注入面
    with pytest.raises(ValueError):
        await repo.get_pe_pb_percentile_bulk(
            {"PPP001.SZ": 1.0}, _START, _END, "pe_ttm; DROP TABLE financial_data--"
        )
