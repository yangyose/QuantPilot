"""面板统计量的组装与扇出（K-6 调用点的纯计算部分）。V1.5-K。

Engine 层纯函数，严格无 IO。落库由 `MarketDataRepository.upsert_factor_panel_stat_bulk`
承担，批次维度（`panel_run` / `trade_date`）由写库方统一盖章。

## 做什么

把一天的评分产出（`list[CompositeScore]`，需 `collect_factor_panel=True` 才带
`factor_raw` / `factor_z`）重建成 `{strategy: {factor: Series}}`，再按
stage × horizon × metric 扇出到 K-2~K-6 各计算函数。

## ⚠️ 哪些指标该跟 horizon 循环——最容易错的一条

| metric | 有前向概念？ | 产出 |
|---|---|---|
| `ic` / `decile_fwd_return` / `top5_excess` | 有 | 每个 horizon 各一组 |
| `valid_ratio` / `turnover_jaccard` / `cost_drag` | **无** | **整天各一条**（`horizon=0`）|

把无前向概念的指标塞进 horizon 循环，会产出 4 条**只有 horizon 不同、值完全相同**
的重复行。它们**能通过九元组唯一键**（horizon 正是维度之一），所以数据库拦不住；
后果是行数膨胀 4 倍，且分析侧按 horizon 分组时同一个换手率被重复计入四次。
`tests/unit/test_factor_panel_assembly.py::TestHorizonIndependentMetricsEmittedOnce`
钉死这条，另有一条用例断言整批产出内无九元组重复。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from quantpilot.engine.diagnostics.factor_cost import CostParams, compute_cost_drag
from quantpilot.engine.diagnostics.factor_ic import (
    PanelStatPoint,
    compute_factor_ic,
    compute_factor_valid_ratio,
)
from quantpilot.engine.diagnostics.factor_portfolio import (
    compute_decile_forward_return,
    compute_top_pct_excess,
)
from quantpilot.engine.diagnostics.factor_turnover import compute_turnover_jaccard

if TYPE_CHECKING:
    from quantpilot.engine.diagnostics.multi_horizon import HorizonForward
    from quantpilot.engine.scorer import CompositeScore

__all__ = ["STAGES", "build_factor_matrices", "compute_panel_stats_for_date"]

STAGES: tuple[str, str] = ("raw", "z")
_STAGE_ATTR = {"raw": "factor_raw", "z": "factor_z"}


def build_factor_matrices(
    composites: list[CompositeScore], *, stage: str
) -> dict[str, dict[str, pd.Series]]:
    """从 `CompositeScore` 列表重建 ``{strategy: {factor: Series(index=ts_code)}}``。

    Args:
        composites: 一天的评分产出。
        stage: ``'raw'`` 或 ``'z'``——分别读 `factor_raw` / `factor_z`。

    Returns:
        因子矩阵；`collect_factor_panel` 未开启（两字段为 None）时返回 **{}**。

    Raises:
        ValueError: stage 不在 `STAGES` 内（防手滑传 'winsorized' 这类中间量名）。
    """
    if stage not in _STAGE_ATTR:
        raise ValueError(f"stage 必须是 {STAGES} 之一，实得 {stage!r}")
    attr = _STAGE_ATTR[stage]

    acc: dict[str, dict[str, dict[str, float]]] = {}
    for c in composites:
        per_strategy = getattr(c, attr, None)
        if not per_strategy:
            continue
        for s_name, factors in per_strategy.items():
            for f_name, v in factors.items():
                acc.setdefault(str(s_name), {}).setdefault(str(f_name), {})[
                    str(c.ts_code)
                ] = float(v)

    return {
        s: {f: pd.Series(vals, dtype=float) for f, vals in factors.items()}
        for s, factors in acc.items()
    }


def compute_panel_stats_for_date(
    composites: list[CompositeScore],
    forwards: list[HorizonForward],
    *,
    state: str,
    universe_size: int,
    prev_composites: list[CompositeScore] | None = None,
    cost: CostParams | None = None,
) -> list[PanelStatPoint]:
    """算一天的全部面板统计量（K-2~K-6），返回可直接落库的点列表。

    Args:
        composites: 当日评分产出（须带 `factor_raw` / `factor_z`）。
        forwards: `resolve_forward_returns` 的可用部分，逐 horizon。
        state: 当日市场状态。
        universe_size: `valid_ratio` 的分母——**必须是 universe 大小**，
            不是 Series 长度（后者会让有效率恒为 1.0，指标等于不存在）。
        prev_composites: 前一交易日产出；缺省则不产换手/成本（首日即如此）。
        cost: 成本参数，缺省取 `config_defaults`。

    Returns:
        `PanelStatPoint` 列表。`panel_run` / `trade_date` 由写库方盖章。
    """
    out: list[PanelStatPoint] = []

    for stage in STAGES:
        matrices = build_factor_matrices(composites, stage=stage)
        if not matrices:
            continue

        # ── 有前向概念：逐 horizon ────────────────────────────────────────────
        for fwd in forwards:
            out.extend(compute_factor_ic(
                matrices, fwd.returns, stage=stage, horizon=fwd.horizon, state=state,
            ))
            out.extend(compute_decile_forward_return(
                matrices, fwd.returns, stage=stage, horizon=fwd.horizon, state=state,
            ))
            out.extend(compute_top_pct_excess(
                matrices, fwd.returns, stage=stage, horizon=fwd.horizon, state=state,
            ))

        # ── 无前向概念：整天一条（horizon=0），**不得进上面的循环** ──────────
        out.extend(compute_factor_valid_ratio(
            matrices, universe_size, stage=stage, state=state,
        ))

        if prev_composites:
            prev_matrices = build_factor_matrices(prev_composites, stage=stage)
            if prev_matrices:
                out.extend(compute_turnover_jaccard(
                    prev_matrices, matrices, stage=stage, state=state,
                ))
                out.extend(compute_cost_drag(
                    prev_matrices, matrices, stage=stage, state=state, cost=cost,
                ))
    return out
