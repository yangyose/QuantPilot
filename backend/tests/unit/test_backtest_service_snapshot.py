"""Phase 10 §4.4：BacktestService.create_task 接收并写入 config_snapshot 验证。"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from quantpilot.engine.backtest.engine import BacktestConfig
from quantpilot.services.backtest_service import BacktestService


def _make_config() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 12, 31),
        initial_capital=1_000_000.0,
        strategy_config={},
        account_config={},
        commission_rate=0.0005,
        stamp_tax_rate=0.001,
        slippage_rate=0.002,
    )


async def test_create_task_writes_config_snapshot_when_provided() -> None:
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    engine = MagicMock()
    svc = BacktestService(session, engine)

    snapshot = {
        "signal_params": {"buy_threshold": 80.0},
        "_snapshot_at": "2026-04-24T10:00:00Z",
    }

    task_id = await svc.create_task(_make_config(), engine_snapshot=snapshot)

    assert task_id  # uuid 字符串
    session.add.assert_called_once()
    added_task = session.add.call_args.args[0]
    assert added_task.task_id == task_id
    assert added_task.status == "PENDING"
    assert added_task.config_snapshot == snapshot


async def test_create_task_allows_snapshot_none_backward_compat() -> None:
    """未提供 engine_snapshot 时向后兼容：config_snapshot 保持 None。"""
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    engine = MagicMock()
    svc = BacktestService(session, engine)

    await svc.create_task(_make_config())  # 不传 engine_snapshot

    added_task = session.add.call_args.args[0]
    assert added_task.config_snapshot is None
