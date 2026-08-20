"""MomentumStrategy：动量策略（Phase 4，Phase 10 接入 UserConfig）。"""
from __future__ import annotations

import logging

import pandas as pd

from quantpilot.core.config_defaults import (
    DEFAULT_MOMENTUM_STRATEGY,
    MomentumStrategyConfig,
)
from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot

logger = logging.getLogger(__name__)

# 追高剔除的观察窗口（交易日）。SDD §7.2.3 注以「近 1 月涨幅」表述，未参数化——
# reversal_exclude_pct（剔除比例）才是 L2 可配项。
_REVERSAL_WINDOW = 20


class MomentumStrategy(BaseStrategy):
    """SDD §7.2.3：3M/6M 涨幅 + 行业相对强度 + 追高剔除。

    Phase 10：`config` 由 ConfigService 注入。
    【降级说明】V1.0 回看期（3M≈60、6M≈120）与 reversal_exclude_pct 在
    `compute_raw_factors` 内部仍硬编码；dataclass 仅作为 Pipeline 快照登记。
    恢复条件：V1.5 完成 lookback/reversal 窗口全参数化。
    """

    name = "momentum"
    display_name = "动量"
    weights = {"return_3m": 0.40, "rs_6m": 0.35, "industry_rs": 0.25}

    # 申万 2021 标准一级行业名称（Tushare stock_industry(src='SW2021') industry_name 值域）
    # 不在此集合的 sw_industry_l1 视为占位值，industry_rs 置中性分 50
    VALID_SW_INDUSTRIES: frozenset[str] = frozenset({
        "农林牧渔", "煤炭", "石油石化", "基础化工", "钢铁", "有色金属",
        "电子", "家用电器", "食品饮料", "纺织服装", "轻工制造", "医药生物",
        "公用事业", "交通运输", "房地产", "商贸零售", "社会服务",
        "银行", "非银金融", "汽车", "机械设备", "国防军工",
        "计算机", "传媒", "通信", "建筑材料", "建筑装饰", "电力设备", "综合",
    })

    def __init__(self, config: MomentumStrategyConfig | None = None) -> None:
        self._cfg = config or DEFAULT_MOMENTUM_STRATEGY

    def compute_raw_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        adj_prices = market_data["adj_prices"].reindex(universe).astype(float)
        financials = market_data["financials"].reindex(universe)
        index_prices = market_data["index_adj_prices"].astype(float)

        # ── return_3m：近 60 交易日收益率 ─────────────────────────────────────
        return_3m = _period_return(adj_prices, 60)

        # ── rs_6m：近 120 交易日收益率 vs 沪深300 ────────────────────────────
        return_6m = _period_return(adj_prices, 120)
        if not index_prices.empty and len(index_prices.columns) >= 121:
            cols = sorted(index_prices.columns)
            idx_return_6m = (
                float(index_prices[cols[-1]].mean()) / float(index_prices[cols[-121]].mean()) - 1.0
            )
        else:
            idx_return_6m = 0.0
        rs_6m = return_6m - idx_return_6m

        # ── industry_rs：行业相对强度 ─────────────────────────────────────────
        if "sw_industry_l1" in financials.columns:
            industries = financials["sw_industry_l1"]
            is_placeholder = ~industries.isin(self.VALID_SW_INDUSTRIES)
            if is_placeholder.all():
                logger.warning(
                    "momentum_industry_rs_placeholder: sw_industry_l1 均为占位值，"
                    "industry_rs 置 50（中性）"
                )
                industry_rs = pd.Series(50.0, index=universe, dtype=float)
            else:
                ind_mean = return_3m.groupby(industries).transform("mean")
                industry_rs = return_3m - ind_mean
                # 占位值行业置 50（原始值）
                industry_rs[is_placeholder] = 50.0
        else:
            logger.warning(
                "momentum_industry_rs_placeholder: financials 无 sw_industry_l1，industry_rs 置 50"
            )
            industry_rs = pd.Series(50.0, index=universe, dtype=float)

        df = pd.DataFrame({
            "return_3m": return_3m,
            "rs_6m": rs_6m,
            "industry_rs": industry_rs,
        }, index=universe)

        return df

    def apply_constraints(
        self,
        raw: pd.DataFrame,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        """V1.5-C C1-1：数据不足 guard + 追高剔除，两者均为「剔除」类 → 置 NaN。

        改造前二者写在 ``score()`` 里，五步管线走 ``compute_strategy_factors``
        取不到 → 生产从未生效（SDD §7.2.3 注的短期反转剔除形同虚设）。
        """
        out = raw.copy()
        adj_prices = market_data["adj_prices"].reindex(universe).astype(float)

        # ── 数据不足 guard ────────────────────────────────────────────────────
        # return_3m / rs_6m 均为 NaN 时，industry_rs 的 50.0 占位值会成为唯一有效
        # 因子，rank(pct=True) 产生 ~0.5 的均匀分数（6 只股票 → 58.3）污染
        # composite。整表置 NaN 让 Scorer 把本策略权重分给其余策略。
        if adj_prices.shape[1] <= self._cfg.lookback_short:
            out.loc[:, :] = float("nan")
            return out

        # ── 追高剔除：近 1M 涨幅处于前 reversal_exclude_pct 的标的不参与本策略 ──
        return_1m = _period_return(adj_prices, _REVERSAL_WINDOW)
        valid_1m = return_1m.dropna()
        # 无离散度时不存在「相对追高」：全体收益率相同就没有谁"追"了谁。少了这道
        # 判定，下面按名次取前 N 名会任意挑 N 只出来剔除。停牌 / 极端一致行情下
        # 这种退化真实存在。
        if valid_1m.empty or valid_1m.nunique() <= 1:
            return out

        # 按**名次**取前 floor(n × pct) 名，不用分位数当阈值：
        # `quantile(1-pct)` + `>=` 在小样本上被线性插值支配——21 只时阈值恰落在第
        # 20 个值上而剔除 2 只（9.5%），2 只时剔除 1 只（50%），都与「前 5%」不符。
        # floor 让「不足 1 只」时不剔除任何标的，这也正是"前 5%"该有的语义。
        n_exclude = int(len(valid_1m) * self._cfg.reversal_exclude_pct)
        if n_exclude <= 0:
            return out
        hit_codes = valid_1m.nlargest(n_exclude).index
        out.loc[out.index.isin(hit_codes), :] = float("nan")
        return out

    def _build_reason(self, ts_code: str, raw_row: pd.Series, final_score: float) -> str:
        r3m = raw_row.get("return_3m", float("nan"))
        r6m_diff = raw_row.get("rs_6m", float("nan"))
        rs_ind = raw_row.get("industry_rs", float("nan"))

        r3m_pct = r3m * 100 if not pd.isna(r3m) else float("nan")
        r6m_label = "超额" if (not pd.isna(r6m_diff) and r6m_diff > 0) else "落后"
        r6m_abs = abs(r6m_diff * 100) if not pd.isna(r6m_diff) else float("nan")

        return (
            f"3月涨幅={r3m_pct:.1f}%，"
            f"相对指数{r6m_label}{r6m_abs:.1f}%，"
            f"行业相对强度={rs_ind:.1f}%。"
        )


def _period_return(adj_prices: pd.DataFrame, n: int) -> pd.Series:
    """计算每只股票最近 n 个交易日的收益率（按行取最后一列 vs 倒数第n列）。"""
    if adj_prices.shape[1] <= n:
        return pd.Series(float("nan"), index=adj_prices.index)
    cols = list(adj_prices.columns)
    start_col = cols[-(n + 1)]
    end_col = cols[-1]
    start_price = adj_prices[start_col].astype(float)
    end_price = adj_prices[end_col].astype(float)
    ret = end_price / start_price - 1.0
    ret = ret.replace([float("inf"), float("-inf")], float("nan"))
    return ret
