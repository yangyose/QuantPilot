"""滑点情景对比的输出字段必须真取到值（2026-09-23）。

## 缺陷

`BacktestService.run_slippage_comparison` 的输出键是**对外契约**（前端表格列
`frontend/src/views/BacktestView.vue::slippageColumns`、`types/api.ts::SlippageScenario`、
`scripts/slippage_sensitivity.py` 的 CSV 表头），而取值来源是 `BacktestReport.generate`
的键——两套词表不同名：`total_return` ← `cumulative_return`、`sharpe` ← `sharpe_ratio`。
原实现写的是 `perf.get("total_return", 0.0)`，**该键 engine 从来不产出** → 前端
「累计收益」列对每一档滑点都显示 **0.00%**，且 DoD（真机-P14-4-3）只看 sharpe 的单调性，
所以从 V1.5-A 上线起没人发现。同一处漂移还让 `run_backtest_local.py` 的汇总
静默少打三行（`total_return` / `annual_return` / `sharpe` 三个名字都不存在）。

## 判据（§4.11「参数是否真被消费」的键名版）

不是「跑通」也不是「有这个字段」，而是**每个 `perf.get("X")` 里的 X 必须出现在真实
`BacktestReport.generate(...)` 的输出键集合里**——用 AST 取调用点的字面量、用真实函数
（不是替身）取键集合，两边对账。键名再漂移一次，这条立刻红。
"""
from __future__ import annotations

import ast
import inspect
from datetime import date, timedelta

from quantpilot.engine.backtest.engine import BacktestConfig
from quantpilot.engine.backtest.report import BacktestReport
from quantpilot.services.backtest_service import BacktestService


def _real_performance_keys() -> set[str]:
    nav = {date(2026, 1, 5) + timedelta(days=i): 1.0 + i * 0.001 for i in range(10)}
    cfg = BacktestConfig(
        start_date=date(2026, 1, 5), end_date=date(2026, 1, 14),
        initial_capital=1_000_000.0, strategy_config={}, account_config={},
    )
    keys = set(BacktestReport.generate(nav, [], cfg))
    assert {"cumulative_return", "sharpe_ratio", "max_drawdown"} <= keys, (
        f"绩效报告的键集合本身变了：{sorted(keys)}"
    )
    return keys


def _perf_get_literals() -> set[str]:
    src = inspect.getsource(BacktestService.run_slippage_comparison)
    out: set[str] = set()
    for node in ast.walk(ast.parse(src.lstrip())):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "get"
            and getattr(getattr(node.func, "value", None), "id", None) == "perf"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            out.add(node.args[0].value)
    return out


def test_every_perf_key_read_actually_exists() -> None:
    read = _perf_get_literals()
    assert read, "没解析到任何 perf.get(\"...\") 调用——取值方式变了，本测试需同步更新"
    missing = sorted(read - _real_performance_keys())
    assert not missing, (
        f"run_slippage_comparison 读的这些键 engine 不产出 → 该列恒为默认值 0.0：{missing}"
    )


def test_report_exposes_the_contract_keys_frontend_renders() -> None:
    """输出键名不得随手改——前端列 dataIndex 与 CSV 表头都按这套名字取。"""
    src = inspect.getsource(BacktestService.run_slippage_comparison)
    for key in ("slippage", "total_return", "max_drawdown", "sharpe", "annualized_return"):
        assert f'"{key}":' in src, f"对外契约字段 {key} 从输出里消失了（前端那一列会变空）"
