"""REST API：系统内通知 /notifications（Phase 10）。

端点（Phase 10 §5.4 + §6.4）：
- GET /notifications                     通知列表（分页 + notify_type/unread_only 筛选）
- GET /notifications/unread-count        未读数量（前端 Bell Badge）
- GET /notifications/wx-status           WxPusher 配置是否可用
- POST /notifications/{id}/read          标记单条已读（不存在 → 404）
- POST /notifications/read-all           全部标记已读
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from quantpilot.api.deps import (
    get_current_account_id,
    get_current_user_id,
    get_notification_service,
)
from quantpilot.core.config import settings
from quantpilot.schemas.notification import (
    MarkAllReadData,
    MarkReadData,
    NotificationItem,
    NotificationListData,
    UnreadCountData,
    WxStatusData,
)
from quantpilot.services.notification_service import NotificationService

router = APIRouter()


@router.get("")
async def list_notifications(
    notify_type: str | None = None,
    unread_only: bool = False,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: NotificationService = Depends(get_notification_service),
    account_id: int = Depends(get_current_account_id),
) -> dict:
    """GET /notifications → 通知列表 + 总数 + 未读数（按当前账户隔离，G-4b §6.4）。"""
    items, total = await service.list_notifications(
        notify_type=notify_type,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
        account_id=account_id,
    )
    unread = await service.count_unread(account_id=account_id)
    data = NotificationListData(
        items=[NotificationItem.model_validate(n) for n in items],
        total=total,
        unread=unread,
    )
    return {"code": 0, "data": data.model_dump(mode="json"), "msg": "ok"}


@router.get("/unread-count")
async def unread_count(
    service: NotificationService = Depends(get_notification_service),
    account_id: int = Depends(get_current_account_id),
) -> dict:
    """GET /notifications/unread-count → {unread: N}（按当前账户隔离，G-4b §6.4）。"""
    unread = await service.count_unread(account_id=account_id)
    return {"code": 0, "data": UnreadCountData(unread=unread).model_dump(), "msg": "ok"}


@router.get("/wx-status")
async def wx_status(_: int = Depends(get_current_user_id)) -> dict:
    """GET /notifications/wx-status → WxPusher 配置状态（判断环境变量）。

    规则：`wx_configured = True` 当且仅当 WXPUSHER_APP_TOKEN 与 WXPUSHER_UID 均非空。
    `uid_masked`：配置时脱敏返回（如 `UID_***xxx`）；未配置时 None。
    """
    configured = bool(settings.wxpusher_app_token) and bool(settings.wxpusher_uid)
    uid = settings.wxpusher_uid
    uid_masked: str | None = None
    if configured and uid:
        tail = uid[-4:] if len(uid) >= 4 else uid
        uid_masked = f"UID_***{tail}"
    data = WxStatusData(wx_configured=configured, uid_masked=uid_masked)
    return {"code": 0, "data": data.model_dump(), "msg": "ok"}


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: int,
    service: NotificationService = Depends(get_notification_service),
    account_id: int = Depends(get_current_account_id),
) -> dict:
    """POST /notifications/{id}/read → 标记单条已读。不存在/越权 → 404（G-4b §6.4）。"""
    notif = await service.mark_read(notification_id, account_id=account_id)
    if notif is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"通知 {notification_id} 不存在",
        )
    data = MarkReadData(id=notif.id, read_at=notif.read_at)  # type: ignore[arg-type]
    return {"code": 0, "data": data.model_dump(mode="json"), "msg": "ok"}


@router.post("/read-all")
async def mark_all_read(
    service: NotificationService = Depends(get_notification_service),
    account_id: int = Depends(get_current_account_id),
) -> dict:
    """POST /notifications/read-all → 批量标记已读（本账户可见范围，G-4b §6.4）。"""
    updated = await service.mark_all_read(account_id=account_id)
    return {"code": 0, "data": MarkAllReadData(updated=updated).model_dump(), "msg": "ok"}
