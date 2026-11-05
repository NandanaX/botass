"""Notification Center API (Phase 7). ``/api/notifications`` remains the Telegram delivery log.

The application is single-user with no login, so ``current_recipient`` resolves to the local
user. Every query is scoped by it and another recipient's event answers 404, so adding real
authentication later only means replacing that one dependency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.notifications.events import LOCAL_RECIPIENT
from app.notifications.settings import NotificationSettings

router = APIRouter(prefix="/api/notification-center", tags=["notifications"])


def current_recipient() -> str:
    return LOCAL_RECIPIENT


Recipient = Annotated[str, Depends(current_recipient)]
CategoryQ = Literal["signal", "trade", "risk", "market", "system", "validation"]
SeverityQ = Literal["INFO", "SUCCESS", "WARNING", "CRITICAL"]


class ReadBody(BaseModel):
    read: bool = True


class ReadAllBody(BaseModel):
    category: CategoryQ | None = None


@router.get("/events", summary="Notifications, newest first (paginated and filterable)")
async def events(
    c: ContainerDep, recipient: Recipient, category: CategoryQ | None = None,
    symbol: str | None = Query(None, pattern=r"^[A-Z0-9]{2,20}USDT$"), strategy: Literal["A", "B", "C"] | None = None,
    timeframe: str | None = Query(None, max_length=8), severity: SeverityQ | None = None,
    read: Literal["read", "unread"] | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
    after_id: int | None = Query(None, ge=0, description="only events newer than this id (recovery after a disconnect)"),
    include_muted: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return await c.notifications.list_events(recipient, category=category, symbol=symbol, strategy=strategy,
                                             timeframe=timeframe, severity=severity, read=read, date_from=date_from,
                                             date_to=date_to, after_id=after_id, include_muted=include_muted,
                                             limit=limit, offset=offset)


@router.get("/unread-count", summary="Unread notifications (total and critical)")
async def unread_count(c: ContainerDep, recipient: Recipient) -> dict[str, int]:
    return await c.notifications.unread_count(recipient)


@router.get("/facets", summary="Values available for the filter controls")
async def facets(c: ContainerDep, recipient: Recipient) -> dict[str, list[str]]:
    return await c.notifications.facets(recipient)


@router.post("/read-all", summary="Mark all notifications (optionally of one category) as read")
async def read_all(c: ContainerDep, recipient: Recipient, body: ReadAllBody | None = None) -> dict[str, Any]:
    return await c.notifications.mark_all_read(recipient, body.category if body else None)


@router.get("/incidents", summary="System and data incident history")
async def incidents(c: ContainerDep, status: Literal["OPEN", "RESOLVED"] | None = None,
                    limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    return await c.notifications.incidents(status, limit)


@router.get("/preferences", summary="Notification preferences")
async def get_preferences(c: ContainerDep) -> dict[str, Any]:
    return {"preferences": c.settings.current.notifications.model_dump(mode="json"),
            "telegram_configured": c.env.telegram_configured,
            "telegram_enabled": c.settings.current.alerts.telegram_enabled}


@router.put("/preferences", summary="Update notification preferences")
async def put_preferences(body: NotificationSettings, c: ContainerDep) -> dict[str, Any]:
    await c.settings.update(c.settings.current.model_copy(update={"notifications": body}))
    return await get_preferences(c)


@router.get("/events/{event_id}", summary="One notification with its context, deliveries and group members")
async def event(event_id: int, c: ContainerDep, recipient: Recipient) -> dict[str, Any]:
    found = await c.notifications.get_event(recipient, event_id)
    if found is None:
        raise HTTPException(404, "notification not found")
    return found


@router.post("/events/{event_id}/read", summary="Mark one notification read or unread")
async def mark_read(event_id: int, c: ContainerDep, recipient: Recipient, body: ReadBody | None = None) -> dict[str, Any]:
    done = await c.notifications.mark_read(recipient, event_id, body.read if body else True)
    if done is None:
        raise HTTPException(404, "notification not found")
    return done
