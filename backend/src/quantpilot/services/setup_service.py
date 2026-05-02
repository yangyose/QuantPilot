"""SetupService：首次启动向导完成状态（Phase 10 §6.6）。

状态存 `system_config` 表（key=`setup.completed`，value=JSON 字符串），
避免污染 `user_config`（后者是业务配置面板数据源）。

Phase 10 §13 风险缓解：Pipeline/信号生成前可读本状态作为"冷启动标志"。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.system import SystemConfig

logger = logging.getLogger(__name__)

SETUP_KEY = "setup.completed"


class SetupService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_status(self) -> dict:
        """返回 {completed: bool, completed_at: str | None}。"""
        stmt = select(SystemConfig).where(SystemConfig.key == SETUP_KEY)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None or not row.value:
            return {"completed": False, "completed_at": None}
        try:
            data = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            logger.warning("setup_status_invalid_json value=%r", row.value)
            return {"completed": False, "completed_at": None}
        return {
            "completed": bool(data.get("completed")),
            "completed_at": data.get("completed_at"),
        }

    async def mark_completed(self) -> dict:
        """upsert `setup.completed`，返回 {completed: True, completed_at: iso}。"""
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = json.dumps({"completed": True, "completed_at": now_iso})
        stmt = (
            insert(SystemConfig)
            .values(key=SETUP_KEY, value=payload, updated_at=func.now())
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": payload, "updated_at": func.now()},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
        return {"completed": True, "completed_at": now_iso}
