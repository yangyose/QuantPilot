"""MeanReversionStrategy：均值回归策略（Phase 4，Phase 10 接入 UserConfig）。"""
from __future__ import annotations

import logging

import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]

from quantpilot.core.config_defaults import (
    DEFAULT_MEAN_REVERSION_STRATEGY,
    MeanReversionStrategyConfig,
)
from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot
from quantpilot.engine.universe import UniverseFilter

# 复用同一份常量，不另写——各写一份必漂，而「两处金融股定义不一致」
# 在数字上看不出来。
_FINANCIAL_INDUSTRIES = UniverseFilter.FINANCIAL_INDUSTRIES

# SDD-EXT-04：F-Score >= 6 方可参与均值回归。
# ⚠️ 阈值现由 `MeanReversionStrategyConfig.piotroski_min_score` 提供——本常量
# 只作历史注记，**不要在判定里读它**，否则配置就成了平行副本（CLAUDE.md §4.11 第 2 例）。
# 金融股替代判据（其会计科目不适用 Piotroski）
_FINANCIAL_MIN_ROE = 0.05

logger = logging.getLogger(__name__)


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
    # ⚠️ 生产不读 weights（五步管线因子等权）——改这里选股不会变。见 BaseStrategy.weights
    weights = {"rsi_oversold": 0.35, "price_deviation": 0.35, "bb_position": 0.30}

    def __init__(self, config: MeanReversionStrategyConfig | None = None) -> None:
        self._cfg = config or DEFAULT_MEAN_REVERSION_STRATEGY

    def apply_constraints(
        self,
        raw: pd.DataFrame,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        """C2 / SDD-EXT-04：F-Score < 6 的股票不参与均值回归。

        写在这里而非 `score()` 末尾——C1-1 记过教训：`compute_strategy_factors`
        从不调用 `score()`，写在那里的约束在五步管线里完全不生效。

        三条分支：

        1. **命中门控** → 该行三列**全置 NaN**。⚠️ 禁止置 0：Z-score 后 0 是
           横截面均值，置 0 等于发了张中性分而不是把它排除。
        2. **金融股**（复用 `UniverseFilter.FINANCIAL_INDUSTRIES`）走 `roe > 5%`。
           【降级说明】当前降级内容 = 金融股仅 ROE 判据；原因 = SDD 外评原文的
           「不良贷款率未显著上升」在 Tushare `fina_indicator` 无对应字段；
           恢复条件 = 接入含 NPL 的数据源后补第二判据。
        3. **`f_score` 为 NaN（不可判）→ 不门控**。不可判 ≠ 不合格；
           当低分处理会让数据缺口伪装成基本面恶化。

        快照未提供 `f_score`（回填未完成 / 回测路径）→ **恒等返回**，
        并记 INFO 便于确认门控是否真的在生效（C-4：可见的降级）。

        ⚠️ **默认 `piotroski_gate_enabled=False`（影子模式）**：上面三条分支照常
        计算、照常记日志（`piotroski_gate_shadow: blocked=N`），但**不改数据**。
        开发集实测该门控在任何阈值上都无显著收益（见 `config_defaults` 的
        【降级说明】与 `docs/reviews/scoring_monotonicity_2026-09-09.md` §7）。
        """
        f_score = market_data.get("f_score")
        if f_score is None or raw.empty:
            logger.info("piotroski_gate_skipped: 快照未提供 f_score，门控未生效")
            return raw

        idx = raw.index
        fs = pd.to_numeric(pd.Series(f_score), errors="coerce").reindex(idx)

        info = market_data.get("stock_info")
        industry = (
            info["sw_industry_l1"].reindex(idx)
            if info is not None and "sw_industry_l1" in info.columns
            else pd.Series(index=idx, dtype=object)
        )
        is_financial = industry.isin(_FINANCIAL_INDUSTRIES)

        fin = market_data.get("financials")
        roe = (
            pd.to_numeric(fin["roe"], errors="coerce").reindex(idx)
            if fin is not None and "roe" in fin.columns
            else pd.Series(float("nan"), index=idx, dtype=float)
        )

        threshold = self._cfg.piotroski_min_score
        # 非金融：f_score 有值且低于阈值 → 门控（NaN 不门控）
        blocked = (~is_financial) & fs.notna() & (fs < threshold)
        # 金融：roe 有值且 <= 5% → 门控（roe 缺失同样不门控）
        blocked_fin = is_financial & roe.notna() & (roe <= _FINANCIAL_MIN_ROE)
        hit = blocked | blocked_fin

        enabled = self._cfg.piotroski_gate_enabled
        logger.info(
            "piotroski_gate_%s: blocked=%d unjudgeable=%d financial_alt=%d threshold=%.1f",
            "applied" if enabled else "shadow",
            int(hit.sum()), int(fs.isna().sum()), int(is_financial.sum()), threshold,
        )
        # 影子模式：算完、报完，但不动数据。日志里 blocked= 就是「本来会剔掉几只」，
        # 观察期靠它累积证据；改成不算不报就什么也观察不到。
        if not enabled or not hit.any():
            return raw
        out = raw.copy()
        out.loc[hit, :] = float("nan")
        return out

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
