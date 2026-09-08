"""Piotroski F-Score：9 项二元基本面信号（V1.5-C C2 / SDD-EXT-04）。

Engine 层纯函数，严格无 IO；取数（含同比配对）由 Service 层负责。

## 9 项与字段映射

| # | 类别 | 信号 | 判定 |
|---|------|------|------|
| 1 | 盈利性 | ROA 为正 | `roa > 0` |
| 2 | 盈利性 | 经营现金流为正 | `ocfps > 0` |
| 3 | 盈利性 | ROA 同比改善 | `Δroa > 0` |
| 4 | 盈利性 | 应计质量 | `ocfps > eps` |
| 5 | 杠杆 | 负债率下降 | `Δdebt_to_asset < 0` |
| 6 | 流动性 | 流动比率上升 | `Δcurrent_ratio > 0` |
| 7 | 融资 | 未增发股本 | `total_share(t) <= total_share(t-1y) × (1+ε)` |
| 8 | 运营 | 毛利率上升 | `Δgrossprofit_margin > 0` |
| 9 | 运营 | 资产周转率上升 | `Δassets_turn > 0` |

`Δ` 一律指**同比上年同期**（report_period 同月日、年份 −1），非环比——
季报口径下环比有季节性偏误。

## 【降级说明】两处口径近似

1. **第 5 项用总资产负债率**，而非教科书的「长期负债/总资产」。
   当前降级内容：`debt_to_asset` 含流动负债；原因：Tushare `fina_indicator` 无稳定的
   长期负债率字段，且该列已在库（F-6 过滤在用）；恢复条件：接入
   `balancesheet.total_ncl` 后改精确口径。
2. **第 7 项的 `total_share` 取自 `daily_basic`**（交易日时点股本），
   而非报告期时点股本。原因：`balancesheet` 不支持逗号多码、全市场两期需约 11000 次
   调用；`daily_basic` 每日一次取回全市场且实测非空率 100%。
   ⚠️ 取快照须回退到 `publish_date` 当日或之前最近的**交易日**（`daily_basic`
   只在交易日有行）。恢复条件：如需精确口径，改接 `balancesheet` 并承担回填成本。

## ⚠️ 缺数据 ≠ 低分（C-4 的直接落地）

任一必需字段为 NaN → **该项记 NaN，不记 0**；缺 ≥ `_MAX_MISSING` 项 →
`f_score = NaN`（**不可判**）。

把缺失记 0 会让**数据缺口伪装成基本面恶化**，进而把股票错误地踢出均值回归——
正是 SDD-EXT-04 想避免的反面。不可判时怎么办由调用方决定（设计 §4.4：**不门控**，
并计数告警）——不可判不等于不合格。
"""
from __future__ import annotations

import pandas as pd

__all__ = ["F_SCORE_ITEMS", "SHARE_ISSUANCE_TOLERANCE", "compute_f_score"]

# 稳定顺序：明细矩阵的列序、lineage 与日志都依赖它，勿随意重排
F_SCORE_ITEMS: tuple[str, ...] = (
    "roa_positive",
    "cfo_positive",
    "roa_improved",
    "accrual_quality",
    "leverage_down",
    "liquidity_up",
    "no_dilution",
    "margin_up",
    "turnover_up",
)

# 第 7 项的容差：股本微增（≤ 0.1%）仍算「未增发」。
# 没有容差的话，四舍五入或极小的股权激励都会把这项打成 0。
SHARE_ISSUANCE_TOLERANCE: float = 0.001

# 缺这么多项即判为「不可判」。9 项里缺 3 项，剩下 6 项已无法支撑 `>= 6` 的门槛判定
# （即使全过也只到 6，与门槛相等，判断力为零）——这正是取 3 的理由。
_MAX_MISSING: int = 3


def _num(df: pd.DataFrame, col: str, idx: pd.Index) -> pd.Series:
    """按 idx 对齐取列；列不存在 → 全 NaN（缺列与缺值同等对待，都不记 0）。"""
    if col not in df.columns:
        return pd.Series(float("nan"), index=idx, dtype=float)
    return pd.to_numeric(df[col], errors="coerce").reindex(idx).astype(float)


def compute_f_score(
    current: pd.DataFrame,
    prior: pd.DataFrame,
) -> tuple[pd.Series, pd.DataFrame]:
    """算 F-Score 与逐项明细。

    Args:
        current: index=ts_code，当期财务；列见模块 docstring。
        prior:   index=ts_code，**上年同期**；缺席的股票视为同比项全缺。

    Returns:
        `(f_score, detail)`：
        - `f_score`：0~9 的浮点，或 NaN（不可判）
        - `detail`：index=ts_code、columns=`F_SCORE_ITEMS` 的 0/1/NaN 矩阵

    不修改入参（Engine 层纯函数；`ScoringService` 会跨调用复用策略实例）。
    """
    idx = current.index
    if len(idx) == 0:
        return (
            pd.Series(dtype=float, index=idx),
            pd.DataFrame(columns=list(F_SCORE_ITEMS), index=idx, dtype=float),
        )

    c = {f: _num(current, f, idx) for f in
         ("roa", "ocfps", "eps", "current_ratio", "grossprofit_margin",
          "assets_turn", "total_share", "debt_to_asset")}
    p = {f: _num(prior, f, idx) for f in
         ("roa", "current_ratio", "grossprofit_margin", "assets_turn",
          "total_share", "debt_to_asset")}

    def _bin(cond: pd.Series, *needed: pd.Series) -> pd.Series:
        """条件转 0/1；任一必需输入为 NaN → NaN（**不是 0**）。"""
        out = cond.astype(float)
        missing = needed[0].isna()
        for s in needed[1:]:
            missing = missing | s.isna()
        return out.mask(missing)

    detail = pd.DataFrame(index=idx, dtype=float)
    detail["roa_positive"] = _bin(c["roa"] > 0, c["roa"])
    detail["cfo_positive"] = _bin(c["ocfps"] > 0, c["ocfps"])
    detail["roa_improved"] = _bin(c["roa"] > p["roa"], c["roa"], p["roa"])
    detail["accrual_quality"] = _bin(c["ocfps"] > c["eps"], c["ocfps"], c["eps"])
    detail["leverage_down"] = _bin(
        c["debt_to_asset"] < p["debt_to_asset"], c["debt_to_asset"], p["debt_to_asset"]
    )
    detail["liquidity_up"] = _bin(
        c["current_ratio"] > p["current_ratio"], c["current_ratio"], p["current_ratio"]
    )
    detail["no_dilution"] = _bin(
        c["total_share"] <= p["total_share"] * (1.0 + SHARE_ISSUANCE_TOLERANCE),
        c["total_share"], p["total_share"],
    )
    detail["margin_up"] = _bin(
        c["grossprofit_margin"] > p["grossprofit_margin"],
        c["grossprofit_margin"], p["grossprofit_margin"],
    )
    detail["turnover_up"] = _bin(
        c["assets_turn"] > p["assets_turn"], c["assets_turn"], p["assets_turn"]
    )
    detail = detail[list(F_SCORE_ITEMS)]

    n_missing = detail.isna().sum(axis=1)
    score = detail.sum(axis=1, skipna=True)
    # ⚠️ 缺 ≥ _MAX_MISSING 项 → NaN（不可判），**不是低分**
    return score.mask(n_missing >= _MAX_MISSING), detail
