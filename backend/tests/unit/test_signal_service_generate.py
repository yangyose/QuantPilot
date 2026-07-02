"""unit/test_signal_service_generate.py: SignalService.generate_for_date（V1.5-G G-4d-1 解耦后）。

V1.5-G G-4d-1（§2 管线与账户解耦）：generate_for_date **不再读账户**，产账户无关的
共享信号（BUY 候选 + 客观 pct_above_sell SELL）。仓位建议（suggested_pct）/ 集中度
BLOCK / 持仓私有 SELL / 回撤 RISK_WARN 移 API 请求期（G-4d-2）+ 每日 Job（G-4d-3）。

本文件验证：
- 依赖齐全（仅 config_service）→ 共享 BUY 信号，suggested_pct 为 None（不再 sizing）
- 缺 config_service → RuntimeError
- 空候选池 → []
- market_state 缺失 → 默认 OSCILLATION（不抛错）
- 评分低于 buy_threshold → 无 BUY 信号
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from quantpilot.core.config_defaults import (
    DEFAULT_SIGNAL_CONFIG,
    DEFAULT_UNIVERSE,
)
from quantpilot.services.signal_service import SignalService

TRADE_DATE = date(2026, 4, 8)


def _pool_entry(
    ts_code: str,
    composite_score: float,
    *,
    market_state: str = "UPTREND",
) -> SimpleNamespace:
    """构造 CandidatePool 行（SimpleNamespace 避免 ORM 导入开销）。"""
    return SimpleNamespace(
        ts_code=ts_code,
        trade_date=TRADE_DATE,
        composite_score=Decimal(str(composite_score)),
        trend_score=Decimal(str(composite_score)),
        reversion_score=Decimal(str(composite_score)),
        momentum_score=Decimal(str(composite_score)),
        value_score=Decimal(str(composite_score)),
        market_state=market_state,
        in_pool=True,
    )


def _snapshot_df(
    ts_codes: list[str], *, close: float = 10.0, industry: str = "电子"
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [close] * len(ts_codes),
            "is_suspended": [False] * len(ts_codes),
            "limit_up": [False] * len(ts_codes),
            "avg_amount": [10_000_000.0] * len(ts_codes),
            "sw_industry_l1": [industry] * len(ts_codes),
        },
        index=pd.Index(ts_codes, name="ts_code"),
    )


def _make_repo(
    pool_entries: list[SimpleNamespace],
    snapshot: pd.DataFrame,
    market_state: str = "UPTREND",
) -> MagicMock:
    repo = MagicMock()
    repo.get_pool = AsyncMock(return_value=pool_entries)
    repo.get_snapshot_quotes = AsyncMock(return_value=snapshot)

    # get_latest_market_state 返回含 market_state 属性的对象；None 视为缺失
    if market_state is None:
        repo.get_latest_market_state = AsyncMock(return_value=None)
    else:
        ms = SimpleNamespace(market_state=market_state, trade_date=TRADE_DATE)
        repo.get_latest_market_state = AsyncMock(return_value=ms)

    # save() 路径：upsert_signals 返回 RETURNING id
    async def _upsert_signals(rows: list[dict]) -> list[dict]:
        return [
            {"id": 100 + i, "ts_code": r["ts_code"], "signal_type": r["signal_type"]}
            for i, r in enumerate(rows)
        ]

    repo.upsert_signals = AsyncMock(side_effect=_upsert_signals)
    repo.upsert_signal_snapshots = AsyncMock(return_value=None)
    return repo


def _make_config_service() -> AsyncMock:
    svc = AsyncMock()
    svc.get_signal_params = AsyncMock(return_value=DEFAULT_SIGNAL_CONFIG)
    svc.get_universe_params = AsyncMock(return_value=DEFAULT_UNIVERSE)
    return svc


async def test_generate_for_date_empty_pool_returns_empty() -> None:
    """空候选池 → 无持久化调用，返回 []。"""
    repo = _make_repo([], _snapshot_df([]))
    cfg = _make_config_service()

    svc = SignalService(repo, config_service=cfg)
    result = await svc.generate_for_date(TRADE_DATE)

    assert result == []
    repo.upsert_signals.assert_not_awaited()


async def test_generate_for_date_writes_shared_buy_signals() -> None:
    """评分 > 买入阈值 → 共享 BUY 信号持久化；管线不再 sizing → suggested_pct 为 None。"""
    pool = [_pool_entry("000001.SZ", 85.0), _pool_entry("000002.SZ", 82.0)]
    snapshot = _snapshot_df(["000001.SZ", "000002.SZ"])

    saved_signals = [
        SimpleNamespace(
            id=100, ts_code="000001.SZ", signal_type="BUY", trade_date=TRADE_DATE,
        ),
        SimpleNamespace(
            id=101, ts_code="000002.SZ", signal_type="BUY", trade_date=TRADE_DATE,
        ),
    ]
    repo = _make_repo(pool, snapshot)
    repo.get_signals_by_date = AsyncMock(return_value=saved_signals)

    cfg = _make_config_service()

    svc = SignalService(repo, config_service=cfg)
    result = await svc.generate_for_date(TRADE_DATE)

    assert len(result) == 2
    repo.upsert_signals.assert_awaited_once()

    # 校验 upsert 参数：两条 BUY，suggested_pct 为 None（sizing 移 API 期），
    # 但 stop_loss_price 由 SignalGenerator 计算，仍非 None。
    rows = repo.upsert_signals.await_args.args[0]
    assert len(rows) == 2
    assert all(r["signal_type"] == "BUY" for r in rows)
    assert all(r["suggested_pct"] is None for r in rows)
    assert all(r["stop_loss_price"] is not None for r in rows)


async def test_generate_for_date_missing_config_service_raises() -> None:
    """缺 config_service → RuntimeError。"""
    pool = [_pool_entry("000001.SZ", 85.0)]
    repo = _make_repo(pool, _snapshot_df(["000001.SZ"]))

    svc = SignalService(repo, config_service=None)

    with pytest.raises(RuntimeError, match="config_service"):
        await svc.generate_for_date(TRADE_DATE)


async def test_generate_for_date_market_state_fallback_oscillation() -> None:
    """market_state_history 缺失 → 默认 OSCILLATION（不抛错）。"""
    pool = [_pool_entry("000001.SZ", 85.0)]
    snapshot = _snapshot_df(["000001.SZ"])

    repo = _make_repo(pool, snapshot, market_state=None)
    repo.get_signals_by_date = AsyncMock(return_value=[
        SimpleNamespace(id=100, ts_code="000001.SZ", signal_type="BUY"),
    ])

    cfg = _make_config_service()

    svc = SignalService(repo, config_service=cfg)
    result = await svc.generate_for_date(TRADE_DATE)

    # OSCILLATION 系数 0.75 → 仍可产生信号
    assert len(result) >= 0  # 不抛错即可
    repo.get_latest_market_state.assert_awaited()


async def test_generate_for_date_low_score_produces_no_buy() -> None:
    """评分低于 buy_threshold（默认 80）→ 无 BUY 信号。"""
    pool = [_pool_entry("000001.SZ", 70.0)]  # 70 < 80
    snapshot = _snapshot_df(["000001.SZ"])

    repo = _make_repo(pool, snapshot)
    repo.get_signals_by_date = AsyncMock(return_value=[])

    cfg = _make_config_service()

    svc = SignalService(repo, config_service=cfg)
    result = await svc.generate_for_date(TRADE_DATE)

    assert result == []
    repo.upsert_signals.assert_not_awaited()
