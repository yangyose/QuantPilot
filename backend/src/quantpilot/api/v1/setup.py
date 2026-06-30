"""REST API：首次启动向导 /setup（Phase 10 §6.6）。

端点：
- GET  /setup/status   → {completed, completed_at}；前端据此决定是否跳转 /onboarding
- POST /setup/complete → 标记完成；幂等（重复 POST 返回同一 completed_at 语义一致）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from quantpilot.api.deps import get_current_user_id, get_setup_service
from quantpilot.schemas.setup import SetupStatusData
from quantpilot.services.setup_service import SetupService

router = APIRouter()


@router.get("/status")
async def get_setup_status(
    service: SetupService = Depends(get_setup_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """GET /setup/status → 向导完成状态。"""
    data = await service.get_status()
    return {"code": 0, "data": SetupStatusData(**data).model_dump(), "msg": "ok"}


@router.post("/complete")
async def mark_setup_complete(
    service: SetupService = Depends(get_setup_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """POST /setup/complete → 标记向导完成。"""
    data = await service.mark_completed()
    return {"code": 0, "data": SetupStatusData(**data).model_dump(), "msg": "ok"}
