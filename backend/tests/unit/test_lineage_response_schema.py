"""UT-P12-A-01/02: SignalLineageResponse 三层 schema 序列化测试（Phase 12）。

依据 phase12_factor_lineage.md §3.1.3 + §6.1：
- ScoreSnapshotLineage 共 19 字段（ts_code(1) + L1 5 + L2 9 + L3 4）
- snapshot 为 None 时 score_snapshot=null，区分"无快照"与"快照字段全 NULL"
"""
from __future__ import annotations

from quantpilot.schemas.signals import (
    PipelineRunLineage,
    ScoreSnapshotLineage,
    SignalLineageResponse,
)

# ScoreSnapshotLineage 字段清单（19 项；与设计文档 §3.1.3 一一对应）
_EXPECTED_SNAPSHOT_FIELDS = {
    # 标识
    "ts_code",
    # L1 业务可解释（5）
    "composite_score",
    "composite_z",
    "composite_pct_in_market",
    "market_state",
    "trigger_reason",
    # L2 ICIR + 中性化（9）
    "trend_score",
    "momentum_score",
    "reversion_score",
    "value_score",
    "weights_source",
    "hysteresis_status",
    "score_breakdown",
    "factor_winsorized",
    "factor_neutralized",
    # L3 正交化 + 审计（4）
    "raw_factors",
    "factor_orthogonal",
    "score_breakdown_raw",
    "score_breakdown_residual",
}

_EXPECTED_PIPELINE_FIELDS = {
    "trade_date",
    "cp1_at",
    "cp2_at",
    "cp3_at",
    "data_snapshot_version",
}


def test_ut_p12_a_01_lineage_response_serializes_19_fields() -> None:
    """UT-P12-A-01: ScoreSnapshotLineage 含 19 字段，序列化齐全。

    评审 P2-3 修订：字段数从 17 改为 19（ts_code(1) + L1 5 + L2 9 + L3 4）。
    """
    assert set(ScoreSnapshotLineage.model_fields.keys()) == _EXPECTED_SNAPSHOT_FIELDS
    assert len(_EXPECTED_SNAPSHOT_FIELDS) == 19

    assert set(PipelineRunLineage.model_fields.keys()) == _EXPECTED_PIPELINE_FIELDS

    assert set(SignalLineageResponse.model_fields.keys()) == {
        "signal_id",
        "trade_date",
        "score_snapshot",
        "pipeline_run",
    }

    snapshot = ScoreSnapshotLineage(
        ts_code="600519.SH",
        composite_score=99.87,
        composite_z=3.85,
        composite_pct_in_market=0.0005,
        market_state="UPTREND",
        trigger_reason="quantile_top_1pct",
        trend_score=1.85,
        momentum_score=0.94,
        reversion_score=-0.21,
        value_score=1.12,
        weights_source="default_matrix",
        hysteresis_status="active",
        score_breakdown={"trend": {"score": 1.85, "weight": 0.4}},
        factor_winsorized={"trend": {"ma_diff": 0.85}},
        factor_neutralized={"trend": {"ma_diff": 0.72}},
        raw_factors={"ma_diff": 0.92},
        factor_orthogonal={"trend": {"ma_diff_normalized": 0.65}},
        score_breakdown_raw={"trend": 1.85},
        score_breakdown_residual={"trend": 0.12},
    )
    pipeline_run = PipelineRunLineage(
        trade_date="2026-05-12",
        cp1_at="2026-05-12T15:30:00+08:00",
        cp2_at="2026-05-12T15:35:12+08:00",
        cp3_at="2026-05-12T15:37:48+08:00",
        data_snapshot_version="abc12345",
    )
    response = SignalLineageResponse(
        signal_id=12345,
        trade_date="2026-05-12",
        score_snapshot=snapshot,
        pipeline_run=pipeline_run,
    )

    dumped = response.model_dump()
    assert dumped["signal_id"] == 12345
    assert dumped["trade_date"] == "2026-05-12"
    assert set(dumped["score_snapshot"].keys()) == _EXPECTED_SNAPSHOT_FIELDS
    assert dumped["score_snapshot"]["composite_score"] == 99.87
    assert dumped["score_snapshot"]["composite_z"] == 3.85
    assert dumped["score_snapshot"]["factor_orthogonal"] == {
        "trend": {"ma_diff_normalized": 0.65}
    }
    assert set(dumped["pipeline_run"].keys()) == _EXPECTED_PIPELINE_FIELDS


def test_ut_p12_a_02_lineage_response_snapshot_none() -> None:
    """UT-P12-A-02: snapshot=None 时 score_snapshot=null。

    区分"无快照"（手动信号，score_snapshot 整对象为 null）与"快照存在但字段为 NULL"
    （5 步管线产物 v1.1 commit 之前的历史信号）。
    """
    response = SignalLineageResponse(
        signal_id=999,
        trade_date="2026-04-01",
        score_snapshot=None,
        pipeline_run=None,
    )
    dumped = response.model_dump()
    assert dumped["score_snapshot"] is None
    assert dumped["pipeline_run"] is None

    # 区分：score_snapshot 存在但 L3 字段为 None ≠ score_snapshot=None
    snapshot_null_fields = ScoreSnapshotLineage(
        ts_code="000001.SZ",
        composite_score=88.0,
        # 其余字段省略 → 全部 None（默认值）
    )
    response_with_partial_snapshot = SignalLineageResponse(
        signal_id=1000,
        trade_date="2026-04-02",
        score_snapshot=snapshot_null_fields,
        pipeline_run=None,
    )
    dumped2 = response_with_partial_snapshot.model_dump()
    assert dumped2["score_snapshot"] is not None
    assert dumped2["score_snapshot"]["ts_code"] == "000001.SZ"
    assert dumped2["score_snapshot"]["composite_score"] == 88.0
    assert dumped2["score_snapshot"]["factor_orthogonal"] is None
    assert dumped2["score_snapshot"]["factor_neutralized"] is None
