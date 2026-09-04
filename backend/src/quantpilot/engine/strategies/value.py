"""ValueStrategy：价值策略（Phase 4，Phase 10 接入 UserConfig）。"""
from __future__ import annotations

import logging

import pandas as pd

from quantpilot.core.config_defaults import DEFAULT_VALUE_STRATEGY, ValueStrategyConfig
from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot

logger = logging.getLogger(__name__)


class ValueStrategy(BaseStrategy):
    """SDD §7.2.4：PE/PB 历史分位 + ROE + 价值陷阱规避。

    Phase 10：`config` 由 ConfigService 注入（`pe_pb_history_years`）。
    【降级说明】V1.0 历史窗口由 ScoringService 在构造 MarketSnapshot 时从数据源
    截取，`pe_pb_history_years` 目前仅登记到 Pipeline 快照；恢复条件：V1.5 在
    ScoringService 内将 dataclass 配置值传给 MarketDataRepository 控制取数窗口。
    """

    name = "value"
    display_name = "价值"
    weights = {"pe_percentile": 0.35, "pb_percentile": 0.30, "roe_quality": 0.35}

    def __init__(self, config: ValueStrategyConfig | None = None) -> None:
        self._cfg = config or DEFAULT_VALUE_STRATEGY

    def compute_raw_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        daily_quotes = market_data["daily_quotes"].reindex(universe).astype(float, errors="ignore")
        financials = market_data["financials"].reindex(universe)
        pe_pb_history = market_data["pe_pb_history"]

        # ── PE 历史分位（越低估 → 低百分位 → 高得分）────────────────────────────
        # pe_percentile = (历史中小于当前值的数量) / 历史总数 → 低分位 = 低估
        # 取 (1 - percentile) 让低估 → 高值 → rank 后高分
        nan_series = pd.Series(float("nan"), index=universe)
        pe_ttm = daily_quotes["pe_ttm"] if "pe_ttm" in daily_quotes.columns else nan_series
        pb = daily_quotes["pb"] if "pb" in daily_quotes.columns else nan_series

        # V1.5-K 性能：Service 若已在 PostgreSQL 内算好分位（`get_pe_pb_percentile_bulk`）
        # 就直接用，不再要求 `pe_pb_history`——后者为算约 3212 个分位数要拉约 380 万行，
        # 是每日管线内存峰值的主项（2026-09-03 生产 OOM 的直接推手）。
        #
        # ⚠️ 保留回退而非直接替换：**回测引擎仍走 `pe_pb_history`**
        # （`backtest/engine.py` 自建 MarketSnapshot 并做 PIT 切片）。两条路必须等价，
        # 由 `tests/unit/test_value_percentile_semantics.py::test_two_paths_agree_on_same_data`
        # 钉死；一旦漂移那条立刻红。
        pe_percentile = _resolve_percentile(
            market_data, "pe_percentile", universe, pe_ttm, pe_pb_history, "pe_ttm"
        )
        pb_percentile = _resolve_percentile(
            market_data, "pb_percentile", universe, pb, pe_pb_history, "pb"
        )

        # ── ROE 质量（横截面 rank，需 TD-1 修复）────────────────────────────────
        if "roe" in financials.columns:
            roe_quality = financials["roe"].astype(float)
        else:
            logger.warning("value_roe_placeholder: financials 无 roe 列，roe_quality 置 NaN")
            roe_quality = pd.Series(float("nan"), index=universe)

        df = pd.DataFrame({
            "pe_percentile": pe_percentile,
            "pb_percentile": pb_percentile,
            "roe_quality": roe_quality,
        }, index=universe)
        return df

    def apply_constraints(
        self,
        raw: pd.DataFrame,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        """SDD §7.2.4 价值陷阱规避：ROE < 行业中位数 → 各因子列截断至该列中位数。

        V1.5-C C1-1：改造前写在 ``score()`` 里做「得分截断至 50」，而五步管线走
        ``compute_strategy_factors`` 从不调用 score() → **生产从未生效**。value 策略
        当前占 composite 权重 0.57(OSCILLATION)~0.87(UPTREND)，即用户看到的买入信号
        主要由它驱动，护栏失效的代价是低 PE/PB + 低 ROE 的价值陷阱未被压制。

        用「分位截断」而非「置 NaN」：§7.2.4 的语义是**上限为中位水平**（仍可参与
        排序，只是不许排在前面），不是把标的排除。分位截断在后续 rank / Z-score
        下不变形，而 0-100 分域的「截断到 50」在中性化 + Z-score 后已无意义。
        """
        out = raw.copy()
        financials = market_data["financials"].reindex(universe)

        if "roe" not in financials.columns or "sw_industry_l1" not in financials.columns:
            logger.warning("value_roe_placeholder: 缺少 roe 或 sw_industry_l1，跳过价值陷阱规避")
            return out

        roe = financials["roe"].astype(float)
        if roe.isna().all():
            logger.warning("value_roe_placeholder: roe 全为 NULL，跳过价值陷阱规避")
            return out

        industry_median_roe = roe.groupby(financials["sw_industry_l1"]).transform("median")
        hit = (roe < industry_median_roe).reindex(out.index).fillna(False)
        if not hit.any():
            return out

        for col in out.columns:
            # 中位数取自**截断前**的该列（逐列独立，不受其他列改动影响）
            median = out[col].quantile(0.5)
            if pd.isna(median):
                continue
            out.loc[hit.to_numpy(), col] = out.loc[hit.to_numpy(), col].clip(upper=median)
        return out

    def _build_reason(self, ts_code: str, raw_row: pd.Series, final_score: float) -> str:
        pe_pct = raw_row.get("pe_percentile", float("nan"))
        pb_pct = raw_row.get("pb_percentile", float("nan"))
        roe = raw_row.get("roe_quality", float("nan"))

        if not pd.isna(pe_pct) and pe_pct > 0.5:
            pe_label = "低估"
        elif not pd.isna(pe_pct) and pe_pct < 0.3:
            pe_label = "高估"
        else:
            pe_label = "合理"
        # pe_pct/pb_pct 均是 1-percentile，高值=低估
        actual_pe_pct = (1.0 - pe_pct) * 100 if not pd.isna(pe_pct) else float("nan")
        actual_pb_pct = (1.0 - pb_pct) * 100 if not pd.isna(pb_pct) else float("nan")

        return (
            f"PE历史分位={actual_pe_pct:.0f}%（{pe_label}），"
            f"PB历史分位={actual_pb_pct:.0f}%，"
            f"ROE={roe:.1f}%。"
        )


def _resolve_percentile(
    market_data: MarketSnapshot,
    key: str,
    universe: pd.Index,
    current_values: pd.Series,
    pe_pb_history: pd.DataFrame,
    col: str,
) -> pd.Series:
    """优先取 Service 预计算的分位；缺失则回退到从历史现算。

    预计算值来自 `MarketDataRepository.get_pe_pb_percentile_bulk`（SQL 下推），
    语义与 `_compute_historical_percentile` 逐股相等（集成测试逐股对照钉死）。
    """
    pre = market_data.get(key)  # type: ignore[attr-defined]
    if pre is not None:
        return pd.Series(pre).reindex(universe).astype(float)
    return _compute_historical_percentile(
        universe, current_values, pe_pb_history, col, inverse=True
    )


def _compute_historical_percentile(
    universe: pd.Index,
    current_values: pd.Series,
    pe_pb_history: pd.DataFrame,
    col: str,
    inverse: bool = True,
) -> pd.Series:
    """
    计算每只股票当前值在过去 5 年历史中的百分位。
    inverse=True 时返回 (1 - percentile)，使低估（低百分位）→ 高值 → rank 后高分。
    """
    if pe_pb_history.empty or col not in pe_pb_history.columns:
        return pd.Series(float("nan"), index=universe)

    available_codes = set(pe_pb_history.index.get_level_values("ts_code"))  # O(1) 查找
    results: dict[str, float] = {}
    for ts_code in universe:
        curr = current_values.get(ts_code, float("nan"))
        if pd.isna(curr):
            results[ts_code] = float("nan")
            continue
        if ts_code not in available_codes:
            results[ts_code] = float("nan")
            continue
        history = pe_pb_history.loc[ts_code, col].astype(float).dropna()
        if len(history) == 0:
            results[ts_code] = float("nan")
            continue
        # percentile_rank = 历史中严格小于当前值的比例
        pct_rank = float((history < float(curr)).sum()) / len(history)
        results[ts_code] = (1.0 - pct_rank) if inverse else pct_rank

    return pd.Series(results, index=universe, dtype=float)
