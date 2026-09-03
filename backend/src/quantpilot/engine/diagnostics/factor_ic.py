"""因子级 IC 与有效率。V1.5-K K-6。Engine 层纯函数，严格无 IO。

## 缺口来源

`compute_daily_ic` 只算**策略级** Spearman IC——`factor_ic_window_state.factor`
这一列存的其实是策略名，因子维度一直空着。于是**因子级指标在生产没有任何落点**：
2026-08-31 C1 上线后想验证 C1-3（价格窗口按交易日推导）是否生效，发现
`rs_6m` 的有效率**痕迹根本不存在**——不是查不到，是从来没记过。

⚠️ 注意归因：那个缺口**不是**「零权重策略没有痕迹」（那一层 C0 已用
`strategy_z_all` 补上）。真正的缺口是**粒度**——`score_breakdown_raw` 与
`strategy_z_all` **都只到策略层**，无论权重是否为 0 都记不到 `rs_6m` 这一级。

## 两个 stage 的意义

- `raw`：因子**固有**预测力，未经五步管线加工
- `z`：**系统实际使用的那一版**（进 composite 的就是它）

**两者之差 = 五步管线（Winsorize + 行业/市值/Beta 中性化 + 正交化）到底提升
还是损耗了预测力**——这套管线自 Phase 11 上线至今从未被量过。

## ⚠️ `ic` 与 `valid_ratio` 的退化行为**故意不同**

- `ic` —— 与 `compute_daily_ic` 同规则：样本不足 / 全 NaN / 横截面无方差
  → **跳过，不写占位行**。
- `valid_ratio` —— **永远写，包括 0.0**。

设计文档 §3 门槛 3 原写「新指标须与 `compute_daily_ic` 一致」，此处有意偏离：
一个因子全 NaN 时「跳过」等于让它从数据里彻底消失，**而那正是要检测的东西**
（C1-3 的 `rs_6m` 0/2274，痕迹为零本身即缺陷本体）。若 valid_ratio 也跟着消失，
K-6 就退化成一个只会报告「一切正常」的指标。§3 那条的原意是防「同一天不同
metric 有的有行有的没行」，本偏离把「没行」换成「有行且值为 0」，更易发现问题。
已回写设计文档 §3。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from quantpilot.engine.diagnostics.ic_aggregator import _DAILY_IC_MIN_XS
from quantpilot.engine.factor_monitor import FactorMonitorEngine

__all__ = ["PanelStatPoint", "compute_factor_ic", "compute_factor_valid_ratio"]

PORTFOLIO_FACTOR = "__portfolio__"
_VALID_RATIO_HORIZON = 0          # 有效率无前向概念（system_design §4.2 约定）


@dataclass(frozen=True)
class PanelStatPoint:
    """`factor_panel_stat` 的一行（去掉批次维度）。

    字段刻意与表列一一对应——少一个就得在写库处现编默认值，那是口径漂移的起点。
    `panel_run` / `trade_date` 由写库方按批次统一填，不属单点计算。
    """

    strategy: str
    factor: str
    stage: str
    state: str
    horizon: int
    metric: str
    value: float | None
    sample_size: int
    bucket: int = -1


def compute_factor_ic(
    factor_values: dict[str, dict[str, pd.Series]],
    forward_returns: pd.Series,
    *,
    stage: str,
    horizon: int,
    state: str = "ALL",
    min_xs: int = _DAILY_IC_MIN_XS,
) -> list[PanelStatPoint]:
    """逐 (strategy, factor) 算单日 Spearman Rank IC。

    Args:
        factor_values: ``{strategy: {factor: Series(index=ts_code)}}``。
        forward_returns: index=ts_code 的前向收益。
        stage: ``'raw'`` 或 ``'z'``——**两者之差即五步管线的增益/损耗**。
        horizon: 前向交易日数（5/10/20/40）。
        state: 市场状态；调用方按当日状态传入。
        min_xs: 对齐后有效数下限，低于则跳过（避免噪声 IC 污染面板）。

    Returns:
        `metric='ic'` 的点列表。退化者**不产出行**（同 `compute_daily_ic`）。
    """
    engine = FactorMonitorEngine()
    out: list[PanelStatPoint] = []
    for strategy, factors in factor_values.items():
        for factor, series in factors.items():
            combined = pd.DataFrame({"f": series, "r": forward_returns}).dropna()
            n = int(len(combined))
            if n < min_xs:
                continue
            ic = engine.calc_ic(combined["f"], combined["r"])
            if ic is None or math.isnan(ic):
                continue
            out.append(PanelStatPoint(
                strategy=str(strategy), factor=str(factor), stage=stage,
                state=state, horizon=int(horizon), metric="ic",
                value=float(ic), sample_size=n,
            ))
    return out


def compute_factor_valid_ratio(
    factor_values: dict[str, dict[str, pd.Series]],
    universe_size: int,
    *,
    stage: str,
    state: str = "ALL",
) -> list[PanelStatPoint]:
    """逐 (strategy, factor) 算有效（非 NaN）率 = 有效值数 / universe 大小。

    ⚠️ **分母必须是 universe 大小，不能用 Series 长度**：若上游只把有效值放进
    Series，用长度当分母会让有效率恒为 1.0——指标永远报正常，等价于没有这个指标。

    ⚠️ **永远产出行，包括 0.0**（与 `compute_factor_ic` 相反，理由见模块 docstring）：
    全 NaN 因子若被跳过就彻底消失，而那正是要检测的东西。

    Returns:
        `metric='valid_ratio'`、`horizon=0` 的点列表；`universe_size <= 0`
        时 value 为 NaN（无法计算，不是 0——0 意味着「算出来就是没有效值」）。
    """
    out: list[PanelStatPoint] = []
    for strategy, factors in factor_values.items():
        for factor, series in factors.items():
            if universe_size > 0:
                valid = int(pd.Series(series).notna().sum())
                value: float | None = valid / universe_size
            else:
                value = float("nan")
            out.append(PanelStatPoint(
                strategy=str(strategy), factor=str(factor), stage=stage,
                state=state, horizon=_VALID_RATIO_HORIZON, metric="valid_ratio",
                value=value, sample_size=int(universe_size),
            ))
    return out
