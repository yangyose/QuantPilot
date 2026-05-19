"""Pydantic schemas：因子质量 /factor-quality（Phase 7 + Phase 11 §9.2 扩展）。"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class FactorIcHistoryItem(BaseModel):
    """GET /factor-quality 和 /factor-quality/history 的 item 结构（Phase 7 旧表）。"""

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
