"""MeanReversionStrategy：均值回归策略（Phase 4，Phase 10 接入 UserConfig）。"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]

from quantpilot.core.config_defaults import (
    DEFAULT_MEAN_REVERSION_STRATEGY,
    MeanReversionStrategyConfig,
)
from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot


class MeanReversionStrategy(BaseStrategy):
    """SDD §7.2.2：RSI + 乖离率 + 布林带。

    Phase 10：`config` 由 ConfigService 注入。
    V1.5-C C1-2：rsi_period / bbands_period / bbands_std 已全部传入 pandas_ta，
    原【降级说明】（"V1.0 仍硬编码，dataclass 仅作 Pipeline 快照登记"）的恢复条件
    已兑现，故移除。注：乖离率的均线窗口复用 bbands_period（二者同为"中枢均线"
    口径，SDD 未给独立参数）。
    """

    name = "mean_reversion"
    display_name = "均值回归"
    weights = {"rsi_oversold": 0.35, "price_deviation": 0.35, "bb_position": 0.30}

    def __init__(self, config: MeanReversionStrategyConfig | None = None) -> None:
        self._cfg = config or DEFAULT_MEAN_REVERSION_STRATEGY

    def compute_raw_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        adj_prices = market_data["adj_prices"].reindex(universe)
        results: dict[str, dict[str, float]] = {}

        for ts_code in universe:
            if ts_code not in adj_prices.index:
                results[ts_code] = _nan_row()
                continue

            close = adj_prices.loc[ts_code].dropna().astype(float)
            if len(close) < 25:
                results[ts_code] = _nan_row()
                continue

            last_close = float(close.iloc[-1])

            # ── RSI（越低越超卖，直接用原始值；rank 时低 RSI → 低 rank → 低百分位
            #    均值回归策略希望超卖（低RSI）得高分，所以取 100-RSI 让低RSI→高值）─────
            rsi_series = ta.rsi(close, length=self._cfg.rsi_period)
            if rsi_series is None or rsi_series.dropna().empty:
                rsi_oversold = float("nan")
            else:
                raw_rsi = float(rsi_series.dropna().iloc[-1])
                rsi_oversold = 100.0 - raw_rsi   # 超卖（低 RSI）→ 高值 → rank 高分

            # ── 乖离率（MA20-close）/ MA20，越大（价格低于均线越多）得分越高 ─────────
            ma20 = float(close.rolling(self._cfg.bbands_period).mean().iloc[-1])
            if pd.isna(ma20) or ma20 == 0:
                price_deviation = float("nan")
            else:
                price_deviation = (ma20 - last_close) / ma20   # 价格低于均线 → 正值 → 高分

            # ── 布林带位置（越接近下轨得分越高）───────────────────────────────────
            # pandas_ta 0.4.x 把 `std` 拆成 `lower_std` / `upper_std`，旧的 `std=`
            # 会被 **kwargs 静默吞掉。原代码写的 `std=2.0` 因此一直是**无效参数**
            # ——只因默认值恰好也是 2.0 才没出事。传错名字不报错，必须按新签名传。
            bb_df = ta.bbands(
                close,
                length=self._cfg.bbands_period,
                lower_std=self._cfg.bbands_std,
                upper_std=self._cfg.bbands_std,
            )
            if bb_df is None or bb_df.empty:
                bb_position = float("nan")
            else:
                col_map = {c.split("_")[0]: c for c in bb_df.columns}  # {"BBL": "BBL_20_2.0", ...}
                bb_lower = float(bb_df.iloc[-1][col_map["BBL"]])
                bb_upper = float(bb_df.iloc[-1][col_map["BBU"]])
                band_width = bb_upper - bb_lower
                if pd.isna(bb_lower) or pd.isna(bb_upper) or band_width == 0:
                    bb_position = float("nan")
                else:
                    # bb_pos = (close - lower) / width，越接近下轨 → 越小 → 取反后越大
                    bb_pos_raw = (last_close - bb_lower) / band_width
                    bb_position = 1.0 - bb_pos_raw   # 下轨 → 高值 → rank 高分

            results[ts_code] = {
                "rsi_oversold": rsi_oversold,
                "price_deviation": price_deviation,
                "bb_position": bb_position,
            }

        return pd.DataFrame(results).T.reindex(universe)

    def _build_reason(self, ts_code: str, raw_row: pd.Series, final_score: float) -> str:
        rsi_inv = raw_row.get("rsi_oversold", float("nan"))
        dev = raw_row.get("price_deviation", float("nan"))
        bb_inv = raw_row.get("bb_position", float("nan"))

        rsi_val = 100.0 - rsi_inv if not pd.isna(rsi_inv) else float("nan")
        if not pd.isna(rsi_val) and rsi_val < 30:
            rsi_label = "超卖"
        elif not pd.isna(rsi_val) and rsi_val > 70:
            rsi_label = "超买"
        else:
            rsi_label = "正常"

        dev_pct = dev * 100 if not pd.isna(dev) else float("nan")
        bb_pos = 1.0 - bb_inv if not pd.isna(bb_inv) else float("nan")

        return (
            f"RSI(14)={rsi_val:.1f}（{rsi_label}），"
            f"偏离MA20={dev_pct:.1f}%，"
            f"布林带位置={bb_pos:.2f}。"
        )


def _nan_row() -> dict[str, float]:
    return {
        "rsi_oversold": float("nan"),
        "price_deviation": float("nan"),
        "bb_position": float("nan"),
    }
