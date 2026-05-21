"""UT-P13-A-03~04: Phase 13 SchedulerHealthService 单元测试。

依据 docs/design/phases/phase13_production_observability.md §3.2.1 + §6.1：
- UT-P13-A-03: scheduler=None 时 snapshot 返回 running=False + 空 jobs
- UT-P13-A-04: EVENT_JOB_ERROR / EVENT_JOB_EXECUTED 监听后状态正确累积
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from quantpilot.services.scheduler_health import SchedulerHealthService


def test_ut_p13_a_03_snapshot_when_scheduler_none() -> None:
    """UT-P13-A-03: scheduler=None 时 snapshot 返回 running=False + 空 jobs + total=0。"""
    svc = SchedulerHealthService(scheduler=None)
    snap = svc.snapshot()
    assert snap["running"] is False
    assert snap["jobs"] == []
    assert snap["total_jobs"] == 0


def test_ut_p13_a_04_event_listeners_accumulate_state() -> None:
    """UT-P13-A-04: EVENT_JOB_ERROR / EVENT_JOB_EXECUTED 监听器正确累积
    failure_count / last_run_status / last_error_at。"""
    # 模拟 scheduler 的 add_listener 行为
    listeners: list = []

    class _FakeScheduler:
        running = True
        def add_listener(self, fn, mask):  # noqa: ANN001
            listeners.append((fn, mask))
        def get_jobs(self):
            return [
                SimpleNamespace(
                    id="daily_pipeline",
                    next_run_time=datetime(2026, 5, 22, 9, 30),
                    trigger="cron[hour=09]",
                ),
                SimpleNamespace(
                    id="monthly_job",
                    next_run_time=None,
                    trigger="cron[day='last']",
                ),
            ]

    svc = SchedulerHealthService(scheduler=_FakeScheduler())  # type: ignore[arg-type]
    # 2 个 listener 已注册
    assert len(listeners) == 2

    # 触发 EVENT_JOB_EXECUTED on daily_pipeline → success
    executed_event = SimpleNamespace(job_id="daily_pipeline")
    svc._on_job_executed(executed_event)
    assert svc._last_run_status["daily_pipeline"] == "success"

    # 触发 EVENT_JOB_ERROR on monthly_job 2 次 → failure_count=2
    error_event = SimpleNamespace(job_id="monthly_job")
    svc._on_job_error(error_event)
    svc._on_job_error(error_event)
    assert svc._failure_counts["monthly_job"] == 2
    assert svc._last_run_status["monthly_job"] == "failed"
    assert "monthly_job" in svc._last_error_at

    # snapshot 返回结构
    snap = svc.snapshot()
    assert snap["running"] is True
    assert snap["total_jobs"] == 2
    by_id = {j["id"]: j for j in snap["jobs"]}
    assert by_id["daily_pipeline"]["last_run_status"] == "success"
    assert by_id["daily_pipeline"]["failure_count"] == 0
    assert by_id["daily_pipeline"]["last_error_at"] is None
    assert by_id["monthly_job"]["last_run_status"] == "failed"
    assert by_id["monthly_job"]["failure_count"] == 2
    assert by_id["monthly_job"]["last_error_at"] is not None
    assert by_id["monthly_job"]["next_run_time"] is None
