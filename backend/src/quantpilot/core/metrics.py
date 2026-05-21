"""Phase 13 Prometheus 指标注册中心（design §3.1.1）。

设计原则：
- 单例 CollectorRegistry，进程级唯一；多 worker 部署需用 multiprocess mode（V1.5+）
- 业务 service 通过模块级常量 import metric handles，避免 service 持有 registry
- 标签维度受控（V1.0 仅 7 个核心 Counter + 3 Gauge + 2 Histogram）

埋点接入点见设计 §3.1.2：
- DailyPipeline / SignalService / TushareAdapter / DataService / NotificationService
- FactorMonitorService / BacktestService / FastAPI middleware
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

# ────── 7 Counter ───────────────────────────────────────────────────────
PIPELINE_RUNS = Counter(
    "quantpilot_pipeline_runs_total",
    "DailyPipeline 执行计数",
    ["status"],  # success / failed / partial
    registry=REGISTRY,
)
SIGNALS_GENERATED = Counter(
    "quantpilot_signals_generated_total",
    "信号生成计数",
    ["type"],  # BUY / SELL / HOLD
    registry=REGISTRY,
)
TUSHARE_CALLS = Counter(
    "quantpilot_tushare_calls_total",
    "Tushare 接口调用计数",
    ["interface", "status"],  # status: success / rate_limit / error
    registry=REGISTRY,
)
VALIDATOR_ERRORS = Counter(
    "quantpilot_validator_errors_total",
    "DataValidator 错误计数",
    ["data_type", "error_type"],
    registry=REGISTRY,
)
DATA_SOURCE_FALLBACK = Counter(
    "quantpilot_data_source_fallback_total",
    "数据源降级计数",
    ["from_source", "to_source", "status"],
    registry=REGISTRY,
)
SCHEDULER_JOBS = Counter(
    "quantpilot_scheduler_jobs_total",
    "调度器 Job 执行计数",
    ["job_id", "status"],
    registry=REGISTRY,
)
NOTIFICATIONS_SENT = Counter(
    "quantpilot_notifications_sent_total",
    "通知发送计数",
    ["notify_type", "channel", "status"],
    registry=REGISTRY,
)

# ────── 3 Gauge ─────────────────────────────────────────────────────────
FACTOR_ICIR = Gauge(
    "quantpilot_factor_icir",
    "因子 ICIR（月末批后更新）",
    ["strategy", "factor", "state"],
    registry=REGISTRY,
)
BACKTEST_QUEUE_DEPTH = Gauge(
    "quantpilot_backtest_queue_depth",
    "回测任务队列深度",
    registry=REGISTRY,
)
DATA_LATENCY = Gauge(
    "quantpilot_data_latency_days",
    "数据延迟（today - max(trade_date)）",
    ["data_type"],
    registry=REGISTRY,
)

# ────── 2 Histogram ─────────────────────────────────────────────────────
PIPELINE_DURATION = Histogram(
    "quantpilot_pipeline_duration_seconds",
    "Pipeline 各 CP 执行时长",
    ["step"],
    buckets=(5, 15, 30, 60, 120, 300, 600, 1800),
    registry=REGISTRY,
)
API_REQUEST_DURATION = Histogram(
    "quantpilot_api_request_duration_seconds",
    "API 请求耗时",
    ["endpoint", "method", "status"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=REGISTRY,
)


__all__ = [
    "REGISTRY",
    "PIPELINE_RUNS",
    "SIGNALS_GENERATED",
    "TUSHARE_CALLS",
    "VALIDATOR_ERRORS",
    "DATA_SOURCE_FALLBACK",
    "SCHEDULER_JOBS",
    "NOTIFICATIONS_SENT",
    "FACTOR_ICIR",
    "BACKTEST_QUEUE_DEPTH",
    "DATA_LATENCY",
    "PIPELINE_DURATION",
    "API_REQUEST_DURATION",
]
