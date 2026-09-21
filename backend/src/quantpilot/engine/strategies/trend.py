"""TrendStrategy：趋势跟踪策略（Phase 4，Phase 10 接入 UserConfig）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]

from quantpilot.core.config_defaults import DEFAULT_TREND_STRATEGY, TrendStrategyConfig
from quantpilot.engine.strategies.base import (
    DEFAULT_REQUIRED_HISTORY_DAYS,
    BaseStrategy,
    MarketSnapshot,
)


class TrendStrategy(BaseStrategy):
    """SDD §7.2.1：MA 排列 + MACD + 价格突破。

    Phase 10：`config` 由 ConfigService 注入，支持用户调整 MA 与 MACD 参数。
    【降级说明】V1.0 因子内部的 rolling 窗口（5/10/20/60）与 MACD 参数仍硬编码在
    `compute_raw_factors` 中；dataclass 仅作为 Pipeline 快照登记。恢复条件：V1.5
    将 rolling 窗口完全参数化（`ma_short`/`ma_long`/`macd_*`）。
    """

    name = "trend"
    display_name = "趋势跟踪"
    # ⚠️ 生产不读 weights（五步管线因子等权）——改这里选股不会变。见 BaseStrategy.weights
    weights = {"ma_alignment": 0.40, "macd_signal": 0.30, "price_breakout": 0.30}

    def __init__(self, config: TrendStrategyConfig | None = None) -> None:
        self._cfg = config or DEFAULT_TREND_STRATEGY

    @property
    def required_history_days(self) -> int:
        """自报窗口深度 = max(默认 65, ma_long + 5)。

        `ma_long` 在设置页可调到 250；不自报的话 `ScoringService` 的价格窗口只按其他策略
        取（约 120 列），MA(ma_long) 算不出 → `ma_alignment` 静默全 NaN 且无告警——
        C1-3（`rs_6m` 0/2274）那一族。
        """
        return max(DEFAULT_REQUIRED_HISTORY_DAYS, self._cfg.ma_long + 5)

    # 向量化开关：只为等价性夹具存在（`test_strategy_vectorized_parity.py` 用它强制全走循环
    # 做对照）。生产恒为 True；别在别处改它。
    _VECTORIZE = True

    @property
    def _min_rows(self) -> int:
        return max(65, self._cfg.ma_long + 5)  # 至少要算得出最长的 MA

    def compute_raw_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        adj_prices = market_data["adj_prices"].reindex(universe)
        cols = ["ma_alignment", "macd_signal", "price_breakout"]
        if adj_prices.empty or len(universe) == 0:
            return pd.DataFrame(float("nan"), index=universe, columns=cols)
        adj = adj_prices.astype(float)
        out = pd.DataFrame(float("nan"), index=universe, columns=cols)

        # ── 向量化路径（2026-09-21）：rolling / ewm 在宽表上逐列算与逐股 Series 同内核；
        # 唯一分歧是逐股路径先 dropna()——历史含**内部** NaN（停牌日）的股票回落循环。
        # pandas_ta 的 EMA 以「前 length 个有效值的 SMA」为种子（TA-Lib 式），
        # `_ema_presma_wide` 逐列按各自的首个有效位对齐复现。等价性由夹具逐元素钉死。
        valid = adj.notna()
        n_valid = valid.sum(axis=1)
        interior_nan = (valid.cummax(axis=1) & ~valid).any(axis=1)
        eligible = (n_valid >= self._min_rows) & ~interior_nan
        if self._VECTORIZE and eligible.any():
            out.loc[eligible] = self._vectorized(adj.loc[eligible])[cols]
        if self._VECTORIZE:
            loop_rows = universe[(n_valid >= self._min_rows) & ~eligible]
        else:
            loop_rows = universe[n_valid >= self._min_rows]
        for ts_code in loop_rows:
            close = adj.loc[ts_code].dropna()
            out.loc[ts_code] = pd.Series(self._factors_for_series(ts_code, close))
        return out

    @staticmethod
    def _ema_presma_wide(px: pd.DataFrame, length: int) -> pd.DataFrame:
        """逐列复现 pandas_ta `ema(presma=True)`：前 length 个有效值的 SMA 作种子，
        之前置 NaN，再 `ewm(span=length, adjust=False)`。px: index=日期, columns=ts_code。"""
        arr = px.to_numpy(dtype=float)
        n_rows, _ = arr.shape
        valid = ~np.isnan(arr)
        fv = np.where(valid.any(axis=0), valid.argmax(axis=0), n_rows)   # 每列首个有效行
        seed_row = fv + length - 1
        sma = px.rolling(length).mean().to_numpy(dtype=float)
        rows = np.arange(n_rows)[:, None]
        seeded = np.where(rows < seed_row, np.nan, arr)
        at_seed = rows == seed_row
        seeded = np.where(at_seed, sma, seeded)
        return pd.DataFrame(seeded, index=px.index, columns=px.columns).ewm(
            span=length, adjust=False
        ).mean()

    def _vectorized(self, adj: pd.DataFrame) -> pd.DataFrame:
        """宽表一次算完三个因子；公式与 `_factors_for_series` 相同。"""
        px = adj.T                                   # index=日期, columns=ts_code
        last_close = px.iloc[-1]
        ma_short, ma_long = self._cfg.ma_short, self._cfg.ma_long
        ma5 = px.rolling(5).mean().iloc[-1]
        ma10 = px.rolling(10).mean().iloc[-1]
        ma_s = px.rolling(ma_short).mean().iloc[-1]
        ma_l = px.rolling(ma_long).mean().iloc[-1]
        ma_alignment = (
            (ma5 > ma10).astype(float) + (ma10 > ma_s).astype(float) + (ma_s > ma_l).astype(float)
        ) / 3.0
        # MACD：dif = ema(fast) − ema(slow)；dea = ema(dif 自其首个有效值起, signal)
        fast_ = self._ema_presma_wide(px, self._cfg.macd_fast)
        slow_ = self._ema_presma_wide(px, self._cfg.macd_slow)
        dif_df = fast_ - slow_
        dea_df = self._ema_presma_wide(dif_df, self._cfg.macd_signal)
        dif = dif_df.iloc[-1]
        dea = dea_df.iloc[-1]
        macd_signal = pd.Series(np.nan, index=px.columns, dtype=float)
        ok = dif.notna() & dea.notna()
        macd_signal[ok] = 0.0
        macd_signal[ok & (dif > dea)] = 0.5
        macd_signal[ok & (dif > dea) & (dea > 0)] = 1.0
        # 价格突破近 20 日高点
        hi20 = px.rolling(20).max().iloc[-1]
        price_breakout = (last_close / hi20).where(hi20.notna() & (hi20 != 0))
        return pd.DataFrame({
            "ma_alignment": ma_alignment,
            "macd_signal": macd_signal,
            "price_breakout": price_breakout,
        })

    def _factors_for_series(self, ts_code: str, close: pd.Series) -> dict[str, float]:
        """逐股路径（历史含内部 NaN 时用）：与向量化路径同公式，pandas_ta 实现。"""
        ma_short, ma_long = self._cfg.ma_short, self._cfg.ma_long
        # ── MA 排列（MA5 > MA10 > MA(ma_short) > MA(ma_long) 满足条件数 / 3）────────
        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma_s = close.rolling(ma_short).mean().iloc[-1]
        ma_l = close.rolling(ma_long).mean().iloc[-1]
        last_close = close.iloc[-1]

        conditions_met = sum([
            ma5 > ma10,
            ma10 > ma_s,
            ma_s > ma_l,
        ])
        ma_alignment = conditions_met / 3.0

        # ── MACD（DIF/DEA，pandas_ta）────────────────────────────────────
        macd_df = ta.macd(
            close,
            fast=self._cfg.macd_fast,
            slow=self._cfg.macd_slow,
            signal=self._cfg.macd_signal,
        )
        if macd_df is None or macd_df.empty:
            macd_signal = float("nan")
        else:
            # 按**位置**取（0=DIF / 2=DEA）：参数化后列名随参数变
            # （MACD_12_26_9 → MACD_3_7_3），按名字取会 KeyError。
            dif = macd_df.iloc[-1, 0]
            dea = macd_df.iloc[-1, 2]
            if pd.isna(dif) or pd.isna(dea):
                macd_signal = float("nan")
            elif dif > dea and dea > 0:
                macd_signal = 1.0
            elif dif > dea:
                macd_signal = 0.5
            else:
                macd_signal = 0.0

        # ── 价格突破近 20 日高点 ──────────────────────────────────────────
        rolling_max_20 = close.rolling(20).max().iloc[-1]
        if pd.isna(rolling_max_20) or rolling_max_20 == 0:
            price_breakout = float("nan")
        else:
            price_breakout = last_close / rolling_max_20  # ∈(0,1]

        return {
            "ma_alignment": ma_alignment,
            "macd_signal": macd_signal,
            "price_breakout": price_breakout,
        }

    def _build_reason(self, ts_code: str, raw_row: pd.Series, final_score: float) -> str:
        ma = raw_row.get("ma_alignment", float("nan"))
        macd = raw_row.get("macd_signal", float("nan"))
        pb = raw_row.get("price_breakout", float("nan"))

        ma_label = "多头" if (not pd.isna(ma) and ma > 0.5) else "空头"
        n_ma = int(round(ma * 3)) if not pd.isna(ma) else 0
        if pd.isna(macd):
            macd_label = "数据不足"
        elif macd == 1.0:
            macd_label = "金叉"
        elif macd == 0.5:
            macd_label = "中性"
        else:
            macd_label = "死叉"
        breakout_label = "突破" if (not pd.isna(pb) and pb > 0.98) else "未突破"

        return (
            f"均线{ma_label}排列（{n_ma}/3 条件满足），"
            f"MACD {macd_label}，"
            f"价格{breakout_label}近期高点。"
        )
