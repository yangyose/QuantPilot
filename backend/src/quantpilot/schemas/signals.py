"""Pydantic schemas for signals API（Phase 5 / Phase 12 三层 lineage 扩展）。"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class SignalResponse(BaseModel):
    id: int
    ts_code: str
    name: str | None = None  # 股票名称（由 API 层批量查询注入，ORM 无此字段）
    signal_type: str
    trade_date: date
    score: float | None
    suggested_pct: float | None
    suggested_price_low: float | None
    suggested_price_high: float | None
    stop_loss_price: float | None
    signal_strength: str | None
    liquidity_note: str | None
    t1_warning: str | None
    reason: str | None
    status: str
    created_at: datetime | None
    # V1.5-G G-4d-2（§2 派生语义）：API 请求期按当前账户叠加的已持仓标记。
    # 管线产的共享信号无此语义（默认 False）；GET /signals 经 SignalViewService 叠加。
    is_holding: bool = False
    # Phase 11 §9.1：分位主路径三层输出 + trigger_reason 细分
    # 注：weights_source 不在 Signal ORM（仅在 candidate_pool / signal_score_snapshot
    # 上下文有意义），Phase 12 评审 R12-P2-2 删除该字段避免响应永远 null 误导前端。
    composite_z: float | None = None
    composite_pct_in_market: float | None = None
    trigger_reason: str | None = None
    model_config = ConfigDict(from_attributes=True)


class SignalStatusUpdate(BaseModel):
    status: str  # VIEWED / ACTED（API 层校验，仅允许这两个值）


# ---------------------------------------------------------------------------
# Phase 12 §3.1.3：信号血缘三层 schema
# ---------------------------------------------------------------------------


class ScoreSnapshotLineage(BaseModel):
    """信号评分快照 L1+L2+L3 完整字段（共 19 项）。

    分层依据：phase12_factor_lineage.md §3.1.3
    - 标识 1：ts_code
    - L1 业务可解释 5：composite_score / composite_z / composite_pct_in_market /
      market_state / trigger_reason
    - L2 ICIR + 中性化 9：trend_score / momentum_score / reversion_score /
      value_score / weights_source / hysteresis_status / score_breakdown /
      factor_winsorized / factor_neutralized
    - L3 正交化 + 审计 4：raw_factors / factor_orthogonal /
      score_breakdown_raw / score_breakdown_residual

    字段名与 ORM 对齐说明：策略 key 为 mean_reversion，但 ORM
    `CandidatePool.reversion_score` / `SignalScoreSnapshot.reversion_score` 列名
    不带 mean_，故 schema 字段名也为 reversion_score（v1.1 评审 P1-2 修订）。
    """

    # 标识
    ts_code: str
    # L1 业务可解释（5）
    composite_score: float | None = None
    composite_z: float | None = None
    composite_pct_in_market: float | None = None
    market_state: str | None = None
    trigger_reason: str | None = None
    # L2 ICIR + 中性化（9）
    trend_score: float | None = None
    momentum_score: float | None = None
    reversion_score: float | None = None
    value_score: float | None = None
    weights_source: str | None = None
    hysteresis_status: str | None = None
    score_breakdown: dict | None = None
    factor_winsorized: dict | None = None
    factor_neutralized: dict | None = None
    # L3 正交化 + 审计（4）
    raw_factors: dict | None = None
    factor_orthogonal: dict | None = None
    score_breakdown_raw: dict | None = None
    score_breakdown_residual: dict | None = None

    model_config = ConfigDict(from_attributes=True)


class PipelineRunLineage(BaseModel):
    """流水线运行摘要（CP1/CP2/CP3 时间戳 + 数据快照版本）。"""

    trade_date: str
    cp1_at: str | None = None
    cp2_at: str | None = None
    cp3_at: str | None = None
    data_snapshot_version: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SignalLineageResponse(BaseModel):
    """GET /signals/{id}/lineage 响应（Phase 12 §3.1.3 三层 schema）。

    评审 P1-1 修订：端点响应 schema 升级，含 19 字段 ScoreSnapshotLineage。
    score_snapshot=None 表示"无快照"（手动信号 / Phase 7 之前历史信号），
    与"快照存在但字段为 NULL"（v1.1 commit 之前 5 步管线产物未落库）严格区分。
    """

    signal_id: int
    trade_date: str
    score_snapshot: ScoreSnapshotLineage | None = None
    pipeline_run: PipelineRunLineage | None = None
