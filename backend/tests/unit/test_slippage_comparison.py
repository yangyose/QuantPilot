"""V1.5-A A1b（SDD §16 滑点敏感性）：BacktestService.run_slippage_comparison 单测。

多滑点情景对比：复用同一 bundle 串行跑各档，产出结构化对比报告；engine.run 每档
用覆盖后的 slippage_rate；bundle 只加载一次（内存不 N 倍）。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from quantpilot.engine.backtest.engine import BacktestConfig
from quantpilot.services.backtest_service import BacktestService


def _cfg(scenarios: list[float] | None = None) -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2024, 6, 3), end_date=date(2024, 6, 10),
        initial_capital=1_000_000.0, strategy_config={}, account_config={},
        slippage_scenarios=scenarios,
    )


class _StubEngine:
    """记录每次 run 的 slippage_rate 与 data 身份。"""

    def __init__(self) -> None:
        self.calls: list[tuple[float, int]] = []

    def run(self, config, data, progress_cb=None, position_sink=None):
        self.calls.append((config.slippage_rate, id(data)))
        # 用 slippage 派生一个确定性 performance，验证对比条目对齐情景
        return SimpleNamespace(
            performance={
                "total_return": 1.0 - config.slippage_rate * 10,
                "max_drawdown": -0.1,
                "sharpe_ratio": 2.0 - config.slippage_rate * 100,
                "annualized_return": 0.2,
            },
            pipeline_mode="real_5step",
        )


def test_a1b_slippage_comparison_reuses_bundle_and_overrides_slippage() -> None:
    """3 档滑点：engine.run 调 3 次、每次 slippage_rate 为对应档、data 复用同一对象；
    对比报告 3 条含 slippage + 关键绩效字段。"""
    engine = _StubEngine()
    svc = BacktestService(session=None, engine=engine)
    scenarios = [0.001, 0.003, 0.005]
    bundle = object()  # 身份哨兵

    report = svc.run_slippage_comparison(_cfg(scenarios), bundle, scenarios=scenarios)

    # 3 次 run，slippage 依次覆盖
    assert [c[0] for c in engine.calls] == scenarios
    # 同一 bundle 复用（id 全等）
    assert len({c[1] for c in engine.calls}) == 1
    assert engine.calls[0][1] == id(bundle)
    # 对比报告结构
    assert len(report) == 3
    assert [r["slippage"] for r in report] == scenarios
    assert report[0]["total_return"] > report[-1]["total_return"]  # 滑点越大回报越低
    for r in report:
        assert {"slippage", "total_return", "max_drawdown", "sharpe", "pipeline_mode"} <= set(r)


def test_a1b_slippage_comparison_defaults_to_config_scenarios() -> None:
    """scenarios 参数缺省时读 config.slippage_scenarios。"""
    engine = _StubEngine()
    svc = BacktestService(session=None, engine=engine)
    report = svc.run_slippage_comparison(_cfg([0.002, 0.004]), object())
    assert [r["slippage"] for r in report] == [0.002, 0.004]


def test_a1b_slippage_comparison_empty_scenarios_returns_empty() -> None:
    """无情景（None 且 config 无）→ 空报告，不跑 engine。"""
    engine = _StubEngine()
    svc = BacktestService(session=None, engine=engine)
    report = svc.run_slippage_comparison(_cfg(None), object())
    assert report == []
    assert engine.calls == []
