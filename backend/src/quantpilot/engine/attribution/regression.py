"""多因子回归归因（OLS 收益拆解）。Engine 层严格无 IO。

设计依据：phase12_factor_lineage.md §3.2.1。

V1.0 简化：归因因子 = 4 策略（trend / momentum / mean_reversion / value）的
strategy_z，不是 SDD §12.3 原描述的"风险因子归因"（Size/Value/Momentum/Beta 暴露）。
完整 4 风险因子归因留 V1.5+ 扩展 strategy_factors → 真因子映射后实施。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttributionResult:
    """单次 OLS 归因结果。

    coefficients: 因子收益 β（每单位 z 暴露对应的收益）
    t_stats: 因子收益 t 统计量（系数 / 标准误，|t| > 2 视为显著）
    residual_std: 残差标准差
    r_squared: 模型解释力（横截面 OLS 经验区间 [0.005, 0.15]）
    sample_size: 有效观测数（drop NaN 后）
    """
    coefficients: dict[str, float]
    t_stats: dict[str, float]
    residual_std: float
    r_squared: float
    sample_size: int


def run_ols(
    exposures: pd.DataFrame,
    returns: pd.Series,
    factors: list[str] | None = None,
) -> AttributionResult | None:
    """跑横截面 / panel OLS 多因子归因。

    Args:
        exposures: index 可为 ts_code（单日横截面）或 (date, ts_code)（panel），
                   columns 为因子名（与 factors 对齐）；值为标准化后因子暴露 z。
        returns: index 与 exposures 对齐；值为前向收益（已对齐窗口）。
        factors: 显式指定因子列；默认用 exposures.columns。

    Returns:
        AttributionResult；样本不足 / 矩阵奇异 → None（不抛异常，调用方决定降级）。
    """
    if factors is None:
        factors = list(exposures.columns)

    df = exposures[factors].copy()
    df["__y__"] = returns
    df = df.dropna()

    min_samples = 10 * len(factors)
    if len(df) < min_samples:
        logger.info(
            "attribution_ols_sample_too_small: have=%d need=%d factors=%s",
            len(df), min_samples, factors,
        )
        return None

    x_matrix = sm.add_constant(df[factors].to_numpy(dtype=float))
    y_vector = df["__y__"].to_numpy(dtype=float)

    # 设计矩阵秩检查：低于列数 → singular，sm.OLS 会跑通但系数无意义
    if np.linalg.matrix_rank(x_matrix) < x_matrix.shape[1]:
        logger.warning(
            "attribution_ols_singular_design: rank<%d shape=%s factors=%s",
            x_matrix.shape[1], x_matrix.shape, factors,
        )
        return None

    try:
        model = sm.OLS(y_vector, x_matrix).fit()
    except np.linalg.LinAlgError:
        logger.exception("attribution_ols_linalg_error: factors=%s", factors)
        return None

    # 跳过 const（params[0]）
    coeffs = dict(zip(factors, model.params[1:].tolist(), strict=True))
    t_stats = dict(zip(factors, model.tvalues[1:].tolist(), strict=True))

    return AttributionResult(
        coefficients=coeffs,
        t_stats=t_stats,
        residual_std=float(np.std(model.resid)),
        r_squared=float(model.rsquared),
        sample_size=len(df),
    )
