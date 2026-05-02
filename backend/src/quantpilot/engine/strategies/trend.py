"""TrendStrategy：趋势跟踪策略（Phase 4，Phase 10 接入 UserConfig）。"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]

from quantpilot.core.config_defaults import DEFAULT_TREND_STRATEGY, TrendStrategyConfig
from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot


class TrendStrategy(BaseStrategy):
    """SDD §7.2.1：MA 排列 + MACD + 价格突破。

    Phase 10：`config` 由 ConfigService 注入，支持用户调整 MA 与 MACD 参数。
    【降级说明】V1.0 因子内部的 rolling 窗口（5/10/20/60）与 MACD 参数仍硬编码在
    `compute_raw_factors` 中；dataclass 仅作为 Pipeline 快照登记。恢复条件：V1.5
    将 rolling 窗口完全参数化（`ma_short`/`ma_long`/`macd_*`）。
    """

    name = "trend"
    display_name = "趋势跟踪"
    weights = {"ma_alignment": 0.40, "macd_signal": 0.30, "price_breakout": 0.30}

    def __init__(self, config: TrendStrategyConfig | None = None) -> None:
        self._cfg = config or DEFAULT_TREND_STRATEGY

    def compute_raw_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        adj_prices = market_data["adj_prices"].reindex(universe)
        results: dict[str, dict[str, float]] = {}

        for ts_code in universe:
            if ts_code not in adj_prices.index:
                results[ts_code] = {
                    "ma_alignment": float("nan"),
                    "macd_signal": float("nan"),
                    "price_breakout": float("nan"),
                }
                continue

            close = adj_prices.loc[ts_code].dropna().astype(float)
            if len(close) < 65:  # 至少需要 60 日计算 MA60
                results[ts_code] = {
                    "ma_alignment": float("nan"),
                    "macd_signal": float("nan"),
                    "price_breakout": float("nan"),
                }
                continue

            # ── MA 排列（MA5>MA10>MA20>MA60 满足条件数 / 3）────────────────────
            ma5 = close.rolling(5).mean().iloc[-1]
            ma10 = close.rolling(10).mean().iloc[-1]
            ma20 = close.rolling(20).mean().iloc[-1]
            ma60 = close.rolling(60).mean().iloc[-1]
            last_close = close.iloc[-1]

            conditions_met = sum([
                ma5 > ma10,
                ma10 > ma20,
                ma20 > ma60,
            ])
            ma_alignment = conditions_met / 3.0

            # ── MACD（DIF/DEA，pandas_ta）────────────────────────────────────
            macd_df = ta.macd(close, fast=12, slow=26, signal=9)
            if macd_df is None or macd_df.empty:
                macd_signal = float("nan")
            else:
                dif = macd_df.iloc[-1, 0]   # MACD_12_26_9
                dea = macd_df.iloc[-1, 2]   # MACDs_12_26_9
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

            results[ts_code] = {
                "ma_alignment": ma_alignment,
                "macd_signal": macd_signal,
                "price_breakout": price_breakout,
            }

        return pd.DataFrame(results).T.reindex(universe)

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
