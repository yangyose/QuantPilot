"""MoneyFlowStrategy：资金动向（V1.5-C C4 / SDD §7.3）。

Engine 层纯函数，严格无 IO。

## 因子

| 因子 | 定义 | 权重 |
|------|------|------|
| `main_net_inflow_5d`  | Σ5（特大+大单买入 − 特大+大单卖出）/ Σ5 成交额 | 0.60 |
| `main_net_inflow_20d` | 同上，20 日窗口 | 0.40 |

两者都是「主力净流入占成交额之比」：除以成交额去掉市值量纲（大票小票可比），
方向「越高越好」与其余策略一致。**用「特大+大单」而不是 `net_mf_amount`**——后者
把小单/中单也算进去，与「主力」语义相悖（MF-STR-06 钉死）。

## v0.13 范围收窄

原设计第三个因子「北向持股变化」已砍：`hk_hold` A 股日频自 2024-08-19 停更、只剩季末，
替代源逐个排除（设计 §6.1）。权重按原 0.45/0.30 归一化为 0.60/0.40。

## 数据来源

`MarketSnapshot["money_flow"]`：long 格式 DataFrame，列 ts_code / trade_date /
net_mf_amount / buy_elg_amount / sell_elg_amount / buy_lg_amount / sell_lg_amount /
**amount**（当日成交额，元，由 repo 联 daily_quote 取）。金额全部为**元**。
未提供（回测引擎 / 冷启动 / 回填未完成）→ 全 NaN → `Scorer` 记
`scorer_strategy_skipped_all_nan` 并跳过，行为安全。

## 窗口纪律（§4.4）

按**行数**精确截取最近 N 个交易日，不足 N 行 → NaN，不用部分和冒充；
分母为 0（停牌）→ NaN 而非 inf（inf 进 Winsorize 会毁掉整列）。

## 影子模式

同 C3：一等 composite 成员（进 `strategy_factors`、走五步管线、被 ICIR 监控），
权重从 0 起步，经 ICIR 验证后由月末 rebalance 自动激活（设计 §8.2）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantpilot.core.config_defaults import (
    DEFAULT_MONEY_FLOW_STRATEGY,
    MoneyFlowStrategyConfig,
)
from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot

_REQUIRED_COLS = (
    "ts_code", "trade_date",
    "buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount", "amount",
)


def _window_ratio(flow: pd.DataFrame, window: int) -> pd.Series:
    """每股最近 `window` 行的 Σ主力净额 / Σ成交额；行数不足或分母 ≤ 0 → NaN。"""
    tail = flow.groupby("ts_code", sort=False).tail(window)
    agg = tail.groupby("ts_code").agg(
        n=("trade_date", "size"), net=("main_net", "sum"), amt=("amount", "sum"),
    )
    ratio = agg["net"] / agg["amt"].where(agg["amt"] > 0)
    ratio = ratio.where(agg["n"] >= window)
    return ratio.replace([np.inf, -np.inf], np.nan)


class MoneyFlowStrategy(BaseStrategy):
    """SDD §7.3：主力资金净流入。"""

    name = "money_flow"
    display_name = "资金动向"
    # ⚠️ 生产不读 weights（五步管线因子等权）——改这里选股不会变。见 BaseStrategy.weights
    weights = {"main_net_inflow_5d": 0.60, "main_net_inflow_20d": 0.40}

    def __init__(self, config: MoneyFlowStrategyConfig | None = None) -> None:
        self._cfg = config or DEFAULT_MONEY_FLOW_STRATEGY

    @property
    def required_history_days(self) -> int:
        """本策略不用 `adj_prices`；自报最小值 1，不抬高全体价格窗口。"""
        return 1

    def compute_raw_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        cols = list(self.weights)
        flow = market_data.get("money_flow")  # type: ignore[call-overload]
        if (
            flow is None or not isinstance(flow, pd.DataFrame) or flow.empty
            or len(universe) == 0 or any(c not in flow.columns for c in _REQUIRED_COLS)
        ):
            return pd.DataFrame(np.nan, index=universe, columns=cols)

        f = flow[flow["ts_code"].isin(universe)].copy()
        if f.empty:
            return pd.DataFrame(np.nan, index=universe, columns=cols)
        for c in ("buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount",
                  "amount"):
            f[c] = pd.to_numeric(f[c], errors="coerce").astype(float)
        f = f.sort_values(["ts_code", "trade_date"])
        f["main_net"] = (
            f["buy_elg_amount"].fillna(0.0) + f["buy_lg_amount"].fillna(0.0)
            - f["sell_elg_amount"].fillna(0.0) - f["sell_lg_amount"].fillna(0.0)
        )

        out = pd.DataFrame(index=universe, columns=cols, dtype=float)
        out["main_net_inflow_5d"] = _window_ratio(f, self._cfg.short_window).reindex(universe)
        out["main_net_inflow_20d"] = _window_ratio(f, self._cfg.long_window).reindex(universe)
        return out[cols]

    def _build_reason(
        self,
        ts_code: str,
        raw_row: pd.Series,
        final_score: float,
    ) -> str:
        parts: list[str] = []
        for label, key, win in (
            ("5 日", "main_net_inflow_5d", self._cfg.short_window),
            ("20 日", "main_net_inflow_20d", self._cfg.long_window),
        ):
            v = raw_row.get(key, float("nan"))
            if pd.notna(v):
                parts.append(f"近{label}主力净流入占成交额 {float(v):+.1%}")
        return "资金动向：" + "，".join(parts) if parts else "资金动向：数据不足"
