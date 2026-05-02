"""Pydantic schemas：首次启动向导 /setup（Phase 10）。"""
from __future__ import annotations

from pydantic import BaseModel


class SetupStatusData(BaseModel):
    """GET /setup/status / POST /setup/complete data 字段。"""

    completed: bool
    completed_at: str | None = None
