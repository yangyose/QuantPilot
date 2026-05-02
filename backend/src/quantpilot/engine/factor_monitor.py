"""FactorMonitorEngine：因子质量监控纯函数（Phase 7）。

无 IO，月末由 FactorMonitorService 编排调用。
提供 IC/IR/半衰期计算和告警检测四个独立纯函数。
"""
from __future__ import annotations

import math

import pandas as pd
from scipy.stats import spearmanr


class FactorMonitorEngine:
    """纯函数，无 IO。月末由 MonthlyScheduler 通过 FactorMonitorService 调用。"""

    def calc_ic(
        self,
        factor_values: pd.Series,
        forward_returns: pd.Series,
    ) -> float | None:
        """Rank IC（Spearman 秩相关），nan_policy='omit'。样本 < 5 时返回 None。

        Args:
            factor_values:   index=ts_code，因子值
            forward_returns: index=ts_code，下期 return_window 日收益率

        Returns:
            IC 值（-1 ~ 1），有效样本 < 5 时返回 None。
        """
        combined = pd.DataFrame({"f": factor_values, "r": forward_returns}).dropna()
        if len(combined) < 5:
            return None
        corr, _ = spearmanr(combined["f"], combined["r"])
        return float(corr)

    def calc_ic_ir(
        self,
        ic_series: list[float],
        window: int = 3,
    ) -> tuple[float | None, float | None, float | None]:
        """返回 (ic_mean, ic_std, ir)，使用序列末尾 window 个值。

        Args:
            ic_series: 历史 IC 序列（按时间升序）
            window:    滚动月数（默认 3）

        Returns:
            (ic_mean, ic_std, ir)；数据不足或 ic_std=0 时对应字段返回 None。
            IR = ic_mean / ic_std * sqrt(window)。
        """
        if len(ic_series) < window:
            return (None, None, None)

        recent = ic_series[-window:]
        ic_mean = sum(recent) / window

        variance = sum((x - ic_mean) ** 2 for x in recent) / (window - 1)  # 样本方差
        ic_std = math.sqrt(variance)

        if ic_std < 1e-12:
            return (ic_mean, ic_std, None)

        ir = ic_mean / ic_std * math.sqrt(window)
        return (ic_mean, ic_std, ir)

    def calc_half_life(self, ic_series: list[float]) -> float | None:
        """一阶自回归估计 IC 半衰期（月）。数据 < 6 个点时返回 None。

        方法：对 IC 序列拟合 dIC_t = a + b * IC_{t-1} + ε，
        半衰期 = -ln(2) / ln(|b|)。
        若 |b| >= 1（非平稳）→ 返回 None。

        Args:
            ic_series: 历史 IC 序列（按时间升序），至少 6 个点

        Returns:
            半衰期（月）；数据不足或非平稳时返回 None。
        """
        if len(ic_series) < 6:
            return None

        y = ic_series[1:]          # IC_t（t = 1, 2, ..., n-1）
        x = ic_series[:-1]         # IC_{t-1}

        n = len(x)
        x_mean = sum(x) / n
        y_mean = sum(y) / n

        # OLS: b = cov(x, y) / var(x)
        cov_xy = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y)) / n
        var_x = sum((xi - x_mean) ** 2 for xi in x) / n

        if var_x == 0.0:
            return None

        b = cov_xy / var_x
        abs_b = abs(b)

        if abs_b >= 1.0:
            return None

        if abs_b == 0.0:
            return None

        half_life = -math.log(2) / math.log(abs_b)
        return half_life

    def detect_alert(
        self,
        ic_mean: float | None,
        ir: float | None,
        half_life_days: float | None,
        recent_ic_signs: list[float],
    ) -> str | None:
        """返回告警类型或 None。优先级：DECAY > FAST_DECAY > INEFFICIENT。

        Args:
            ic_mean:         近期 IC 均值
            ir:              信息比率（可为 None）
            half_life_days:  IC 半衰期（月，可为 None）
            recent_ic_signs: 最近 3 个月 IC 值列表

        Returns:
            'DECAY'       — 最近 3 个月 IC 全为负
            'FAST_DECAY'  — half_life_days < 5
            'INEFFICIENT' — ir < 0.3
            None          — 无告警
        """
        # DECAY：最近 3 个月 IC 均为负
        if len(recent_ic_signs) >= 3 and all(v < 0 for v in recent_ic_signs[-3:]):
            return "DECAY"

        # FAST_DECAY：半衰期 < 5 月
        if half_life_days is not None and half_life_days < 5:
            return "FAST_DECAY"

        # INEFFICIENT：IR < 0.3
        if ir is not None and ir < 0.3:
            return "INEFFICIENT"

        return None
