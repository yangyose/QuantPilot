"""Phase 13 调度器健康摘要服务（design §3.2.1）。

不持久化失败计数（V1.0 单进程）；重启后清零。
依赖 APScheduler EVENT_JOB_EXECUTED / EVENT_JOB_ERROR 事件累积 in-memory dict。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, JobEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler


class SchedulerHealthService:
    """调度器健康快照 + 事件监听累积器。"""

    def __init__(self, scheduler: AsyncIOScheduler | None) -> None:
        self._scheduler = scheduler
        self._failure_counts: dict[str, int] = {}
        self._last_run_status: dict[str, str] = {}
        self._last_error_at: dict[str, datetime] = {}
        if scheduler is not None:
            scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
            scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)

    def _on_job_executed(self, event: JobEvent) -> None:
        from quantpilot.core.metrics import SCHEDULER_JOBS
        self._last_run_status[event.job_id] = "success"
        SCHEDULER_JOBS.labels(job_id=event.job_id, status="success").inc()

    def _on_job_error(self, event: JobEvent) -> None:
        from quantpilot.core.metrics import SCHEDULER_JOBS
        self._last_run_status[event.job_id] = "failed"
        self._failure_counts[event.job_id] = (
            self._failure_counts.get(event.job_id, 0) + 1
        )
        self._last_error_at[event.job_id] = datetime.now()
        SCHEDULER_JOBS.labels(job_id=event.job_id, status="failed").inc()

    def snapshot(self) -> dict[str, Any]:
        """返回 {running, jobs: [...], total_jobs}。

        scheduler=None / 未启动时返回 running=False + 空 jobs，不抛异常。
        """
        if self._scheduler is None:
            return {"running": False, "jobs": [], "total_jobs": 0}
        jobs: list[dict[str, Any]] = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "next_run_time": (
                    job.next_run_time.isoformat() if job.next_run_time else None
                ),
                "trigger": str(job.trigger),
                "last_run_status": self._last_run_status.get(job.id, "unknown"),
                "last_error_at": (
                    self._last_error_at[job.id].isoformat()
                    if job.id in self._last_error_at else None
                ),
                "failure_count": self._failure_counts.get(job.id, 0),
            })
        return {
            "running": self._scheduler.running,
            "jobs": jobs,
            "total_jobs": len(jobs),
        }


__all__ = ["SchedulerHealthService"]
