"""Phase 14 §14-4：IC 时序聚合 + 多场景 panel 纯函数。

供 `scripts/validate_ic_timeseries.py` + `scripts/compare_strategy_ic_panels.py`
直接调用。严格无 IO（CLAUDE.md §6 Engine 层约束）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

__all__ = [
    "ICRecord",
    "MonthlyAggregateRow",
    "PanelCellRow",
    "compute_t_stat",
    "aggregate_monthly",
    "build_panel",
]


@dataclass(frozen=True)
class ICRecord:
    """单点 IC 观测（来自 factor_ic_window_state 单点行）。"""

    trade_date: date
    ic_value: float
    sample_size: int


@dataclass(frozen=True)
class MonthlyAggregateRow:
    """月度聚合行（validate_ic_timeseries 输出）。"""

    year_month: str   # "YYYY-MM"
    ic_mean: float
    ic_std: float
    sample_size: int  # 该月观察日数 (N daily IC points)
    avg_xs_sample: float  # 月均横截面样本数（IC_daily.sample_size 求均值）
    t_stat: float | None  # ic_mean / (ic_std / sqrt(n))；n=1 或 std=0 时 None


@dataclass(frozen=True)
class PanelCellRow:
    """panel 单格（4 strategy × 3 state heatmap）。"""

    strategy: str
    state: str
    ic_mean: float
    ic_std: float
    n_months: int
    t_stat: float | None


def compute_t_stat(mean: float, std: float, n: int) -> float | None:
    """t-statistic = mean / (std / sqrt(n))。

    n ≤ 1 或 std ≤ 0 时返回 None（不可计算）。"""
    if n is None or n <= 1:
        return None
    if std is None or std <= 0.0 or not math.isfinite(std):
        return None
    se = std / math.sqrt(float(n))
    if se <= 0.0 or not math.isfinite(se):
        return None
    return float(mean) / se


def aggregate_monthly(records: list[ICRecord]) -> pd.DataFrame:
    """把若干日 ic_value 聚合到 year_month 月度。

    输入：``ICRecord`` 列表（trade_date / ic_value / sample_size）。
    输出：DataFrame，columns=[year_month, ic_mean, ic_std, sample_size,
    avg_xs_sample, t_stat]，按 year_month 升序；空输入返回空 DataFrame
    （columns 完整）。

    - 用样本标准差（ddof=1）；月内仅 1 个观察点时 ic_std=0、t_stat=None。
    - sample_size 列含义为「该月观察日数」（聚合行数），与 ICRecord.sample_size
      （单日横截面样本数）区分；后者均值落到 ``avg_xs_sample``。
    """
    cols = [
        "year_month", "ic_mean", "ic_std", "sample_size", "avg_xs_sample", "t_stat",
    ]
    if not records:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(
        [{"trade_date": r.trade_date, "ic_value": r.ic_value,
          "xs_sample": r.sample_size} for r in records]
    )
    df["year_month"] = df["trade_date"].apply(lambda d: f"{d.year:04d}-{d.month:02d}")

    out_rows: list[dict] = []
    for ym, group in df.groupby("year_month", sort=True):
        ics = group["ic_value"].astype(float)
        n = int(len(ics))
        mean = float(ics.mean())
        # ddof=1（样本标准差）；n=1 时 pandas 返回 NaN
        std_raw = float(ics.std(ddof=1)) if n > 1 else 0.0
        std = std_raw if (std_raw is not None and not math.isnan(std_raw)) else 0.0
        avg_xs = float(group["xs_sample"].astype(float).mean())
        out_rows.append({
            "year_month": str(ym),
            "ic_mean": mean,
            "ic_std": std,
            "sample_size": n,
            "avg_xs_sample": avg_xs,
            "t_stat": compute_t_stat(mean, std, n),
        })

    return pd.DataFrame(out_rows, columns=cols)


def build_panel(
    monthly_panels: dict[tuple[str, str], pd.DataFrame],
) -> pd.DataFrame:
    """把 ``(strategy, state) -> monthly aggregate DataFrame`` 折叠到 PanelCellRow。

    输入：dict 键 (strategy, state)，值为 ``aggregate_monthly`` 输出的 monthly DataFrame；
    输出：DataFrame columns=[strategy, state, ic_mean, ic_std, n_months, t_stat]，
    每个 (strategy, state) 一行——对该 (s, t) 全部月度的 ic_mean 求均值（n_months
    = 行数）+ ic_std 用月度 ic_mean 的样本 std + t_stat 用月度均值 / (月度 std / sqrt(n_months))。

    n_months ≤ 1 → ic_std=0、t_stat=None。
    """
    cols = ["strategy", "state", "ic_mean", "ic_std", "n_months", "t_stat"]
    if not monthly_panels:
        return pd.DataFrame(columns=cols)

    out_rows: list[dict] = []
    for (strategy, state), monthly_df in monthly_panels.items():
        if monthly_df.empty:
            out_rows.append({
                "strategy": strategy, "state": state,
                "ic_mean": 0.0, "ic_std": 0.0, "n_months": 0, "t_stat": None,
            })
            continue
        ics = monthly_df["ic_mean"].astype(float)
        n = int(len(ics))
        mean = float(ics.mean())
        std_raw = float(ics.std(ddof=1)) if n > 1 else 0.0
        std = std_raw if (std_raw is not None and not math.isnan(std_raw)) else 0.0
        out_rows.append({
            "strategy": strategy, "state": state,
            "ic_mean": mean, "ic_std": std, "n_months": n,
            "t_stat": compute_t_stat(mean, std, n),
        })

    return pd.DataFrame(out_rows, columns=cols)
