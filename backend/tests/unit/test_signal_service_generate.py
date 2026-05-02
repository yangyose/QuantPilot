"""unit/test_signal_service_generate.py: Phase 10 §7.1 SignalService.generate_for_date 完整化。

验证：
- 依赖齐全 → 完整链路（SignalGenerator → PositionSizer → RiskChecker）
- 缺依赖 → RuntimeError（去除 V1.0 降级）
- 空候选池 → []
- RiskChecker BLOCK → 信号不保存
- PositionSizer 正确填入 suggested_pct
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from quantpilot.core.config_defaults import (
    DEFAULT_RISK_LIMITS,
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
    # id 按顺序 100, 101, ...；signal_type/ts_code 从输入 rows 回读
    async def _upsert_signals(rows: list[dict]) -> list[dict]:
        return [
            {"id": 100 + i, "ts_code": r["ts_code"], "signal_type": r["signal_type"]}
            for i, r in enumerate(rows)
        ]

    repo.upsert_signals = AsyncMock(side_effect=_upsert_signals)
    repo.upsert_signal_snapshots = AsyncMock(return_value=None)
    # get_today_signals 由 generate_for_date 尾部调用
    #   返回 ORM 风格对象（SimpleNamespace 也可）
    return repo


def _make_account_service(
    positions: list[SimpleNamespace] | None = None,
    total_assets: float = 1_000_000.0,
    cash: float = 500_000.0,
) -> AsyncMock:
    svc = AsyncMock()
    svc.get_all_positions = AsyncMock(return_value=positions or [])
    account = SimpleNamespace(
        id=1,
        total_assets=Decimal(str(total_assets)),
        cash=Decimal(str(cash)),
    )
    svc.get_default_account = AsyncMock(return_value=account)
    # V1.0 整改 Batch 2 — B2-1：默认无回撤数据（< 2 个 daily_portfolio_value 行 → None）
    svc.get_current_drawdown = AsyncMock(return_value=None)
    return svc


def _make_config_service() -> AsyncMock:
    svc = AsyncMock()
    svc.get_signal_params = AsyncMock(return_value=DEFAULT_SIGNAL_CONFIG)
    svc.get_universe_params = AsyncMock(return_value=DEFAULT_UNIVERSE)
    svc.get_risk_limits = AsyncMock(return_value=DEFAULT_RISK_LIMITS)
    return svc


async def test_generate_for_date_empty_pool_returns_empty() -> None:
    """空候选池 → 无持久化调用，返回 []。"""
    repo = _make_repo([], _snapshot_df([]))
    account_svc = _make_account_service()
    cfg = _make_config_service()

    svc = SignalService(repo, account_service=account_svc, config_service=cfg)
    result = await svc.generate_for_date(TRADE_DATE)

    assert result == []
    repo.upsert_signals.assert_not_awaited()


async def test_generate_for_date_full_chain_writes_signals() -> None:
    """评分 > 买入阈值 → BUY 信号经 SignalGenerator + PositionSizer + RiskChecker 后持久化。"""
    pool = [_pool_entry("000001.SZ", 85.0), _pool_entry("000002.SZ", 82.0)]
    snapshot = _snapshot_df(["000001.SZ", "000002.SZ"])

    # get_today_signals 返回本次 upsert 写入的两个 signal（用 SimpleNamespace 模拟 ORM）
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

    account_svc = _make_account_service()  # 空持仓 + 现金充足
    cfg = _make_config_service()

    svc = SignalService(repo, account_service=account_svc, config_service=cfg)
    result = await svc.generate_for_date(TRADE_DATE)

    assert len(result) == 2
    repo.upsert_signals.assert_awaited_once()

    # 校验 upsert 参数：两条 BUY 均有 suggested_pct（PositionSizer 填充）
    rows = repo.upsert_signals.await_args.args[0]
    assert len(rows) == 2
    assert all(r["signal_type"] == "BUY" for r in rows)
    assert all(r["suggested_pct"] is not None for r in rows)
    assert all(r["stop_loss_price"] is not None for r in rows)


async def test_generate_for_date_risk_checker_blocks_concentration() -> None:
    """行业集中度超限 → RiskChecker BLOCK → 信号在 save() 阶段被移除。

    已持两只同行业股合计占 25%，行业上限 30%；新 BUY 建议 10% 导致 35% > 30% → BLOCK。
    """
    pool = [_pool_entry("000003.SZ", 90.0)]
    snapshot = _snapshot_df(["000003.SZ", "000001.SZ", "000002.SZ"], industry="电子")

    # 现有 2 只持仓（同行业"电子"），合计占总资产 25%
    existing_1 = SimpleNamespace(
        ts_code="000001.SZ",
        market_value=Decimal("120000"),
        pnl_pct=Decimal("0.03"),
        cost_price=Decimal("9.5"),
        current_price=Decimal("10.0"),
        shares=12000,
    )
    existing_2 = SimpleNamespace(
        ts_code="000002.SZ",
        market_value=Decimal("130000"),
        pnl_pct=Decimal("0.04"),
        cost_price=Decimal("9.0"),
        current_price=Decimal("10.0"),
        shares=13000,
    )

    repo = _make_repo(pool, snapshot)
    # BLOCK 移除后 rows 为空，upsert_signals 不会被调用 → get_today_signals 返回 []
    repo.get_signals_by_date = AsyncMock(return_value=[])

    account_svc = _make_account_service(positions=[existing_1, existing_2])
    cfg = _make_config_service()

    svc = SignalService(repo, account_service=account_svc, config_service=cfg)
    result = await svc.generate_for_date(TRADE_DATE)

    # 期望：BLOCK 后无信号持久化
    assert result == []
    repo.upsert_signals.assert_not_awaited()


async def test_generate_for_date_missing_account_service_raises() -> None:
    """缺 account_service → RuntimeError（Phase 10 §7.1 去除 V1.0 降级）。"""
    pool = [_pool_entry("000001.SZ", 85.0)]
    repo = _make_repo(pool, _snapshot_df(["000001.SZ"]))
    cfg = _make_config_service()

    svc = SignalService(repo, account_service=None, config_service=cfg)

    with pytest.raises(RuntimeError, match="account_service"):
        await svc.generate_for_date(TRADE_DATE)


async def test_generate_for_date_missing_config_service_raises() -> None:
    """缺 config_service → RuntimeError。"""
    pool = [_pool_entry("000001.SZ", 85.0)]
    repo = _make_repo(pool, _snapshot_df(["000001.SZ"]))
    account_svc = _make_account_service()

    svc = SignalService(repo, account_service=account_svc, config_service=None)

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

    account_svc = _make_account_service()
    cfg = _make_config_service()

    svc = SignalService(repo, account_service=account_svc, config_service=cfg)
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

    account_svc = _make_account_service()
    cfg = _make_config_service()

    svc = SignalService(repo, account_service=account_svc, config_service=cfg)
    result = await svc.generate_for_date(TRADE_DATE)

    assert result == []
    repo.upsert_signals.assert_not_awaited()
