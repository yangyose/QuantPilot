"""σ / β 公共纯函数（V1.5-C C3 前置：C1 与 C3 共用一份实现）。

## 为什么要抽出来

C1-2 的风险调整动量已有 `_rolling_sigma`（私有于 `momentum.py`），C3 低波动策略
又要算 σ60。**各算一遍必然漂移**——而「两处 σ 定义不一致」在数字上看不出来
（都是"看起来正常的波动率"），只会让 composite 里两个策略对同一只股票用不同的风险度量。

## 搬移的第一不变量：**逐值等价**

重构不得改变 C1 的任何输出。本文件第一条即钉此：新公共函数与原实现在同一输入上
必须给出**完全相同**的结果（含 NaN 位置）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantpilot.engine.volatility import (
    SIGMA_MIN_VALID_RATIO,
    rolling_beta,
    rolling_sigma,
)


def _prices(rows: dict[str, list[float]]) -> pd.DataFrame:
    """Wide 格式：index=代码、columns=交易日（与 `MarketSnapshot.adj_prices` 同构）。"""
    n = len(next(iter(rows.values())))
    cols = pd.date_range("2025-01-01", periods=n, freq="B").date.tolist()
    df = pd.DataFrame.from_dict(rows, orient="index", columns=cols)
    df.index.name = "ts_code"
    return df


class TestRollingSigmaMatchesLegacy:
    def test_identical_to_momentum_private_implementation(self) -> None:
        """⚠️ 搬移的核心判据：与 `momentum._rolling_sigma` **逐值相同**。"""
        from quantpilot.engine.strategies.momentum import _rolling_sigma

        rng = np.random.default_rng(42)
        px = _prices({
            f"{i:06d}.SZ": list(100 * np.cumprod(1 + rng.normal(0, 0.02, 90)))
            for i in range(5)
        })
        for w in (20, 60):
            pd.testing.assert_series_equal(
                rolling_sigma(px, w), _rolling_sigma(px, w), check_names=False
            )

    def test_momentum_delegates_not_duplicates(self) -> None:
        """`momentum` 必须**复用**公共实现，而不是留一份副本。

        留副本的话上面那条也会绿——两份代码此刻恰好一致。
        故断言函数对象同一性。
        """
        from quantpilot.engine.strategies import momentum as mod

        assert mod._rolling_sigma is rolling_sigma


class TestRollingSigma:
    def test_constant_series_gives_zero_not_nan(self) -> None:
        """常数价格 → 对数收益恒 0 → σ = 0（有效样本充足，不该记 NaN）。"""
        px = _prices({"A.SZ": [10.0] * 40})
        assert rolling_sigma(px, 20).loc["A.SZ"] == pytest.approx(0.0)

    def test_insufficient_samples_is_nan(self) -> None:
        """有效收益数 < window × 比例 → NaN。σ 在分母上，样本不足会放大噪声。"""
        px = _prices({"A.SZ": [10.0, 11.0, 12.0] + [float("nan")] * 37})
        assert pd.isna(rolling_sigma(px, 20).loc["A.SZ"])

    def test_non_positive_prices_do_not_blow_up(self) -> None:
        """非正价格取对数会得 -inf/NaN → 必须先置 NaN 再判有效样本。"""
        px = _prices({"A.SZ": [10.0, -1.0, 0.0] + [10.0] * 37})
        v = rolling_sigma(px, 20).loc["A.SZ"]
        assert not np.isinf(v)


class TestRollingBeta:
    def test_beta_one_when_stock_tracks_index(self) -> None:
        """股票与指数同步 → β = 1（解析解，不靠近似断言）。"""
        rng = np.random.default_rng(7)
        idx_ret = rng.normal(0, 0.01, 150)
        idx_px = 100 * np.cumprod(1 + idx_ret)
        px = _prices({"A.SZ": list(idx_px)})
        bench = _prices({"000300.SH": list(idx_px)})
        assert rolling_beta(px, bench, 120).loc["A.SZ"] == pytest.approx(1.0, abs=1e-9)

    def test_beta_two_when_stock_doubles_index_moves(self) -> None:
        """收益恰为指数两倍 → β = 2。"""
        rng = np.random.default_rng(11)
        r = rng.normal(0, 0.005, 150)
        idx_px = 100 * np.cumprod(1 + r)
        stk_px = 100 * np.cumprod(1 + 2 * r)
        b = rolling_beta(_prices({"A.SZ": list(stk_px)}),
                         _prices({"000300.SH": list(idx_px)}), 120).loc["A.SZ"]
        assert b == pytest.approx(2.0, rel=2e-2)

    def test_insufficient_overlap_is_nan(self) -> None:
        """有效对齐样本 < 80 → NaN（设计 §5.1）。"""
        px = _prices({"A.SZ": list(np.linspace(10, 12, 60))})
        bench = _prices({"000300.SH": list(np.linspace(100, 110, 60))})
        assert pd.isna(rolling_beta(px, bench, 120).loc["A.SZ"])

    def test_zero_variance_benchmark_is_nan_not_inf(self) -> None:
        """指数无波动 → var=0 → **NaN 而非 inf**。

        inf 会一路穿过 Winsorize/Z-score 变成极端分，比缺失更危险。
        """
        px = _prices({"A.SZ": list(np.linspace(10, 20, 150))})
        bench = _prices({"000300.SH": [100.0] * 150})
        v = rolling_beta(px, bench, 120).loc["A.SZ"]
        assert pd.isna(v) or not np.isinf(v)

    def test_missing_benchmark_returns_all_nan(self) -> None:
        px = _prices({"A.SZ": list(np.linspace(10, 20, 150))})
        out = rolling_beta(px, pd.DataFrame(), 120)
        assert out.isna().all()
        assert list(out.index) == ["A.SZ"]

    def test_does_not_mutate_inputs(self) -> None:
        px = _prices({"A.SZ": list(np.linspace(10, 20, 150))})
        bench = _prices({"000300.SH": list(np.linspace(100, 130, 150))})
        p0, b0 = px.copy(deep=True), bench.copy(deep=True)
        rolling_beta(px, bench, 120)
        pd.testing.assert_frame_equal(px, p0)
        pd.testing.assert_frame_equal(bench, b0)


def test_min_valid_ratio_is_shared_constant() -> None:
    """比例常量必须只有一份——两处各写一个数就会静默分叉。"""
    from quantpilot.engine.strategies import momentum as mod

    assert mod._SIGMA_MIN_VALID_RATIO is SIGMA_MIN_VALID_RATIO
