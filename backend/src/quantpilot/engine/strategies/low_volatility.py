"""LowVolatilityStrategy：低波动异象（V1.5-C C3 / SDD §7.3）。

Engine 层纯函数，严格无 IO。

## 因子

| 因子 | 定义 | 权重 |
|------|------|------|
| `inv_volatility` | `-σ60`（60 交易日对数收益标准差，取负） | 0.55 |
| `inv_beta` | `-β120`（对 000300.SH 的 120 交易日 Beta，取负） | 0.45 |

**两个因子都取负号**，使「低波动 → 高值」，与其余策略「越高越好」的方向一致。
⚠️ 方向搞反不报错、不会让任何「不抛异常」的测试变红，只会让系统**专挑高波动股**
——与策略意图正好相反。`test_low_volatility.py` 逐因子钉死方向。

## 无新数据源

σ 与 β 只用 `adj_prices` + `index_adj_prices`，两者都已在 `MarketSnapshot`
（后者是 MomentumStrategy 的 `rs_6m` 在用的那份）。σ 的实现与 C1-2 **共用**
`engine/volatility.py`——各算一遍必然漂移，而「两处 σ 定义不一致」在数字上看不出来。

## 【降级说明】β 窗口 120 而非 252

当前降级内容：Beta 窗口 120 交易日（教科书常用 252）。
原因：复用现有约 120 交易日的价格快照窗口，对数据层零改动、对生产内存零额外开销。
恢复条件：价格窗口扩至 ≥400 日历天并实测生产内存/延迟可接受后，
把 `LowVolatilityStrategyConfig.beta_window` 改 252 即可（已参数化）。

## 影子模式

本策略作为**一等 composite 成员**实现（进 `strategy_factors`、走五步管线、被 ICIR
监控），但权重从 0 起步，经 ICIR 验证后由月末 rebalance 自动激活（设计 §8.2）。
零权重策略不进正交化矩阵（C0 已修陷阱 1），故影子期对现有四策略**零回归**。
"""
from __future__ import annotations

import pandas as pd

from quantpilot.core.config_defaults import (
    DEFAULT_LOW_VOLATILITY_STRATEGY,
    LowVolatilityStrategyConfig,
)
from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot
from quantpilot.engine.volatility import rolling_beta, rolling_sigma


class LowVolatilityStrategy(BaseStrategy):
    """SDD §7.3：低波动 + 低 Beta。"""

    name = "low_volatility"
    display_name = "低波动"
    weights = {"inv_volatility": 0.55, "inv_beta": 0.45}

    def __init__(self, config: LowVolatilityStrategyConfig | None = None) -> None:
        self._cfg = config or DEFAULT_LOW_VOLATILITY_STRATEGY

    @property
    def required_history_days(self) -> int:
        """自报所需**交易日**深度 = `beta_window + 1`（算 n 日收益要 n+1 列）。

        ⚠️ 自报少了 → `ScoringService` 取的价格窗口不够 → 因子静默全 NaN。
        C1-3 记过这条：momentum 因窗口不足导致 `rs_6m` 有效率 0/2274，无任何告警。
        """
        return max(self._cfg.beta_window, self._cfg.volatility_window) + 1

    def compute_raw_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        cols = list(self.weights)
        adj_prices = market_data.get("adj_prices")
        if adj_prices is None or adj_prices.empty or len(universe) == 0:
            return pd.DataFrame(float("nan"), index=universe, columns=cols)

        px = adj_prices.reindex(universe)
        sigma = rolling_sigma(px, self._cfg.volatility_window)
        beta = rolling_beta(
            px,
            market_data.get("index_adj_prices", pd.DataFrame()),
            self._cfg.beta_window,
            benchmark=self._cfg.benchmark,
        )
        # 取负号：低波动 / 低 Beta → 高因子值
        return pd.DataFrame(
            {"inv_volatility": -sigma, "inv_beta": -beta}, index=universe
        )[cols]

    def _build_reason(
        self,
        ts_code: str,
        raw_row: pd.Series,
        final_score: float,
    ) -> str:
        sigma = raw_row.get("inv_volatility", float("nan"))
        beta = raw_row.get("inv_beta", float("nan"))
        parts: list[str] = []
        if pd.notna(sigma):
            # 展示时年化（×√252）并还原正号，便于用户理解
            parts.append(f"年化波动率 {abs(float(sigma)) * (252 ** 0.5):.1%}")
        if pd.notna(beta):
            parts.append(f"Beta {abs(float(beta)):.2f}")
        return "低波动：" + "，".join(parts) if parts else "低波动：数据不足"
