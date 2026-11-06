"""Price-level alerts: notify once on Telegram when the live last price trades through a level.

These are plain price notifications, not trade signals: no strategy rules are evaluated.

* Levels are checked on every ticker update (WebSocket, or the REST fallback), so only
  symbols with a live ticker (the watchlist) can have alerts.
* ``above`` fires when last price >= level, ``below`` when last price <= level. When no
  condition is given it is chosen from the current price, so the alert fires on the next
  cross. An alert whose condition is already true when created is rejected.
* Firing is idempotent: ``UPDATE ... WHERE status = 'ACTIVE'`` succeeds for exactly one
  caller, so an alert is sent at most once, even with several backend instances.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select, update

from app.alerts.telegram import TelegramClient
from app.api.hub import EventHub
from app.db.models import Notification, PriceAlert
from app.notifications.messages import price_alert_event
from app.notifications.telegram_format import price_alert_text
from app.timeutil import utcnow

log = logging.getLogger(__name__)

Condition = Literal["above", "below"]
MAX_ACTIVE = 100


def alert_to_dict(a: PriceAlert) -> dict[str, Any]:
    return {
        "id": a.id, "symbol": a.symbol, "condition": a.condition, "price": a.price, "note": a.note,
        "status": a.status, "price_at_creation": a.price_at_creation, "created_at": a.created_at.isoformat(),
        "triggered_at": a.triggered_at.isoformat() if a.triggered_at else None, "trigger_price": a.trigger_price,
        "notification_status": a.notification_status,
    }


def format_price_alert(a: dict[str, Any], last: float, at: datetime) -> str:
    return price_alert_text(a["symbol"], a["condition"], a["price"], last, a.get("note"), at)


class PriceAlertService:
    def __init__(self, session_factory, hub: EventHub, telegram: TelegramClient | None,
                 refresh_seconds: float = 30.0) -> None:
        self._session_factory = session_factory
        self.hub = hub
        self.telegram = telegram
        self.center = None  # NotificationService (Phase 7), set by the container
        self.refresh_seconds = refresh_seconds
        self._active: dict[str, list[dict[str, Any]]] = {}
        self._loaded_at: float | None = None
        self._inflight: set[int] = set()
        self._lock = asyncio.Lock()

    async def reload(self) -> None:
        async with self._session_factory()() as session:
            rows = (await session.execute(select(PriceAlert).where(PriceAlert.status == "ACTIVE"))).scalars().all()
        active: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            active.setdefault(r.symbol, []).append(alert_to_dict(r))
        self._active = active
        self._loaded_at = asyncio.get_running_loop().time()

    async def _ensure_fresh(self) -> None:
        now = asyncio.get_running_loop().time()
        if self._loaded_at is None or now - self._loaded_at >= self.refresh_seconds:
            async with self._lock:
                if self._loaded_at is None or now - self._loaded_at >= self.refresh_seconds:
                    await self.reload()

    # ------------------------------------------------------------------ CRUD

    async def create(self, symbol: str, price: float, current: float, condition: Condition | None = None,
                     note: str | None = None) -> dict[str, Any]:
        if price <= 0 or current <= 0:
            raise ValueError("price must be positive")
        cond: Condition = condition or ("above" if price > current else "below")
        if (cond == "above" and current >= price) or (cond == "below" and current <= price):
            raise ValueError(f"{symbol} is already {'at or above' if cond == 'above' else 'at or below'} "
                             f"{price:g} (last {current:g}); the alert would fire immediately")
        async with self._session_factory()() as session:
            n = len((await session.execute(select(PriceAlert.id).where(PriceAlert.status == "ACTIVE"))).all())
            if n >= MAX_ACTIVE:
                raise ValueError(f"at most {MAX_ACTIVE} active price alerts")
            row = PriceAlert(symbol=symbol, condition=cond, price=price, note=(note or None), status="ACTIVE",
                             price_at_creation=current, created_at=utcnow())
            session.add(row)
            await session.commit()
            await session.refresh(row)
            data = alert_to_dict(row)
        await self.reload()
        await self.hub.broadcast("price_alert", data)
        return data

    async def cancel(self, alert_id: int) -> dict[str, Any] | None:
        async with self._session_factory()() as session:
            row = await session.get(PriceAlert, alert_id)
            if row is None:
                return None
            if row.status == "ACTIVE":
                row.status = "CANCELLED"
                await session.commit()
            data = alert_to_dict(row)
        await self.reload()
        await self.hub.broadcast("price_alert", data)
        return data

    async def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        async with self._session_factory()() as session:
            q = select(PriceAlert).order_by(PriceAlert.created_at.desc()).limit(limit)
            if status:
                q = q.where(PriceAlert.status == status)
            return [alert_to_dict(r) for r in (await session.execute(q)).scalars().all()]

    # ------------------------------------------------------------------ live checks

    async def on_ticker(self, symbol: str, view: dict[str, Any]) -> None:
        last = view.get("last_price")
        if not isinstance(last, (int, float)) or last <= 0:
            return
        await self._ensure_fresh()
        for a in list(self._active.get(symbol, [])):
            hit = last >= a["price"] if a["condition"] == "above" else last <= a["price"]
            if hit and a["id"] not in self._inflight:
                self._inflight.add(a["id"])
                try:
                    await self._fire(a, float(last))
                finally:
                    self._inflight.discard(a["id"])

    async def _fire(self, a: dict[str, Any], last: float) -> None:
        now = utcnow()
        async with self._session_factory()() as session:
            res = await session.execute(
                update(PriceAlert).where(PriceAlert.id == a["id"], PriceAlert.status == "ACTIVE")
                .values(status="TRIGGERED", triggered_at=now, trigger_price=last))
            await session.commit()
        self._active[a["symbol"]] = [x for x in self._active.get(a["symbol"], []) if x["id"] != a["id"]]
        if res.rowcount != 1:
            return  # cancelled meanwhile, or another instance fired it
        message = format_price_alert(a, last, now)
        status = "NOT_CONFIGURED"
        if self.telegram is not None:
            result = await self.telegram.send_message(message)
            status = "SENT" if result.ok else "FAILED"
            async with self._session_factory()() as session:
                session.add(Notification(signal_id=None, channel="telegram", kind="price", status=status,
                                         attempts=result.attempts, error=result.error, message=message,
                                         sent_at=now if result.ok else None))
                await session.commit()
        async with self._session_factory()() as session:
            row = await session.get(PriceAlert, a["id"])
            if row is not None:
                row.notification_status = status
                await session.commit()
                data = alert_to_dict(row)
        if self.center is not None:
            await self.center.emit(price_alert_event(a, last, now, status))
        log.info("price alert triggered", extra={"alert_id": a["id"], "symbol": a["symbol"], "level": a["price"],
                                                 "last": last, "notification": status})
        await self.hub.broadcast("price_alert", data)
