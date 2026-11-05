"""Notification center service: idempotent event storage, outbox delivery, incidents, grouping.

Producers call ``add`` inside their own database transaction, so a notification is committed
atomically with the signal or paper trade it describes (the outbox pattern); then ``publish``
announces new rows over the WebSocket. ``emit`` is the standalone variant for events without a
domain write. Nothing here may ever interrupt signal generation: failures are logged, and events
that could not be stored are buffered and retried.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, delete, func, or_, select, update

from app.alerts.telegram import SendResult
from app.db.models import Notification, NotificationDelivery, NotificationEvent, NotificationIncident, Signal
from app.db.session import upsert_stmt
from app.logging_setup import redact
from app.notifications.events import LOCAL_RECIPIENT, TAXONOMY, EventIn, route
from app.notifications.messages import correlated_summary, telegram_message
from app.notifications.settings import SEVERITY_RANK, NotificationSettings
from app.timeutil import utcnow

log = logging.getLogger(__name__)

CHANNEL_TELEGRAM = "telegram"
# Seconds before the 2nd, 3rd and later attempts. An alert keeps retrying until it expires (not_after):
# a limit on attempts would silently lose stop-loss alerts during any outage longer than a few minutes.
RETRY_DELAYS = (60, 300, 900)
DEFAULT_TELEGRAM_TTL = timedelta(hours=6)
# The signal service sends its alert right after the commit; the worker only steps in if that never happened.
SYNC_SEND_GRACE = timedelta(seconds=90)
STALE_SENDING = timedelta(minutes=5)
TELEGRAM_INCIDENT = "telegram_delivery"


def event_to_dict(e: NotificationEvent) -> dict[str, Any]:
    return {
        "id": e.id, "event_id": e.event_id, "event_type": e.event_type, "category": e.category, "severity": e.severity,
        "title": e.title, "message": e.message, "payload": e.payload, "symbol": e.symbol, "timeframe": e.timeframe,
        "strategy_id": e.strategy_id, "strategy_version": e.strategy_version, "signal_id": e.signal_id,
        "paper_trade_id": e.paper_trade_id, "incident_id": e.incident_id, "group_key": e.group_key,
        "is_group_summary": e.is_group_summary, "muted": e.muted, "delivery_status": e.delivery_status,
        "occurred_at": e.occurred_at.isoformat(), "created_at": e.created_at.isoformat(),
        "read_at": e.read_at.isoformat() if e.read_at else None, "links": event_links(e),
    }


def delivery_to_dict(d: NotificationDelivery) -> dict[str, Any]:
    return {"id": d.id, "channel": d.channel, "status": d.status, "attempts": d.attempts, "last_error": d.last_error,
            "next_attempt_at": d.next_attempt_at.isoformat(), "created_at": d.created_at.isoformat(),
            "sent_at": d.sent_at.isoformat() if d.sent_at else None}


def incident_to_dict(i: NotificationIncident) -> dict[str, Any]:
    return {"id": i.id, "key": i.key, "status": i.status, "category": i.category, "severity": i.severity, "title": i.title,
            "message": i.message, "occurrences": i.occurrences, "started_at": i.started_at.isoformat(),
            "last_seen_at": i.last_seen_at.isoformat(), "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
            "data": i.data}


def event_links(e: NotificationEvent) -> list[dict[str, str]]:
    """Links to pages that exist. A signal link is only offered when a signal id is recorded."""
    out: list[dict[str, str]] = []
    tf = f"?tf={e.timeframe}" if e.timeframe else ""
    if e.paper_trade_id and e.symbol:
        out.append({"label": "Open paper trade on chart", "href": f"/market/{e.symbol}{tf}{'&' if tf else '?'}trade={e.paper_trade_id}"})
    elif e.signal_id and e.symbol:
        out.append({"label": "Open signal on chart", "href": f"/market/{e.symbol}{tf}{'&' if tf else '?'}signal={e.signal_id}"})
    elif e.symbol:
        out.append({"label": "Open chart", "href": f"/market/{e.symbol}{tf}"})
    if e.signal_id and e.event_type.startswith("SIGNAL_"):
        out.append({"label": "Signal details", "href": f"/signals?id={e.signal_id}"})
    if e.paper_trade_id or e.category in ("risk", "validation") or e.strategy_id:
        out.append({"label": "Validation Monitor", "href": "/validation"})
    if e.category == "risk":
        out.append({"label": "Risk settings", "href": "/settings"})
    if e.category == "system":
        out.append({"label": "Incident history", "href": "/notifications?tab=incidents"})
    return out


class NotificationService:
    def __init__(self, session_factory, hub, settings_store, telegram=None) -> None:
        self._session_factory = session_factory
        self.hub = hub
        self.settings = settings_store
        self.telegram = telegram
        self._last_published: int | None = None
        self._buffer: deque[EventIn] = deque(maxlen=500)
        self._wake = asyncio.Event()
        self._last_prune: datetime | None = None
        self.stats: dict[str, int] = {"malformed": 0, "store_failures": 0, "buffered": 0}
        self.heartbeat_at: datetime | None = None

    @property
    def prefs(self) -> NotificationSettings:
        return self.settings.current.notifications

    # ------------------------------------------------------------------ storing events
    def _validate(self, ev: EventIn | dict[str, Any]) -> EventIn | None:
        if isinstance(ev, EventIn):
            return ev
        try:
            return EventIn.model_validate(ev)
        except ValidationError as exc:
            self.stats["malformed"] += 1
            log.error("malformed notification event ignored", extra={"error": str(exc)[:500]})
            return None

    async def add(self, session, ev: EventIn | dict[str, Any]) -> int | None:
        """Store an event inside the caller's transaction. Returns its id, or None when it already
        existed or could not be stored; a failure here never fails the caller's own write."""
        event = self._validate(ev)
        if event is None:
            return None
        try:
            return await self._insert(session, event)
        except Exception:  # noqa: BLE001 - never interrupt the producer
            self.stats["store_failures"] += 1
            log.exception("notification could not be stored", extra={"event_id": event.event_id})
            return None

    async def _insert(self, session, event: EventIn) -> int | None:
        now = utcnow()
        settings = self.settings.current
        telegram_on = self.telegram is not None and settings.alerts.telegram_enabled
        muted, send = route(event, settings.notifications, telegram_on)
        if event.paper_trade_id and event.telegram is not False and send and settings.notifications.telegram_portfolio_only \
                and event.payload.get("portfolio_taken") is False:
            send = False
        row = {
            "recipient": event.recipient, "event_id": event.event_id, "event_type": event.event_type,
            "category": event.category, "severity": event.severity, "title": event.title, "message": event.message,
            "payload": event.payload, "symbol": event.symbol, "timeframe": event.timeframe,
            "strategy_id": event.strategy_id, "strategy_version": event.strategy_version, "signal_id": event.signal_id,
            "paper_trade_id": event.paper_trade_id, "incident_id": event.incident_id, "group_key": event.group_key,
            "is_group_summary": event.is_group_summary, "muted": muted,
            "delivery_status": "TELEGRAM_PENDING" if send else ("MUTED" if muted else "IN_APP"),
            "occurred_at": event.occurred_at, "created_at": now, "read_at": None,
        }
        async with session.begin_nested():
            stmt = upsert_stmt(session, NotificationEvent, [row], ["recipient", "event_id"]).returning(NotificationEvent.id)
            nid = (await session.execute(stmt)).scalar_one_or_none()
            if nid is None:
                return None  # idempotent: this event was already recorded
            if send:
                # A signal alert is sent synchronously by the signal service; other events go straight to the worker.
                wait = SYNC_SEND_GRACE if event.event_type == "SIGNAL_QUALIFIED" else timedelta(0)
                session.add(NotificationDelivery(
                    notification_id=nid, channel=CHANNEL_TELEGRAM, status="PENDING", attempts=0,
                    message=telegram_message(event, settings.notifications.timezone), next_attempt_at=now + wait,
                    not_after=event.telegram_not_after or now + DEFAULT_TELEGRAM_TTL, created_at=now, updated_at=now))
        return nid

    async def emit(self, ev: EventIn | dict[str, Any]) -> int | None:
        """Store and announce an event with no accompanying domain write. If the database is
        unavailable the event is buffered in memory and retried by the worker."""
        event = self._validate(ev)
        if event is None:
            return None
        try:
            async with self._session_factory()() as session:
                nid = await self._insert(session, event)
                await session.commit()
        except Exception:  # noqa: BLE001
            self.stats["store_failures"] += 1
            self._buffer.append(event)
            self.stats["buffered"] = len(self._buffer)
            log.exception("notification buffered: database write failed", extra={"event_id": event.event_id})
            return None
        await self.publish()
        return nid

    async def flush_buffer(self) -> None:
        while self._buffer:
            event = self._buffer[0]
            try:
                async with self._session_factory()() as session:
                    await self._insert(session, event)
                    await session.commit()
            except Exception:  # noqa: BLE001 - still down; try again next round
                return
            self._buffer.popleft()
            self.stats["buffered"] = len(self._buffer)

    # ------------------------------------------------------------------ real-time announcements
    async def publish(self) -> None:
        """Announce rows committed since the last call. Clients treat this as a hint and read
        from the API, so a lost message costs nothing: they re-sync on reconnect."""
        try:
            async with self._session_factory()() as session:
                if self._last_published is None:
                    self._last_published = 0
                rows = (await session.execute(
                    select(NotificationEvent).where(NotificationEvent.id > self._last_published,
                                                    NotificationEvent.recipient == LOCAL_RECIPIENT)
                    .order_by(NotificationEvent.id).limit(200))).scalars().all()
            if not rows:
                return
            self._last_published = rows[-1].id
            unread = await self.unread_count()
            for r in rows:
                if not r.muted:
                    await self.hub.broadcast("notification", {"event": event_to_dict(r), "unread": unread})
            self._wake.set()
        except Exception:  # noqa: BLE001
            log.exception("notification publish failed")

    async def sync_cursor(self) -> None:
        """Start announcing from the current end (earlier rows are read from the database)."""
        async with self._session_factory()() as session:
            self._last_published = (await session.execute(select(func.max(NotificationEvent.id)))).scalar() or 0

    # ------------------------------------------------------------------ reading
    def _collapse(self, q, category, strategy, severity):
        """Grouping is applied when reading, so individual records always stay available. With a
        category, strategy or severity filter the individual records are what the user asks for."""
        if category or strategy or severity:
            return q.where(NotificationEvent.event_type != "STRATEGY_EVALUATION")
        if self.prefs.grouping.enabled:
            return q.where(or_(NotificationEvent.group_key.is_(None), NotificationEvent.is_group_summary.is_(True)))
        return q.where(NotificationEvent.is_group_summary.is_(False))

    async def list_events(self, recipient: str, *, category: str | None = None, symbol: str | None = None,
                          strategy: str | None = None, timeframe: str | None = None, severity: str | None = None,
                          read: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
                          after_id: int | None = None, include_muted: bool = False, limit: int = 50,
                          offset: int = 0) -> dict[str, Any]:
        q = select(NotificationEvent).where(NotificationEvent.recipient == recipient)
        q = self._collapse(q, category, strategy, severity)
        if not include_muted:
            q = q.where(NotificationEvent.muted.is_(False))
        if category:
            q = q.where(NotificationEvent.category == category)
        if symbol:
            q = q.where(NotificationEvent.symbol == symbol)
        if strategy:
            q = q.where(NotificationEvent.strategy_id == strategy)
        if timeframe:
            q = q.where(NotificationEvent.timeframe == timeframe)
        if severity:
            q = q.where(NotificationEvent.severity == severity)
        if read == "unread":
            q = q.where(NotificationEvent.read_at.is_(None))
        elif read == "read":
            q = q.where(NotificationEvent.read_at.is_not(None))
        if date_from:
            q = q.where(NotificationEvent.occurred_at >= date_from)
        if date_to:
            q = q.where(NotificationEvent.occurred_at <= date_to)
        if after_id is not None:
            q = q.where(NotificationEvent.id > after_id)
        async with self._session_factory()() as session:
            total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(q.order_by(NotificationEvent.occurred_at.desc(), NotificationEvent.id.desc())
                                          .limit(limit).offset(offset))).scalars().all()
        return {"total": total, "items": [event_to_dict(r) for r in rows], "unread": await self.unread_count(recipient)}

    async def unread_count(self, recipient: str = LOCAL_RECIPIENT) -> dict[str, int]:
        q = select(NotificationEvent.severity, func.count()).where(
            NotificationEvent.recipient == recipient, NotificationEvent.read_at.is_(None),
            NotificationEvent.muted.is_(False))
        q = self._collapse(q, None, None, None).group_by(NotificationEvent.severity)
        async with self._session_factory()() as session:
            counts = dict((await session.execute(q)).all())
        return {"total": sum(counts.values()), "critical": counts.get("CRITICAL", 0)}

    async def get_event(self, recipient: str, event_id: int) -> dict[str, Any] | None:
        async with self._session_factory()() as session:
            e = await session.get(NotificationEvent, event_id)
            if e is None or e.recipient != recipient:
                return None  # another recipient's event looks exactly like a missing one
            out = event_to_dict(e)
            deliveries = (await session.execute(select(NotificationDelivery).where(
                NotificationDelivery.notification_id == e.id))).scalars().all()
            out["deliveries"] = [delivery_to_dict(d) for d in deliveries]
            members: list[dict[str, Any]] = []
            if e.is_group_summary and e.group_key:
                rows = (await session.execute(select(NotificationEvent).where(
                    NotificationEvent.recipient == recipient, NotificationEvent.group_key == e.group_key,
                    NotificationEvent.is_group_summary.is_(False)).order_by(NotificationEvent.strategy_id,
                                                                             NotificationEvent.id))).scalars().all()
                members = [event_to_dict(r) for r in rows]
            out["members"] = members
            out["signal_exists"] = bool(e.signal_id and await session.get(Signal, e.signal_id) is not None)
            if not out["signal_exists"]:
                out["links"] = [lk for lk in out["links"] if "signal" not in lk["href"]]
            if e.paper_trade_id:
                from app.db.models import PaperTrade
                t = await session.get(PaperTrade, e.paper_trade_id)
                if t is not None:
                    out["trade"] = {c.name: (v.isoformat() if isinstance(v := getattr(t, c.name), datetime) else v)
                                    for c in PaperTrade.__table__.columns}
            if e.incident_id:
                inc = await session.get(NotificationIncident, e.incident_id)
                out["incident"] = incident_to_dict(inc) if inc else None
        return out

    async def mark_read(self, recipient: str, event_id: int, read: bool = True) -> dict[str, Any] | None:
        now = utcnow()
        async with self._session_factory()() as session:
            e = await session.get(NotificationEvent, event_id)
            if e is None or e.recipient != recipient:
                return None
            cond = [NotificationEvent.id == e.id]
            if e.is_group_summary and e.group_key:  # reading a group covers its members
                cond.append(and_(NotificationEvent.group_key == e.group_key, NotificationEvent.recipient == recipient))
            await session.execute(update(NotificationEvent).where(or_(*cond)).values(read_at=now if read else None))
            await session.commit()
        unread = await self.unread_count(recipient)
        await self.hub.broadcast("notification_read", {"ids": [event_id], "read": read, "unread": unread})
        return {"id": event_id, "read": read, "unread": unread}

    async def mark_all_read(self, recipient: str, category: str | None = None) -> dict[str, Any]:
        q = update(NotificationEvent).where(NotificationEvent.recipient == recipient, NotificationEvent.read_at.is_(None))
        if category:
            q = q.where(NotificationEvent.category == category)
        async with self._session_factory()() as session:
            res = await session.execute(q.values(read_at=utcnow()))
            await session.commit()
        unread = await self.unread_count(recipient)
        await self.hub.broadcast("notification_read", {"ids": "all", "read": True, "unread": unread})
        return {"updated": res.rowcount, "unread": unread}

    async def facets(self, recipient: str) -> dict[str, list[str]]:
        async with self._session_factory()() as session:
            symbols = (await session.execute(select(NotificationEvent.symbol).where(
                NotificationEvent.recipient == recipient, NotificationEvent.symbol.is_not(None)).distinct())).scalars().all()
            tfs = (await session.execute(select(NotificationEvent.timeframe).where(
                NotificationEvent.recipient == recipient, NotificationEvent.timeframe.is_not(None)).distinct())).scalars().all()
        return {"symbols": sorted(symbols), "timeframes": sorted(tfs), "event_types": sorted(TAXONOMY)}

    # ------------------------------------------------------------------ Telegram outbox
    async def _update_event_delivery(self, session, nid: int, status: str) -> None:
        await session.execute(update(NotificationEvent).where(NotificationEvent.id == nid).values(delivery_status=status))

    async def suppress_signal_delivery(self, signal_id: str, reason: str) -> None:
        """The signal service decided not to alert (below score threshold, too old, blocked)."""
        async with self._session_factory()() as session:
            d = await self._delivery_for_signal(session, signal_id)
            if d is None or d.status not in ("PENDING", "RETRY"):
                return
            d.status, d.last_error, d.updated_at = "SUPPRESSED", reason, utcnow()
            await self._update_event_delivery(session, d.notification_id, f"TELEGRAM_{reason}"[:24])
            await session.commit()

    async def _delivery_for_signal(self, session, signal_id: str) -> NotificationDelivery | None:
        return (await session.execute(select(NotificationDelivery).join(
            NotificationEvent, NotificationEvent.id == NotificationDelivery.notification_id).where(
            NotificationEvent.event_type == "SIGNAL_QUALIFIED", NotificationEvent.signal_id == signal_id,
            NotificationDelivery.channel == CHANNEL_TELEGRAM))).scalar_one_or_none()

    async def deliver_signal(self, signal_id: str, now: datetime | None = None) -> SendResult | None:
        """Send a signal's alert now, judging its age by the caller's (exchange-synchronised) clock.
        None means there is nothing to send (muted by preference, or already claimed), so the alert
        can never go out twice."""
        async with self._session_factory()() as session:
            d = await self._delivery_for_signal(session, signal_id)
        return await self._deliver(d.id, now) if d is not None else None

    async def deliver_pending(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        async with self._session_factory()() as session:
            # A crash mid-send leaves SENDING behind. Telegram may or may not have received it; retrying
            # favours delivery over silence for an alert, and the event row is never duplicated.
            await session.execute(update(NotificationDelivery).where(
                NotificationDelivery.status == "SENDING", NotificationDelivery.updated_at < now - STALE_SENDING
            ).values(status="RETRY", updated_at=now))
            await session.commit()
            ids = (await session.execute(select(NotificationDelivery.id).where(
                NotificationDelivery.status.in_(("PENDING", "RETRY")), NotificationDelivery.next_attempt_at <= now
            ).order_by(NotificationDelivery.id).limit(20))).scalars().all()
        for did in ids:
            await self._deliver(did, now)
        return len(ids)

    async def _deliver(self, delivery_id: int, now: datetime | None = None) -> SendResult | None:
        now = now or utcnow()
        async with self._session_factory()() as session:
            claimed = await session.execute(update(NotificationDelivery).where(
                NotificationDelivery.id == delivery_id, NotificationDelivery.status.in_(("PENDING", "RETRY"))
            ).values(status="SENDING", attempts=NotificationDelivery.attempts + 1, updated_at=now))
            await session.commit()
            if claimed.rowcount != 1:
                return None
            d = await session.get(NotificationDelivery, delivery_id)
            assert d is not None
            e = await session.get(NotificationEvent, d.notification_id)
            assert e is not None
            message, attempts, not_after, nid = d.message, d.attempts, d.not_after, d.notification_id
            legacy_signal = e.signal_id if e.event_type == "SIGNAL_QUALIFIED" else None
            kind = "signal" if legacy_signal else "event"
            title = e.title
            if not_after is not None and now > not_after:
                d.status, d.updated_at = "EXPIRED", now
                await self._update_event_delivery(session, nid, "TELEGRAM_EXPIRED")
                await session.commit()
                return None
        result = await self._send(message)
        await self._record_result(delivery_id, nid, attempts, result, message, legacy_signal, kind, now)
        if result.ok:
            await self._retry_waiting_now(now)
            await self.incident_resolve(TELEGRAM_INCIDENT, "Telegram delivery restored",
                                        "Telegram messages are being delivered again.", now)
        else:
            await self.incident_open(TELEGRAM_INCIDENT, "Telegram delivery failing",
                                     f"Could not deliver: {title}. Error: {result.error}", "WARNING", now, telegram=False)
        return result

    async def _retry_waiting_now(self, now: datetime) -> None:
        """Telegram works again, so alerts waiting out a long backoff should not wait any longer."""
        async with self._session_factory()() as session:
            await session.execute(update(NotificationDelivery).where(
                NotificationDelivery.status == "RETRY", NotificationDelivery.next_attempt_at > now).values(next_attempt_at=now))
            await session.commit()

    async def _send(self, message: str) -> SendResult:
        if self.telegram is None:
            return SendResult(False, 0, "Telegram is not configured")
        try:
            return await self.telegram.send_message(message)
        except Exception as exc:  # noqa: BLE001 - delivery problems never reach signal generation
            return SendResult(False, 1, redact(f"{type(exc).__name__}: {exc}"))

    async def _record_result(self, delivery_id: int, nid: int, attempts: int, result: SendResult, message: str,
                             legacy_signal: str | None, kind: str, now: datetime) -> None:
        async with self._session_factory()() as session:
            d = await session.get(NotificationDelivery, delivery_id)
            assert d is not None
            d.updated_at, d.last_error = utcnow(), redact(result.error) if result.error else None
            if result.ok:
                d.status, d.sent_at = "SENT", utcnow()
                status = "TELEGRAM_SENT"
            elif self.telegram is None:
                d.status = "FAILED"
                status = "TELEGRAM_FAILED"
            else:
                d.status = "RETRY"
                d.next_attempt_at = now + timedelta(seconds=RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)])
                status = "TELEGRAM_RETRY"
            await self._update_event_delivery(session, nid, status)
            session.add(Notification(signal_id=legacy_signal, channel="telegram", kind=kind,
                                     status="SENT" if result.ok else "FAILED", attempts=result.attempts,
                                     error=result.error, message=message, sent_at=utcnow() if result.ok else None))
            if legacy_signal:  # a later retry may succeed after the signal service reported FAILED
                await session.execute(update(Signal).where(Signal.id == legacy_signal).values(
                    notification_status="SENT" if result.ok else "FAILED"))
            await session.commit()

    # ------------------------------------------------------------------ incidents
    async def incident_open(self, key: str, title: str, message: str, severity: str, now: datetime | None = None,
                            category: str = "system", event_type: str = "INCIDENT_OPENED", telegram: bool | None = None,
                            data: dict[str, Any] | None = None, started_at: datetime | None = None) -> None:
        """Open an incident, or just update the existing one: retries add no notification.
        A notification is only added if the severity rises."""
        now = now or utcnow()
        try:
            async with self._session_factory()() as session:
                inc = (await session.execute(select(NotificationIncident).where(
                    NotificationIncident.key == key, NotificationIncident.status == "OPEN"))).scalars().first()
                new = inc is None
                escalated = False
                if inc is None:
                    inc = NotificationIncident(key=key, status="OPEN", category=category, severity=severity, title=title,
                                               message=message, occurrences=1, started_at=started_at or now, last_seen_at=now,
                                               data=data or {})
                    session.add(inc)
                else:
                    escalated = SEVERITY_RANK[severity] > SEVERITY_RANK[inc.severity]
                    inc.occurrences += 1
                    inc.last_seen_at, inc.message = now, message
                    inc.title = title
                    if escalated:
                        inc.severity = severity
                await session.flush()
                if new or escalated:
                    etype = event_type if new else "INCIDENT_ESCALATED"
                    suffix = "opened" if new else f"escalated:{severity}"
                    await self._insert(session, EventIn(
                        event_id=f"incident:{key}:{inc.started_at.isoformat()}:{suffix}", event_type=etype, title=title,
                        message=message, severity=severity, category=category, incident_id=inc.id, occurred_at=now,  # type: ignore[arg-type]
                        payload={"incident_key": key, **(data or {})}, telegram=telegram))
                await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("incident could not be recorded", extra={"key": key})
            return
        await self.publish()

    async def incident_resolve(self, key: str, title: str, message: str, now: datetime | None = None) -> None:
        now = now or utcnow()
        try:
            async with self._session_factory()() as session:
                inc = (await session.execute(select(NotificationIncident).where(
                    NotificationIncident.key == key, NotificationIncident.status == "OPEN"))).scalars().first()
                if inc is None:
                    return
                inc.status, inc.resolved_at, inc.last_seen_at = "RESOLVED", now, now
                # Whoever was told about a critical problem is also told when it is over.
                telegram = (True if inc.severity == "CRITICAL" else None) if key != TELEGRAM_INCIDENT else False
                await self._insert(session, EventIn(
                    event_id=f"incident:{key}:{inc.started_at.isoformat()}:resolved", event_type="INCIDENT_RESOLVED",
                    title=title, message=message, category=inc.category, incident_id=inc.id, occurred_at=now,
                    payload={"incident_key": key, "started_at": inc.started_at, "occurrences": inc.occurrences},
                    telegram=telegram))
                await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("incident could not be resolved", extra={"key": key})
            return
        await self.publish()

    async def incidents(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q = select(NotificationIncident).order_by(NotificationIncident.started_at.desc()).limit(limit)
        if status:
            q = q.where(NotificationIncident.status == status)
        async with self._session_factory()() as session:
            return [incident_to_dict(i) for i in (await session.execute(q)).scalars().all()]

    # ------------------------------------------------------------------ grouping
    async def group_correlated(self, corr: Callable[[str, str], float] | None, threshold: float,
                               now: datetime | None = None) -> None:
        """Summarise bursts of same-direction qualified signals on correlated coins. Coins with
        unknown or low correlation are never grouped, however close together they fire. Individual
        signal notifications stay available and keep their Telegram alert."""
        g = self.prefs.grouping
        if not g.enabled or corr is None:
            return
        now = now or utcnow()
        since = now - timedelta(minutes=g.correlated_window_minutes)
        try:
            async with self._session_factory()() as session:
                rows = (await session.execute(select(NotificationEvent).where(
                    NotificationEvent.recipient == LOCAL_RECIPIENT, NotificationEvent.event_type == "SIGNAL_QUALIFIED",
                    NotificationEvent.created_at >= since).order_by(NotificationEvent.id))).scalars().all()
                changed = False
                for direction in ("LONG", "SHORT"):
                    same = [r for r in rows if r.payload.get("direction") == direction and r.symbol]
                    for comp in _correlated_components(same, corr, threshold):
                        symbols = sorted({r.symbol for r in comp})
                        if len(symbols) < g.correlated_min_signals:
                            continue
                        key = next((r.group_key for r in comp if r.group_key), None) or \
                            f"market-group:{direction}:{comp[0].created_at.isoformat()}"
                        if all(r.group_key == key for r in comp):
                            continue  # nothing new in this group
                        summary = correlated_summary(direction, key, symbols, threshold, comp[-1].occurred_at)
                        if await self._insert(session, summary) is None:
                            await session.execute(update(NotificationEvent).where(
                                NotificationEvent.recipient == LOCAL_RECIPIENT, NotificationEvent.event_id == key
                            ).values(title=summary.title, message=summary.message, payload=summary.payload))
                        for r in comp:
                            r.group_key = key
                        changed = True
                if changed:
                    await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("correlated grouping failed")
            return
        if changed:
            await self.publish()

    # ------------------------------------------------------------------ housekeeping
    async def prune(self, now: datetime | None = None) -> int:
        """Apply the retention preference. CRITICAL events and the incident table are kept."""
        now = now or utcnow()
        cutoff = now - timedelta(days=self.prefs.retention_days)
        async with self._session_factory()() as session:
            old = select(NotificationEvent.id).where(NotificationEvent.created_at < cutoff,
                                                     NotificationEvent.severity != "CRITICAL")
            await session.execute(delete(NotificationDelivery).where(NotificationDelivery.notification_id.in_(old)))
            res = await session.execute(delete(NotificationEvent).where(NotificationEvent.id.in_(old)))
            await session.commit()
        return res.rowcount

    async def run(self, interval: float = 5.0) -> None:
        """Worker: re-store buffered events, announce new rows, deliver the outbox, prune hourly."""
        await self.sync_cursor()
        while True:
            try:
                await self.flush_buffer()
                await self.publish()
                await self.deliver_pending()
                now = utcnow()
                self.heartbeat_at = now
                if self._last_prune is None or now - self._last_prune > timedelta(hours=1):
                    self._last_prune = now
                    await self.prune(now)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the worker alive
                log.exception("notification worker cycle failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except TimeoutError:
                pass


def _correlated_components(rows: list[NotificationEvent], corr: Callable[[str, str], float],
                           threshold: float) -> list[list[NotificationEvent]]:
    """Connected components of signals whose coins correlate at or above ``threshold``."""
    comps: list[list[NotificationEvent]] = []
    for r in rows:
        linked = []
        for comp in comps:
            for other in comp:
                c = 1.0 if other.symbol == r.symbol else corr(r.symbol, other.symbol)  # type: ignore[arg-type]
                if c == c and c >= threshold:  # NaN (unknown correlation) never links
                    linked.append(comp)
                    break
        merged = [r]
        for comp in linked:
            merged = comp + merged
            comps.remove(comp)
        comps.append(sorted(merged, key=lambda x: x.id))
    return comps
