"""Pydantic schemas：因子质量 /factor-quality（Phase 7 + Phase 11 §9.2 扩展）。"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class FactorIcHistoryItem(BaseModel):
    """GET /factor-quality 和 /factor-quality/history 的 item 结构。

    Phase 15 §15-7：底层表已由 factor_ic_history 归并进 factor_ic_window_state
    （row_type='monthly_quality'），但**对外响应字段名保持不变**（前端零改动）；
    经 ``from_window_state`` 从复用列映射。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    calc_month: date
    strategy_name: str
    factor_name: str
    ic_value: float | None
    ic_mean_3m: float | None
    ic_std_3m: float | None
    ir_3m: float | None
    half_life_days: float | None
    return_window: int
    alert_status: str | None

    @classmethod
    def from_window_state(cls, row: object) -> FactorIcHistoryItem:
        """从 FactorICWindowState monthly_quality 行映射（复用列还原旧语义）。

        trade_date→calc_month / strategy→strategy_name / factor→factor_name /
        ic_mean_state→ic_mean_3m / ic_std_state→ic_std_3m / icir→ir_3m /
        half_life→half_life_days；return_window 月度路径恒 20。
        """
        def _f(v: object) -> float | None:
            return float(v) if v is not None else None  # type: ignore[arg-type]

        return cls(
            id=row.id,
            calc_month=row.trade_date,
            strategy_name=row.strategy,
            factor_name=row.factor,
            ic_value=_f(row.ic_value),
            ic_mean_3m=_f(row.ic_mean_state),
            ic_std_3m=_f(row.ic_std_state),
            ir_3m=_f(row.icir),
            half_life_days=_f(row.half_life),
            return_window=20,
            alert_status=row.alert_status,
        )


class ICRollingHistoryItem(BaseModel):
    """Phase 11 §9.2：GET /factor-quality/ic-history 单行（factor_ic_window_state 聚合行）。"""

    model_config = ConfigDict(from_attributes=True)

    trade_date: date
    strategy: str
    factor: str
    state: str
    ic_value: float | None
    ic_mean_state: float | None
    ic_std_state: float | None
    icir: float | None
    sample_size: int
    ic_ci_low: float | None
    ic_ci_high: float | None
    t_stat: float | None
    half_life: int | None


class CurrentWeightsItem(BaseModel):
    """Phase 11 §9.2：GET /factor-quality/current-weights 单行（strategy_weights_history 最近）。"""

    model_config = ConfigDict(from_attributes=True)

    state: str
    strategy: str
    trade_date: date              # 生效日（当前 active 行的 trade_date）
    weight_used: float
    weights_source: str           # icir / default_matrix / user_override
    hysteresis_status: str        # stable / pending_switch
