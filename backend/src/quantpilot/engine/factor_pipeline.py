"""FactorPipeline：5 步评分管线的 Step 1~3 纯函数封装（Phase 11）。

依据 SDD v1.4 §7.1 + docs/design/phases/phase11_scoring_industrialization.md
v1.2 §3.1。Engine 层，无 IO。

- Step 1 Winsorize：横截面百分位 1%/99% 截断（A 股因子偏态多，百分位优于 MAD）
- Step 2 中性化：横截面 OLS 回归取残差，行业强制开 / 市值默认开 / Beta 默认关
- Step 3 Z-score：保留尾部信号绝对幅度，跨期有限可比

NaN 处理原则：
- 输入 NaN 透传输出 NaN
- industry 缺失的 ts_code → Step 2 输出 NaN
- market_cap 缺失但 industry 存在 → 不参与 OLS，输出 NaN
- 回归奇异（自由度不够 / 行业全集中）→ 残差 = 原值（不做中性化，写降级审计）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FactorPipelineConfig:
    """SDD §7.1 Step 1~3 配置（Q1 Winsorize 百分位 + Q2 中性化分层默认）。"""

    winsorize_lower_pct: float = 0.01
    winsorize_upper_pct: float = 0.99
    neutralize_industry: bool = True       # SDD §7.1 Step 2 强制开
    neutralize_market_cap: bool = True     # Q2 锁定默认开（避免被动押注市值风格）
    neutralize_beta: bool = False          # Q2 锁定默认关（保留 Beta alpha）


DEFAULT_FACTOR_PIPELINE = FactorPipelineConfig()


class FactorPipeline:
    """5 步评分管线 Step 1~3 纯函数实现。

    单元测试覆盖：百分位截断 / OLS 残差 / Z-score / NaN 透传 / 回归奇异降级 /
    单只票 corner（详见 tests/unit/test_factor_pipeline.py）。
    """

    def __init__(self, cfg: FactorPipelineConfig = DEFAULT_FACTOR_PIPELINE) -> None:
        self._cfg = cfg

    # ============================================================
    # Step 1：Winsorize
    # ============================================================
    def winsorize(self, values: pd.Series) -> pd.Series:
        """横截面 percentile_1%/99% 截断。NaN 保持 NaN。

        - 全 NaN 输入 → 全 NaN 输出
        - 全相同值 → 不变（无截断）
        - 单值有效（其它 NaN）→ 不变（无截断）
        """
        if values.empty or values.isna().all():
            return values.copy()

        lo = float(np.nanpercentile(values.values, self._cfg.winsorize_lower_pct * 100))
        hi = float(np.nanpercentile(values.values, self._cfg.winsorize_upper_pct * 100))
        clipped = values.clip(lower=lo, upper=hi)
        # 保持 NaN 透传（pd.Series.clip 已天然支持，不必额外处理）
        return clipped

    # ============================================================
    # Step 2：横截面中性化（OLS 残差）
    # ============================================================
    def neutralize(
        self,
        values: pd.Series,
        industry: dict[str, str],
        market_cap: pd.Series | None = None,
        beta: pd.Series | None = None,
    ) -> pd.Series:
        """横截面回归 ``values ~ industry_dummies [+ log(market_cap)] [+ beta]``，
        返回残差。

        - 行业 dummy 用 ``pd.get_dummies(drop_first=True)`` 避免共线
        - 市值用 ``np.log(total_mv)``（业界惯例）
        - industry 缺失的 ts_code → 输出 NaN（不参与 OLS）
        - market_cap / beta 缺失且对应开关开 → 该行输出 NaN
        - 回归奇异（自由度不足，``n_obs ≤ n_features``）→ **降级**为残差=原值
          且不抛异常（业务上等价于"未做中性化"，下游 Z-score 仍能跑）
        """
        out = pd.Series(index=values.index, dtype=float, name=values.name)

        # industry 映射；不在 industry dict 内的 ts_code → 输出 NaN
        industry_series = pd.Series(industry, dtype="object")
        df = pd.DataFrame({"y": values})
        df["_industry"] = industry_series.reindex(df.index)

        if not self._cfg.neutralize_industry:
            # 强制开关闭时回 Z-score 前一步：直接返回 winsorize 后值
            # （Phase 11 V1.0 锁定 neutralize_industry=True，此分支仅用于研究模式）
            return values.copy()

        df = df[df["_industry"].notna()]
        if df.empty:
            return out

        # 构造设计矩阵 X
        x_parts: list[pd.DataFrame | pd.Series] = []
        dummies = pd.get_dummies(df["_industry"], prefix="ind", drop_first=True, dtype=float)
        x_parts.append(dummies)

        if self._cfg.neutralize_market_cap:
            if market_cap is None:
                # 市值开关开但没传 → 该行视为缺失，全部输出 NaN
                return out
            mv = market_cap.reindex(df.index).astype(float)
            mv_positive = mv.where(mv > 0)
            log_mv = np.log(mv_positive).rename("log_mv")
            x_parts.append(log_mv)

        if self._cfg.neutralize_beta:
            if beta is None:
                return out
            b = beta.reindex(df.index).astype(float).rename("beta")
            x_parts.append(b)

        x_parts.insert(0, pd.Series(1.0, index=df.index, name="const"))
        X = pd.concat(x_parts, axis=1)

        # 合并 y + X，丢含 NaN 行
        combined = pd.concat([df["y"].rename("y"), X], axis=1).dropna()
        if combined.shape[0] <= combined.shape[1]:
            # 【降级说明】自由度不足（n_obs <= n_features）—— 典型场景：单只票
            # 通过过滤，或所有票同行业（行业 dummy 列接近列满秩）。OLS 退化无
            # 唯一解，降级为残差=原值（等价于"未做中性化"），下游 Z-score 仍
            # 能跑。仅对 industry 存在的行写原值；industry 缺失行保持 NaN。
            # 恢复条件：universe 扩张到 industry 多样的子集 + market_cap 覆盖
            # 率回升。Phase 13 可观测性接入后由 WARN 级日志触发告警；当前
            # 5y 真机 4 trade_date × 3 state 未触发本路径。
            logger.warning(
                "factor_pipeline.neutralize_degraded_dof: n_obs=%d <= n_features=%d "
                "(industry over-concentrated or universe too small after filter); "
                "falling back to winsorized values for %s",
                combined.shape[0], combined.shape[1], values.name,
            )
            out.loc[df.index] = values.loc[df.index]
            return out

        y_arr = combined["y"].to_numpy(dtype=float)
        x_mat = combined.drop(columns=["y"]).to_numpy(dtype=float)

        try:
            beta_hat, *_ = np.linalg.lstsq(x_mat, y_arr, rcond=None)
        except np.linalg.LinAlgError:
            # 【降级说明】lstsq SVD 失败（设计矩阵奇异，如 log_mv 全相等导致
            # 共线、或某行业 dummy 与 const 完全共线）。降级为残差=原值；
            # 恢复条件：market_cap 范围回归正常 / industry 多样性恢复。
            logger.exception(
                "factor_pipeline.neutralize_lstsq_failed: returning raw values for %s",
                values.name,
            )
            return values.copy()

        y_hat = x_mat @ beta_hat
        residuals = pd.Series(y_arr - y_hat, index=combined.index, name=values.name)
        out.loc[residuals.index] = residuals
        return out

    # ============================================================
    # Step 3：Z-score 标准化
    # ============================================================
    def zscore(self, values: pd.Series) -> pd.Series:
        """``z = (x - mean) / std``。

        - mean / std 计算时跳过 NaN
        - ``std == 0`` 或全 NaN → 返回全 0（避免下游除零）
        - 输入 NaN → 输出 NaN
        """
        if values.empty or values.isna().all():
            return pd.Series(0.0, index=values.index, name=values.name)
        mean = values.mean(skipna=True)
        std = values.std(skipna=True)
        if std == 0 or pd.isna(std):
            return pd.Series(0.0, index=values.index, name=values.name)
        return (values - mean) / std

    # ============================================================
    # 组合：Step 1~3 完整管线
    # ============================================================
    def run_steps_1_to_3(
        self,
        raw_factor: pd.Series,
        industry: dict[str, str],
        market_cap: pd.Series | None = None,
        beta: pd.Series | None = None,
    ) -> pd.Series:
        """组合：Winsorize → Neutralize → Zscore。"""
        winsorized = self.winsorize(raw_factor)
        neutralized = self.neutralize(winsorized, industry, market_cap, beta)
        standardized = self.zscore(neutralized)
        return standardized
