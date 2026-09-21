"""向量化因子路径 ≡ 逐股循环路径（2026-09-21）。

背景：`MeanReversionStrategy` / `TrendStrategy` 的 `compute_raw_factors` 逐股循环调 pandas_ta，
6 日回测里两者合计 125 s（每日约 21 s），是回测与面板的第一大时间项。向量化只在**语义完全等价**
的前提下上：pandas 的 rolling / ewm 在宽表上逐列计算，与逐股 Series 计算走同一内核；唯一分歧
是逐股路径先 `dropna()`——历史里有**内部** NaN（停牌日）的股票被压缩后再算，宽表算不出同样的
结果。故：只有 NaN 全在前导位置（新上市）或没有 NaN 的股票走向量化，其余回落循环。

判据（§4.4「改参数 → 结果必须变」的孪生：**改实现 → 结果不许变**）：
- 随机价格面板（含前导 NaN / 内部 NaN / 不足窗口 四类行），向量化结果与「强制全走循环」逐元素
  相等（1e-9），NaN 位置也一致
- 内部 NaN 的行必须被路由到循环（用 spy 数循环被调次数）
- 改 rsi_period / bbands 参数，向量化与循环仍一致（参数被两条路径同样消费）
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quantpilot.core.config_defaults import MeanReversionStrategyConfig, TrendStrategyConfig
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.trend import TrendStrategy


def _panel(n_stocks: int = 60, n_days: int = 130, seed: int = 0) -> tuple[pd.Index, dict]:
    rng = np.random.default_rng(seed)
    cols: list[date] = []
    d = date(2025, 1, 1)
    while len(cols) < n_days:
        if d.weekday() < 5:
            cols.append(d)
        d += timedelta(days=1)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n_stocks, n_days)), axis=1))
    df = pd.DataFrame(px, columns=cols, index=pd.Index([f"{i:06d}.SZ" for i in range(n_stocks)],
                                                        name="ts_code"))
    # 四类行：0~39 完整；40~47 前导 NaN（新上市）；48~55 内部 NaN（停牌）；56~59 不足窗口
    for i in range(40, 48):
        df.iloc[i, : rng.integers(5, 60)] = np.nan
    for i in range(48, 56):
        gaps = rng.choice(np.arange(30, n_days - 5), size=rng.integers(1, 6), replace=False)
        df.iloc[i, gaps] = np.nan
    for i in range(56, 60):
        df.iloc[i, : n_days - rng.integers(5, 24)] = np.nan
    return df.index, {"trade_date": cols[-1], "adj_prices": df}


def _assert_same(a: pd.DataFrame, b: pd.DataFrame) -> None:
    assert list(a.index) == list(b.index) and list(a.columns) == list(b.columns)
    assert a.isna().equals(b.isna()), "NaN 位置不一致"
    np.testing.assert_allclose(
        a.fillna(0).to_numpy(float), b.fillna(0).to_numpy(float), rtol=0, atol=1e-9,
    )


@pytest.mark.parametrize("strategy_cls, cfgs", [
    (MeanReversionStrategy, [None, MeanReversionStrategyConfig(rsi_period=7, bbands_period=10,
                                                              bbands_std=1.5)]),
    (TrendStrategy, [None, TrendStrategyConfig(macd_fast=5, macd_slow=13, macd_signal=4,
                                               ma_short=15, ma_long=45)]),
])
def test_vectorized_equals_loop_on_mixed_panel(strategy_cls, cfgs, monkeypatch) -> None:
    for cfg in cfgs:
        for seed in (0, 1, 2):
            universe, snap = _panel(seed=seed)
            s = strategy_cls(config=cfg)
            fast = s.compute_raw_factors(universe, snap)
            monkeypatch.setattr(strategy_cls, "_VECTORIZE", False)
            slow = strategy_cls(config=cfg).compute_raw_factors(universe, snap)
            monkeypatch.setattr(strategy_cls, "_VECTORIZE", True)
            _assert_same(fast, slow)
            # 有值的行不能是空的（否则「全 NaN 等于全 NaN」也过）
            assert fast.iloc[:40].notna().all().all()


@pytest.mark.parametrize("strategy_cls", [MeanReversionStrategy, TrendStrategy])
def test_interior_nan_rows_fall_back_to_loop(strategy_cls, monkeypatch) -> None:
    universe, snap = _panel(seed=3)
    calls: list[str] = []
    orig = strategy_cls._factors_for_series

    def spy(self, ts_code, close):
        calls.append(ts_code)
        return orig(self, ts_code, close)

    monkeypatch.setattr(strategy_cls, "_factors_for_series", spy)
    strategy_cls().compute_raw_factors(universe, snap)
    interior = {f"{i:06d}.SZ" for i in range(48, 56)}
    assert interior <= set(calls), "内部 NaN 的行没有回落到循环"
    assert not (set(calls) & {f"{i:06d}.SZ" for i in range(0, 40)}), "完整行不该走循环"
