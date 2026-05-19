"""Orthogonalizer：5 步评分管线的 Step 4a + 4b 纯函数封装（Phase 11）。

依据 SDD v1.4 §7.1 + docs/design/phases/phase11_scoring_industrialization.md
v1.2 §3.2。Engine 层，无 IO。

- Step 4a Gram-Schmidt 残差化：按策略级 ICIR 排序，逐策略剔除前序投影
- Step 4b 残差再标准化：业界 Barra 流程标准做法，使 ``u_i ~ N(0, 1)`` 成立
  （否则 ``Var(u_i) = 1 - Σ ρ² < 1``，§7.6 综合评分方差归一化公式假设失败）

Hysteresis（防月度排序跳跃）由调用方在传 ``order`` 参数时实现（见 §4.3
HysteresisStateMachine）；Orthogonalizer 自身只接受最终的 ``order`` 列表。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_ORTH_SUFFIX = "_orthogonal"
_NORM_SUFFIX = "_normalized"


@dataclass(frozen=True)
class OrthogonalizationConfig:
    """Phase 11 §3.2 配置（Q10 Hysteresis 默认开）。"""

    enable_hysteresis: bool = True       # 仅记录，实际由调用方传 order 时实现
    rebalance_freq: str = "monthly"      # 仅记录，实际由 MonthlyScheduler 驱动
    # v1.3 修订：共线退化检测阈值——残差列 std 相对原列 std 低于此比例视为
    # "信息已被前序投影吸收"（R² > 1 - threshold²，默认 0.3 ↔ R² > 91%）。
    # 不剔除会让 renormalize 用极小 std 除 → outlier 被放大几百倍 → 顶分 z=15+。
    collinear_residual_ratio: float = 0.3


DEFAULT_ORTHOGONALIZER = OrthogonalizationConfig()


class Orthogonalizer:
    """5 步评分管线 Step 4a + 4b 纯函数实现。

    单元测试覆盖：4 维退化 / 完全共线检测 / renormalize Var≈1 / order 改变结果
    （详见 tests/unit/test_orthogonalizer.py）。
    """

    def __init__(self, cfg: OrthogonalizationConfig = DEFAULT_ORTHOGONALIZER) -> None:
        self._cfg = cfg

    # ============================================================
    # Step 4a：Gram-Schmidt 残差化
    # ============================================================
    def gram_schmidt(
        self,
        strategy_z_matrix: pd.DataFrame,
        order: list[str],
    ) -> pd.DataFrame:
        """逐策略剔除前序投影。

        - 输入 ``strategy_z_matrix``：``index=ts_code``，``cols=策略名``，
          ``values=strategy_z``（已经过 §3.1 5 步管线 Step 1~3）
        - ``order``：正交化顺序（按 ICIR 高→低，由 ScoringService.get_active_weights 提供）
        - 输出 ``index=ts_code``，``cols=[s + '_orthogonal' for s in order]``
        - **完全共线检测**：若某列残差 ``std < 1e-12`` → 输出该列为 NaN，写
          ``weights_source='collinear_skipped'`` 由调用方记录
        - **NaN 处理**：参与计算的行必须所有 ``order`` 列 non-NaN；其它行输出 NaN
        - **单行或全 NaN**：退化为输入矩阵（重命名列）
        """
        # 校验 order 全部在 columns 中
        missing = [s for s in order if s not in strategy_z_matrix.columns]
        if missing:
            raise ValueError(f"order contains strategies not in matrix: {missing}")

        if not order:
            return pd.DataFrame(index=strategy_z_matrix.index)

        # 仅保留 order 中的列
        matrix = strategy_z_matrix[order]
        out_cols = [s + _ORTH_SUFFIX for s in order]

        # 只对所有 order 列同时 non-NaN 的行参与投影计算
        valid_mask = matrix.notna().all(axis=1)
        valid_count = int(valid_mask.sum())

        if valid_count < 2:
            # 退化：单行或全 NaN → 列名重命名后直接返回（不投影）
            out = matrix.copy()
            out.columns = out_cols
            return out

        valid = matrix.loc[valid_mask].copy()
        residuals: dict[str, pd.Series] = {}

        for s in order:
            u_i = valid[s].copy()
            for prev_col in [s2 + _ORTH_SUFFIX for s2 in order]:
                if prev_col not in residuals:
                    continue
                u_j = residuals[prev_col]
                denom = float((u_j * u_j).sum())
                if denom < 1e-12:
                    # 前序残差全 0（极端共线），跳过该投影
                    continue
                coef = float((u_i * u_j).sum()) / denom
                u_i = u_i - coef * u_j

            col = s + _ORTH_SUFFIX
            # 完全共线检测：std≈0 → 该列写 NaN（下游 Scorer 权重置 0）
            if float(u_i.std(ddof=0)) < 1e-12:
                residuals[col] = pd.Series(np.nan, index=valid.index)
            else:
                residuals[col] = u_i

        residual_df = pd.DataFrame(residuals, index=valid.index)[out_cols]
        # 按原 index reindex 回填 NaN（未参与计算的行）
        return residual_df.reindex(strategy_z_matrix.index)

    # ============================================================
    # Step 4b：残差再标准化
    # ============================================================
    def renormalize(self, residual_df: pd.DataFrame) -> pd.DataFrame:
        """对每个残差列重新做 z-score，使 ``u_i_normalized ~ N(0, 1)``。

        - 输入列名形如 ``trend_orthogonal``，输出列名替换后缀为
          ``trend_normalized``
        - ``std == 0`` 或全 NaN 列 → 输出全 NaN（不写 0 避免下游误判"已标准化"）
        """
        out_cols: dict[str, pd.Series] = {}
        for col in residual_df.columns:
            new_col = col.removesuffix(_ORTH_SUFFIX) + _NORM_SUFFIX
            series = residual_df[col]
            std = float(series.std(skipna=True)) if series.notna().any() else 0.0
            if std < 1e-12 or pd.isna(std) or series.isna().all():
                out_cols[new_col] = pd.Series(np.nan, index=series.index)
            else:
                mean = float(series.mean(skipna=True))
                out_cols[new_col] = (series - mean) / std
        return pd.DataFrame(out_cols, index=residual_df.index)

    # ============================================================
    # 组合：Step 4a + 4b
    # ============================================================
    def compute(
        self,
        strategy_z_matrix: pd.DataFrame,
        order: list[str],
    ) -> pd.DataFrame:
        """完整 Step 4a + 4b：Gram-Schmidt 残差化 + 残差再标准化 + 共线退化检测。

        返回 ``cols=[s + '_normalized' for s in order]``，保证非 NaN 列
        ``Var ≈ 1 / mean ≈ 0``——这是 §7.6 综合评分方差归一化公式的数学前提。

        v1.3 修订：增加共线退化检测——若某残差列 std 相对原列 std 比例 <
        ``collinear_residual_ratio``（默认 0.3，对应 R² > 91%），认为该策略信息
        已被前序策略吸收 → 该列直接输出全 NaN（下游 Scorer 视为"该策略不贡献"），
        避免 renormalize 用极小 std 除把残差里的 outlier 放大数百倍（5y 真机
        2026-05-12 抓到顶分 composite_z=15.5，原因正是 momentum 残差 std≈0.004，
        renormalize 把 z_raw=-0.089 放大到 z_normalized=23.7）。
        """
        # 原 strategy_z 列 std（用于共线退化判断的分母）
        original_std: dict[str, float] = {}
        for s in order:
            if s in strategy_z_matrix.columns:
                col_std = float(strategy_z_matrix[s].std(skipna=True))
                original_std[s] = col_std if pd.notna(col_std) else 0.0

        orthogonal = self.gram_schmidt(strategy_z_matrix, order)

        out_cols: dict[str, pd.Series] = {}
        ratio_threshold = self._cfg.collinear_residual_ratio
        for s in order:
            orth_col = s + _ORTH_SUFFIX
            new_col = s + _NORM_SUFFIX
            if orth_col not in orthogonal.columns:
                out_cols[new_col] = pd.Series(np.nan, index=strategy_z_matrix.index)
                continue
            series = orthogonal[orth_col]
            std = float(series.std(skipna=True)) if series.notna().any() else 0.0
            orig = original_std.get(s, 0.0)
            # (a) 绝对接近 0：完全共线 → NaN
            # (b) 相对接近 0（残差 std / 原列 std < threshold）：共线退化 → NaN
            if std < 1e-12 or pd.isna(std) or series.isna().all():
                out_cols[new_col] = pd.Series(np.nan, index=series.index)
            elif orig > 1e-12 and std / orig < ratio_threshold:
                out_cols[new_col] = pd.Series(np.nan, index=series.index)
            else:
                mean = float(series.mean(skipna=True))
                out_cols[new_col] = (series - mean) / std
        return pd.DataFrame(out_cols, index=strategy_z_matrix.index)
