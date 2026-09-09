"""LowVolatilityStrategy（V1.5-C C3 / SDD §7.3）：低波动 + 低 Beta。

## 因子方向是本文件的第一判据

两个因子都取**负号**（`-σ60` / `-β120`），使「低波动 → 高值」。
方向搞反不会报错、不会让任何「不抛异常」的测试变红，只会让系统**专挑高波动股**——
与策略意图正好相反。故逐因子钉死「低波动股的因子值必须更高」。

## 无新数据源

σ60 与 β120 只用 `adj_prices` + `index_adj_prices`，两者都已在 `MarketSnapshot`。
【降级说明】β 窗口取 120 而非教科书的 252，是为复用现有 ≈120 交易日的价格窗口
（对数据层零改动、对生产内存零额外开销）；`beta_window` 已参数化，
恢复条件 = 价格窗口扩至 ≥400 日历天并实测内存可接受。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantpilot.core.config_defaults import LowVolatilityStrategyConfig
from quantpilot.engine.strategies.low_volatility import LowVolatilityStrategy

_BENCH = "000300.SH"


def _wide(rows: dict[str, list[float]]) -> pd.DataFrame:
    n = len(next(iter(rows.values())))
    cols = pd.date_range("2024-01-01", periods=n, freq="B").date.tolist()
    df = pd.DataFrame.from_dict(rows, orient="index", columns=cols)
    df.index.name = "ts_code"
    return df


def _snapshot(stock: dict[str, list[float]], bench: list[float]) -> dict:
    return {
        "adj_prices": _wide(stock),
        "index_adj_prices": _wide({_BENCH: bench}),
    }


def _series(n: int, vol: float, seed: int, beta: float = 1.0,
            bench_ret: np.ndarray | None = None) -> tuple[list[float], np.ndarray]:
    rng = np.random.default_rng(seed)
    if bench_ret is None:
        bench_ret = rng.normal(0, 0.01, n)
    idio = rng.normal(0, vol, n)
    r = beta * bench_ret + idio
    return list(100 * np.cumprod(1 + r)), bench_ret


class TestFactorDirection:
    def test_low_volatility_stock_scores_higher(self) -> None:
        """⚠️ 方向：低波动股的 `inv_volatility` 必须**更高**。

        取反不会报错，只会让系统专挑高波动股——与策略意图相反。
        """
        calm, br = _series(150, 0.002, seed=1)
        wild, _ = _series(150, 0.05, seed=2, bench_ret=br)
        raw = LowVolatilityStrategy().compute_raw_factors(
            pd.Index(["CALM.SZ", "WILD.SZ"], name="ts_code"),
            _snapshot({"CALM.SZ": calm, "WILD.SZ": wild}, list(100 * np.cumprod(1 + br))),
        )
        assert raw.loc["CALM.SZ", "inv_volatility"] > raw.loc["WILD.SZ", "inv_volatility"]

    def test_low_beta_stock_scores_higher(self) -> None:
        rng = np.random.default_rng(3)
        br = rng.normal(0, 0.012, 150)
        lo, _ = _series(150, 0.001, seed=4, beta=0.3, bench_ret=br)
        hi, _ = _series(150, 0.001, seed=5, beta=1.8, bench_ret=br)
        raw = LowVolatilityStrategy().compute_raw_factors(
            pd.Index(["LO.SZ", "HI.SZ"], name="ts_code"),
            _snapshot({"LO.SZ": lo, "HI.SZ": hi}, list(100 * np.cumprod(1 + br))),
        )
        assert raw.loc["LO.SZ", "inv_beta"] > raw.loc["HI.SZ", "inv_beta"]

    def test_both_factors_are_negated(self) -> None:
        """负号必须真的加了：正常波动的股票两个因子都应 < 0。"""
        px, br = _series(150, 0.01, seed=6)
        raw = LowVolatilityStrategy().compute_raw_factors(
            pd.Index(["A.SZ"], name="ts_code"),
            _snapshot({"A.SZ": px}, list(100 * np.cumprod(1 + br))),
        )
        assert raw.loc["A.SZ", "inv_volatility"] < 0
        assert raw.loc["A.SZ", "inv_beta"] < 0


class TestContract:
    def test_columns_match_weights_keys(self) -> None:
        """因子矩阵的列必须与 `weights` 的键完全一致。

        ⚠️ `Scorer.aggregate` 是 `for col in df.columns` 逐列处理、**不读 weights**，
        多一列就是多一个因子参与合成（§4.4 记过 σ 辅助列污染 composite 的教训）。
        """
        px, br = _series(150, 0.01, seed=7)
        raw = LowVolatilityStrategy().compute_raw_factors(
            pd.Index(["A.SZ"], name="ts_code"),
            _snapshot({"A.SZ": px}, list(100 * np.cumprod(1 + br))),
        )
        assert set(raw.columns) == set(LowVolatilityStrategy.weights)

    def test_required_history_days_covers_beta_window(self) -> None:
        """自报窗口深度必须 ≥ beta_window + 1（算 n 日收益要 n+1 列）。

        自报少了 → `ScoringService` 取的价格窗口不够 → 因子静默全 NaN
        （C1-3 记过这条，代价是 momentum 的 `rs_6m` 有效率 0/2274）。
        """
        s = LowVolatilityStrategy()
        assert s.required_history_days >= s._cfg.beta_window + 1

    def test_config_parameters_are_consumed(self) -> None:
        """改窗口 → 结果必须变（§4.4：接了配置却不读的代码能跑过任何冒烟测试）。"""
        px, br = _series(200, 0.01, seed=8)
        snap = _snapshot({"A.SZ": px}, list(100 * np.cumprod(1 + br)))
        idx = pd.Index(["A.SZ"], name="ts_code")
        a = LowVolatilityStrategy().compute_raw_factors(idx, snap)
        b = LowVolatilityStrategy(
            LowVolatilityStrategyConfig(volatility_window=20, beta_window=100)
        ).compute_raw_factors(idx, snap)
        assert a.loc["A.SZ", "inv_volatility"] != b.loc["A.SZ", "inv_volatility"]
        assert a.loc["A.SZ", "inv_beta"] != b.loc["A.SZ", "inv_beta"]


class TestDegradation:
    def test_missing_benchmark_yields_nan_beta_but_keeps_sigma(self) -> None:
        """指数缺失 → `inv_beta` NaN，但 `inv_volatility` 仍可算。

        整条策略因缺指数而全废，比只丢一个因子更糟。
        """
        px, _ = _series(150, 0.01, seed=9)
        raw = LowVolatilityStrategy().compute_raw_factors(
            pd.Index(["A.SZ"], name="ts_code"),
            {"adj_prices": _wide({"A.SZ": px}), "index_adj_prices": pd.DataFrame()},
        )
        assert pd.isna(raw.loc["A.SZ", "inv_beta"])
        assert pd.notna(raw.loc["A.SZ", "inv_volatility"])

    def test_short_history_yields_nan_not_zero(self) -> None:
        """历史不足 → NaN，**不是 0**。0 在 Z-score 后是横截面均值 = 中性分。"""
        px, br = _series(30, 0.01, seed=10)
        raw = LowVolatilityStrategy().compute_raw_factors(
            pd.Index(["A.SZ"], name="ts_code"),
            _snapshot({"A.SZ": px}, list(100 * np.cumprod(1 + br))),
        )
        assert raw.loc["A.SZ"].isna().all()

    def test_absent_stock_is_all_nan(self) -> None:
        px, br = _series(150, 0.01, seed=11)
        raw = LowVolatilityStrategy().compute_raw_factors(
            pd.Index(["A.SZ", "GHOST.SZ"], name="ts_code"),
            _snapshot({"A.SZ": px}, list(100 * np.cumprod(1 + br))),
        )
        assert raw.loc["GHOST.SZ"].isna().all()

    def test_empty_snapshot_returns_empty_frame_with_columns(self) -> None:
        raw = LowVolatilityStrategy().compute_raw_factors(
            pd.Index([], name="ts_code"), {}
        )
        assert set(raw.columns) == set(LowVolatilityStrategy.weights)
        assert raw.empty


class TestRegisteredAtEveryConstructionSite:
    """⚠️ 策略必须在**每一处**组装点注册，漏一处即「不同路径算的不是同一个东西」。

    设计 §5.2 说三处（`api/deps.py` / `daily_pipeline._cp2_scoring` /
    `backtest_service`），实测是**四处**——还有
    `services/scoring_factory.py::build_default_strategies`，
    而那正是**面板脚本与回填脚本**走的那条路。漏它的后果是：
    生产管线有 5 策略、离线研究只有 4 策略，两边算出来的 composite 不可比，
    **且不报错**。

    判据只能在**调用点**上验（§4.11：构造 spy 再调用它的测试是自证式的）。
    """

    _SITES = (
        ("api/deps.py", None),
        ("pipeline/daily_pipeline.py", "_cp2_scoring"),
        ("services/backtest_service.py", None),
        ("services/scoring_factory.py", "build_default_strategies"),
    )

    @staticmethod
    def _strategy_calls(path: str, func: str | None) -> set[str]:
        import ast
        import pathlib

        src_root = pathlib.Path(__file__).resolve().parents[2] / "src" / "quantpilot"
        tree = ast.parse((src_root / path).read_text(encoding="utf-8"))
        if func is not None:
            tree = next(
                n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == func
            )
        return {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

    @pytest.mark.parametrize(("path", "func"), _SITES)
    def test_site_registers_low_volatility(self, path: str, func: str | None) -> None:
        calls = self._strategy_calls(path, func)
        assert "ValueStrategy" in calls, f"{path} 不是策略组装点了？测试前提已失效"
        assert "LowVolatilityStrategy" in calls, (
            f"{path} 未注册 LowVolatilityStrategy —— 该路径与其余路径算的不是同一个 composite"
        )

    def test_all_four_sites_agree_on_the_strategy_set(self) -> None:
        """四处的策略集合必须完全一致——不一致就是「路径分叉」，数字上看不出来。"""
        sets = [self._strategy_calls(p, f) for p, f in self._SITES]
        strategy_only = [
            {c for c in s if c.endswith("Strategy")} for s in sets
        ]
        assert all(s == strategy_only[0] for s in strategy_only), (
            f"各组装点策略集合不一致：{strategy_only}"
        )
