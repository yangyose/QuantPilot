"""UT-P13-A-01~02: Phase 13 MetricsRegistry 单元测试。

依据 docs/design/phases/phase13_production_observability.md §3.1 + §6.1：
- UT-P13-A-01: MetricsRegistry 单例 + 7 Counter / 3 Gauge / 2 Histogram 标签合法性
- UT-P13-A-02: generate_latest(REGISTRY) 输出 Prometheus exposition 格式
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.metrics import Counter, Gauge, Histogram


def test_ut_p13_a_01_metrics_registry_singleton_and_labels() -> None:
    """UT-P13-A-01: REGISTRY 是 CollectorRegistry 单例；7 Counter + 3 Gauge +
    2 Histogram 全部注册成功，标签维度合法。"""
    from quantpilot.core import metrics

    # 单例性
    assert isinstance(metrics.REGISTRY, CollectorRegistry)

    # 7 Counter
    counters = {
        "PIPELINE_RUNS": (metrics.PIPELINE_RUNS, ["status"]),
        "SIGNALS_GENERATED": (metrics.SIGNALS_GENERATED, ["type"]),
        "TUSHARE_CALLS": (metrics.TUSHARE_CALLS, ["interface", "status"]),
        "VALIDATOR_ERRORS": (metrics.VALIDATOR_ERRORS, ["data_type", "error_type"]),
        "DATA_SOURCE_FALLBACK": (
            metrics.DATA_SOURCE_FALLBACK,
            ["from_source", "to_source", "status"],
        ),
        "SCHEDULER_JOBS": (metrics.SCHEDULER_JOBS, ["job_id", "status"]),
        "NOTIFICATIONS_SENT": (
            metrics.NOTIFICATIONS_SENT,
            ["notify_type", "channel", "status"],
        ),
    }
    assert len(counters) == 7
    for name, (counter, expected_labels) in counters.items():
        assert isinstance(counter, Counter), f"{name} 不是 Counter"
        assert list(counter._labelnames) == expected_labels, (
            f"{name} 标签 {counter._labelnames} != 预期 {expected_labels}"
        )

    # 3 Gauge
    gauges = {
        "FACTOR_ICIR": (metrics.FACTOR_ICIR, ["strategy", "factor", "state"]),
        "BACKTEST_QUEUE_DEPTH": (metrics.BACKTEST_QUEUE_DEPTH, []),
        "DATA_LATENCY": (metrics.DATA_LATENCY, ["data_type"]),
    }
    assert len(gauges) == 3
    for name, (gauge, expected_labels) in gauges.items():
        assert isinstance(gauge, Gauge), f"{name} 不是 Gauge"
        assert list(gauge._labelnames) == expected_labels, (
            f"{name} 标签 {gauge._labelnames} != 预期 {expected_labels}"
        )

    # 2 Histogram
    histograms = {
        "PIPELINE_DURATION": (metrics.PIPELINE_DURATION, ["step"]),
        "API_REQUEST_DURATION": (
            metrics.API_REQUEST_DURATION,
            ["endpoint", "method", "status"],
        ),
    }
    assert len(histograms) == 2
    for name, (hist, expected_labels) in histograms.items():
        assert isinstance(hist, Histogram), f"{name} 不是 Histogram"
        assert list(hist._labelnames) == expected_labels, (
            f"{name} 标签 {hist._labelnames} != 预期 {expected_labels}"
        )


def test_ut_p13_a_02_generate_latest_prometheus_exposition() -> None:
    """UT-P13-A-02: generate_latest(REGISTRY) 输出 Prometheus exposition
    text format（含 HELP / TYPE 行 + 各指标名）。"""
    from quantpilot.core import metrics

    # 触发各类指标至少有一个 child 序列（避免空 registry 不输出）
    metrics.PIPELINE_RUNS.labels(status="success").inc()
    metrics.SIGNALS_GENERATED.labels(type="BUY").inc(5)
    metrics.TUSHARE_CALLS.labels(interface="daily_quote", status="success").inc()
    metrics.VALIDATOR_ERRORS.labels(
        data_type="daily_quote", error_type="completeness_violation_count",
    ).inc()
    metrics.DATA_SOURCE_FALLBACK.labels(
        from_source="tushare", to_source="akshare", status="trying",
    ).inc()
    metrics.SCHEDULER_JOBS.labels(job_id="daily_pipeline", status="success").inc()
    metrics.NOTIFICATIONS_SENT.labels(
        notify_type="SIGNAL_BUY", channel="wxpusher", status="success",
    ).inc()
    metrics.FACTOR_ICIR.labels(
        strategy="trend", factor="macd_hist", state="UPTREND",
    ).set(0.12)
    metrics.BACKTEST_QUEUE_DEPTH.set(2)
    metrics.DATA_LATENCY.labels(data_type="daily_quote").set(1)
    metrics.PIPELINE_DURATION.labels(step="cp1").observe(45.0)
    metrics.API_REQUEST_DURATION.labels(
        endpoint="/api/v1/signals", method="GET", status="200",
    ).observe(0.123)

    payload = generate_latest(metrics.REGISTRY).decode("utf-8")

    # 7 Counter
    assert "quantpilot_pipeline_runs_total" in payload
    assert "quantpilot_signals_generated_total" in payload
    assert "quantpilot_tushare_calls_total" in payload
    assert "quantpilot_validator_errors_total" in payload
    assert "quantpilot_data_source_fallback_total" in payload
    assert "quantpilot_scheduler_jobs_total" in payload
    assert "quantpilot_notifications_sent_total" in payload

    # 3 Gauge
    assert "quantpilot_factor_icir" in payload
    assert "quantpilot_backtest_queue_depth" in payload
    assert "quantpilot_data_latency_days" in payload

    # 2 Histogram
    assert "quantpilot_pipeline_duration_seconds" in payload
    assert "quantpilot_api_request_duration_seconds" in payload

    # HELP / TYPE 行（Prometheus exposition format）
    assert "# HELP " in payload
    assert "# TYPE " in payload
    assert "counter" in payload
    assert "gauge" in payload
    assert "histogram" in payload
