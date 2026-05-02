"""单元测试：BacktestReport 纯函数（INV-BR-01~03）。"""
from __future__ import annotations

import math
from datetime import date

import pytest

from quantpilot.engine.backtest.engine import BacktestConfig
from quantpilot.engine.backtest.report import BacktestReport


def _config(**kwargs) -> BacktestConfig:
    defaults = {
        "start_date": date(2023, 1, 1),
        "end_date": date(2023, 12, 31),
        "initial_capital": 1_000_000.0,
        "strategy_config": {},
        "account_config": {},
    }
    defaults.update(kwargs)
    return BacktestConfig(**defaults)


class TestMaxDrawdown:
    """INV-BR-01/02：max_drawdown 计算。"""

    def test_br_01_known_sequence(self) -> None:
        """INV-BR-01：已知 nav 序列 [1.0, 1.1, 0.99, 1.05] → max_drawdown ≈ 0.1/1.1。"""
        nav = {
            date(2023, 1, 3): 1.0,
            date(2023, 1, 4): 1.1,
            date(2023, 1, 5): 0.99,
            date(2023, 1, 6): 1.05,
        }
        result = BacktestReport.generate(nav, [], _config())
        expected = (1.1 - 0.99) / 1.1
        assert abs(result["max_drawdown"] - expected) < 1e-9

    def test_br_02_monotone_increasing(self) -> None:
        """INV-BR-02：nav 持续上升 → max_drawdown = 0。"""
        nav = {
            date(2023, 1, 3): 1.0,
            date(2023, 1, 4): 1.05,
            date(2023, 1, 5): 1.10,
            date(2023, 1, 6): 1.15,
        }
        result = BacktestReport.generate(nav, [], _config())
        assert result["max_drawdown"] == pytest.approx(0.0, abs=1e-9)


class TestSharpe:
    """INV-BR-03：sharpe_ratio 公式验证。"""

    def test_br_03_known_sharpe(self) -> None:
        """INV-BR-03：已知日收益率序列 → sharpe 可推导验证（rf=0.03）。"""
        import numpy as np

        # 构造一个简单的 nav 序列（均匀上涨）
        dates = [date(2023, 1, d) for d in range(3, 33) if d <= 31]  # 1月3日~31日
        nav_vals = [1.0 + 0.001 * i for i in range(len(dates))]
        nav = dict(zip(dates, nav_vals))

        result = BacktestReport.generate(nav, [], _config())

        # 手动计算 sharpe：日收益率序列
        navs = np.array(nav_vals)
        daily_returns = np.diff(navs) / navs[:-1]
        ann_return = (navs[-1] / navs[0]) ** (252 / (len(navs) - 1)) - 1
        ann_vol = daily_returns.std(ddof=1) * math.sqrt(252)
        expected_sharpe = (ann_return - 0.03) / ann_vol if ann_vol > 0 else 0.0

        assert abs(result["sharpe_ratio"] - expected_sharpe) < 1e-6

    def test_br_03_single_day_no_crash(self) -> None:
        """单日 nav 不崩溃，sharpe 返回 0.0（无法计算波动率）。"""
        nav = {date(2023, 1, 3): 1.0}
        result = BacktestReport.generate(nav, [], _config())
        assert result["sharpe_ratio"] == 0.0


class TestResultKeys:
    """BacktestReport.generate() 返回所有必需字段。"""

    def test_required_fields(self) -> None:
        nav = {date(2023, 1, 3): 1.0, date(2023, 1, 4): 1.02}
        result = BacktestReport.generate(nav, [], _config())
        required = {
            "cumulative_return",
            "annualized_return",
            "max_drawdown",
            "sharpe_ratio",
            "win_rate",
            "profit_loss_ratio",
            "total_trading_days",
        }
        assert required.issubset(result.keys())

    def test_cumulative_return_zero_nav(self) -> None:
        """initial_capital=1000000，最终 nav=1.0（无变化）→ cumulative_return=0.0。"""
        nav = {date(2023, 1, 3): 1.0}
        result = BacktestReport.generate(nav, [], _config(initial_capital=1_000_000.0))
        assert result["cumulative_return"] == pytest.approx(0.0, abs=1e-9)
