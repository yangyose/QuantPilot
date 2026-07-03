"""unit/test_signal_service_private.py: SignalService.evaluate_private_signals（V1.5-G G-4d-3）。

G-4d-1 把持仓私有 SELL 移出每日管线；G-4d-3（用户 2026-07-03 拍板 A=走每日 Job 通知）
让每日 Job 按账户重跑 SignalGenerator 评估持仓私有 SELL，经通知推送。为保持止损逻辑
**单一实现源**（不另写一份 hard_stop_loss），本方法复用 generate_for_date 的输入加载 +
SignalGenerator，只过滤出持仓派生的私有信号——私有 SELL（hard_stop_loss /
short_term_z_drop / mid_term_icir_flip）+ **加仓 BUY**（持仓 + 达买入条件 + 满足
SDD §10.1 加仓规则；用户 2026-07-03 拍板与私有 SELL 同路走每日 Job 通知），
**不落库**。共享 pct_above_sell（管线已产）被排除。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd

from quantpilot.core.config_defaults import DEFAULT_SIGNAL_CONFIG, DEFAULT_UNIVERSE
from quantpilot.services.signal_service import SignalService

TRADE_DATE = date(2026, 4, 8)


def _pool_entry(ts_code: str, composite_score: float, *, market_state: str = "OSCILLATION"):
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


def _snapshot_df(ts_codes: list[str], *, close: float = 10.0):
    return pd.DataFrame(
        {
            "close": [close] * len(ts_codes),
            "is_suspended": [False] * len(ts_codes),
            "limit_up": [False] * len(ts_codes),
            "avg_amount": [10_000_000.0] * len(ts_codes),
            "sw_industry_l1": ["电子"] * len(ts_codes),
        },
        index=pd.Index(ts_codes, name="ts_code"),
    )


def _position(ts_code: str, pnl_pct: float, cost_price: float = 11.2):
    return SimpleNamespace(
        ts_code=ts_code, pnl_pct=pnl_pct, cost_price=cost_price,
        market_value=50_000.0,
    )


def _make_repo(
    pool, snapshot, market_state: str = "OSCILLATION", *, pool_date: date | None = TRADE_DATE
) -> MagicMock:
    repo = MagicMock()
    # G-4d-4 时序修正：evaluate_private_signals 先解析 ≤ trade_date 的最新池日期
    # （15:05 Job 跑在 17:30 管线之前，当日池尚不存在）
    repo.get_latest_pool_date = AsyncMock(return_value=pool_date)
    repo.get_pool = AsyncMock(return_value=pool)
    repo.get_snapshot_quotes = AsyncMock(return_value=snapshot)
    repo.get_latest_market_state = AsyncMock(
        return_value=SimpleNamespace(market_state=market_state, trade_date=TRADE_DATE)
    )
    # _compute_holding_signal_states 走的两个源：无历史快照 / IC → 返回空（自然降级）
    repo.get_recent_score_snapshots_for_holdings = AsyncMock(return_value=[])
    repo.session = MagicMock()
    return repo


def _make_config_service() -> AsyncMock:
    svc = AsyncMock()
    svc.get_signal_params = AsyncMock(return_value=DEFAULT_SIGNAL_CONFIG)
    svc.get_universe_params = AsyncMock(return_value=DEFAULT_UNIVERSE)
    return svc


async def test_private_signals_empty_positions_returns_empty() -> None:
    """无持仓 → []（不查池，无 IO）。"""
    repo = _make_repo([], _snapshot_df([]))
    svc = SignalService(repo, config_service=_make_config_service())
    result = await svc.evaluate_private_signals(TRADE_DATE, [])
    assert result == []
    repo.get_pool.assert_not_awaited()


async def test_private_signals_hard_stop_loss() -> None:
    """持仓浮亏 -10% 在中性区（不触发 pct_above_sell）→ 返回 hard_stop_loss 私有 SELL。"""
    # composite_score=55 → 中性（不 > buy 80、不 < sell 阈值）
    pool = [_pool_entry("000001.SZ", 55.0)]
    snapshot = _snapshot_df(["000001.SZ"])
    repo = _make_repo(pool, snapshot)
    svc = SignalService(repo, config_service=_make_config_service())

    positions = [_position("000001.SZ", pnl_pct=-0.10)]  # 浮亏 10% > 8% 阈值
    result = await svc.evaluate_private_signals(TRADE_DATE, positions)

    assert len(result) == 1
    assert result[0].signal_type == "SELL"
    assert result[0].trigger_reason == "hard_stop_loss"
    assert result[0].ts_code == "000001.SZ"


async def test_private_signals_healthy_holding_no_signal() -> None:
    """持仓浮盈 + 中性评分 → 无私有 SELL（返回 []）。"""
    pool = [_pool_entry("000002.SZ", 55.0)]
    snapshot = _snapshot_df(["000002.SZ"])
    repo = _make_repo(pool, snapshot)
    svc = SignalService(repo, config_service=_make_config_service())

    positions = [_position("000002.SZ", pnl_pct=0.05)]
    result = await svc.evaluate_private_signals(TRADE_DATE, positions)
    assert result == []


async def test_private_signals_excludes_shared_pct_above_sell() -> None:
    """持仓 + 高分位（pct_above_sell）是共享 SELL（管线已产）→ 不计入私有集合。"""
    # composite_score 低 → 旧路径下 score < sell_threshold 会触发 pct_above_sell（共享语义）
    pool = [_pool_entry("000003.SZ", 5.0)]
    snapshot = _snapshot_df(["000003.SZ"])
    repo = _make_repo(pool, snapshot)
    svc = SignalService(repo, config_service=_make_config_service())

    # 浮盈，避免 hard_stop_loss；低分 → pct_above_sell（共享）
    positions = [_position("000003.SZ", pnl_pct=0.05)]
    result = await svc.evaluate_private_signals(TRADE_DATE, positions)
    # 共享 pct_above_sell 被过滤，私有集合为空
    assert all(s.trigger_reason != "pct_above_sell" for s in result)
    assert result == []


async def test_private_signals_no_pool_returns_empty() -> None:
    """候选池为空 → []（持仓无对应评分上下文）。"""
    repo = _make_repo([], _snapshot_df([]))
    svc = SignalService(repo, config_service=_make_config_service())
    positions = [_position("000004.SZ", pnl_pct=-0.10)]
    result = await svc.evaluate_private_signals(TRADE_DATE, positions)
    assert result == []


async def test_private_signals_add_position_buy() -> None:
    """持仓 + 评分 85 > 80 + 浮盈（can_add）→ 加仓 BUY 计入私有信号（G-4d-4 拍板）。"""
    pool = [_pool_entry("000005.SZ", 85.0)]
    snapshot = _snapshot_df(["000005.SZ"])
    repo = _make_repo(pool, snapshot)
    svc = SignalService(repo, config_service=_make_config_service())

    # 浮盈 5% → SDD §10.1 加仓条件 pnl_pct > 0 满足
    positions = [_position("000005.SZ", pnl_pct=0.05, cost_price=9.5)]
    result = await svc.evaluate_private_signals(TRADE_DATE, positions)

    assert len(result) == 1
    assert result[0].signal_type == "BUY"
    assert result[0].ts_code == "000005.SZ"


async def test_private_signals_resolves_latest_pool_date() -> None:
    """G-4d-4 时序修正：15:05 Job 早于 17:30 管线 → 当日池不存在，须回落 ≤ trade_date
    的最新池日期评估（否则私有信号每天静默为空）。"""
    from datetime import timedelta

    pool = [_pool_entry("000007.SZ", 55.0)]
    snapshot = _snapshot_df(["000007.SZ"])
    prev_date = TRADE_DATE - timedelta(days=1)
    repo = _make_repo(pool, snapshot, pool_date=prev_date)
    svc = SignalService(repo, config_service=_make_config_service())

    positions = [_position("000007.SZ", pnl_pct=-0.10)]
    result = await svc.evaluate_private_signals(TRADE_DATE, positions)

    # 以解析出的池日期加载输入
    repo.get_latest_pool_date.assert_awaited_once_with(TRADE_DATE)
    repo.get_pool.assert_awaited_once_with(trade_date=prev_date)
    assert len(result) == 1
    assert result[0].trigger_reason == "hard_stop_loss"


async def test_private_signals_no_pool_date_returns_empty() -> None:
    """库中从无候选池（get_latest_pool_date → None）→ []。"""
    repo = _make_repo([], _snapshot_df([]), pool_date=None)
    svc = SignalService(repo, config_service=_make_config_service())
    positions = [_position("000008.SZ", pnl_pct=-0.10)]
    result = await svc.evaluate_private_signals(TRADE_DATE, positions)
    assert result == []
    repo.get_pool.assert_not_awaited()


async def test_private_signals_add_position_blocked_when_cannot_add() -> None:
    """持仓 + 评分 85 但浮亏且成本偏离 > 10% → can_add False → 无加仓 BUY。"""
    pool = [_pool_entry("000006.SZ", 85.0)]
    snapshot = _snapshot_df(["000006.SZ"])  # close=10.0
    repo = _make_repo(pool, snapshot)
    svc = SignalService(repo, config_service=_make_config_service())

    # 浮亏 5%（> -8% 不触发硬止损）；cost=11.5 → 偏离 13% > add_cost_deviation 10%
    positions = [_position("000006.SZ", pnl_pct=-0.05, cost_price=11.5)]
    result = await svc.evaluate_private_signals(TRADE_DATE, positions)
    assert result == []
