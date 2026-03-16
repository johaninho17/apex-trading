from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from services.notification_store import (
    delete_notification_by_id,
    delete_notifications_by_group,
    get_notification_detail,
    list_notification_summaries,
)

router = APIRouter()


class NotificationSummary(BaseModel):
    id: str
    ts: int
    title: str
    channel: str
    group: str


class NotificationListResponse(BaseModel):
    items: List[NotificationSummary] = Field(default_factory=list)
    count: int = 0


class NotificationDetail(BaseModel):
    id: str
    ts: int
    title: str
    channel: str
    group: str
    event_type: str
    severity: str
    message: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class NotificationDeleteResponse(BaseModel):
    success: bool
    deleted: int = 0


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    limit: int = Query(default=200, ge=1, le=1000),
    days: int = Query(default=7, ge=1, le=7),
    group: str = Query(default="all"),
):
    items = await list_notification_summaries(limit=limit, days=days, group=group)
    return {"items": items, "count": len(items)}


@router.get("/{notification_id}", response_model=NotificationDetail)
async def notification_detail(notification_id: str):
    detail = await get_notification_detail(notification_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Notification not found")
    return detail


@router.delete("/{notification_id}", response_model=NotificationDeleteResponse)
async def delete_notification(notification_id: str):
    deleted = await delete_notification_by_id(notification_id)
    return {"success": deleted, "deleted": 1 if deleted else 0}


@router.delete("", response_model=NotificationDeleteResponse)
async def delete_notifications(group: str = Query(default="all")):
    deleted = await delete_notifications_by_group(group=group)
    return {"success": True, "deleted": deleted}
