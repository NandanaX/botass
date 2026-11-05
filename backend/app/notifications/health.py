"""System-health monitor: turns operational conditions into grouped incidents.

Each condition has a grace period before it opens an incident, so short blips and reconnects
produce no notification. An ongoing problem updates one incident rather than alerting on every
retry, and recovery closes it with a single "restored" notification. A monitor failure is logged
and never reaches the scanner: they are separate tasks that share nothing but read access.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.db.models import AppSetting, NotificationDelivery, PaperTrade
from app.db.session import upsert_stmt
from app.exchanges.bybit import ExchangeError
from app.notifications.messages import validation_ready_event
from app.notifications.service import NotificationService
from app.timeutil import utcnow

log = logging.getLogger(__name__)

CHECK_INTERVAL = 30.0
WS_GRACE = timedelta(seconds=60)
WS_SILENCE = timedelta(seconds=60)
WS_FLAP_WINDOW = timedelta(minutes=10)
WS_FLAP_RECONNECTS = 5
ALL_STALE_GRACE = timedelta(minutes=3)
SYMBOL_STALE_GRACE = timedelta(minutes=2)
SCANNER_MIN_GRACE = timedelta(minutes=3)
READINESS_EVERY = timedelta(hours=1)
PROBE_EVERY = timedelta(seconds=60)
REST_GRACE = timedelta(minutes=3)
REST_CRITICAL_AFTER = timedelta(minutes=10)
BACKLOG_AGE = timedelta(minutes=10)
WORKER_STALE = timedelta(minutes=2)
MAX_CLOCK_SKEW_S = 5.0
MAX_CLOCK_SAMPLE_S = 2.0
DB_PROBE_TIMEOUT_S = 10.0
# Longer than this between checks means the whole process stopped, normally because the PC slept.
PAUSE_GAP = timedelta(minutes=3)
RESUME_SETTLE = timedelta(minutes=3)
PROBE_KEY = "health_probe"


def minutes(delta: timedelta) -> str:
    n = max(1, round(delta.total_seconds() / 60))
    return f"{n} minute{'s' if n != 1 else ''}"


@dataclass(slots=True)
class Condition:
    key: str
    grace: timedelta
    title: str
    message: str  # may use {duration}
    severity: str
    recovered_title: str
    recovered_message: str  # may use {duration}
    category: str = "system"
    event_type: str = "INCIDENT_OPENED"
    telegram: bool | None = None


@dataclass(slots=True)
class Probes:
    """Latest independent measurements, read by the System Health page."""

    db_ok: bool | None = None
    db_latency_ms: float | None = None
    db_error: str | None = None
    db_checked_at: datetime | None = None
    rest_ok: bool | None = None
    rest_latency_ms: float | None = None
    rest_error: str | None = None
    rest_checked_at: datetime | None = None
    rest_last_ok_at: datetime | None = None
    rest_failures: int = 0
    clock_skew_s: float | None = None
    backlog: int | None = None
    pauses: list[dict[str, str]] = field(default_factory=list)


class HealthMonitor:
    def __init__(self, center: NotificationService, market, scanner, forward, settings, env) -> None:
        self.center = center
        self.market = market
        self.scanner = scanner
        self.forward = forward
        self.settings = settings
        self.env = env
        self.probes = Probes()
        self.tasks: list[asyncio.Task] = []
        self.heartbeat_at: datetime | None = None
        self._since: dict[str, datetime] = {}
        self._open: dict[str, Condition] = {}
        self._db_down_since: datetime | None = None
        self._last_readiness: datetime | None = None
        self._last_check: datetime | None = None
        self._settle_until: datetime | None = None
        self._last_probe: datetime | None = None
        self._rest_down_since: datetime | None = None
        self._ws_history: list[tuple[datetime, int]] = []

    async def _track(self, cond: Condition, active: bool, now: datetime) -> None:
        if active:
            since = self._since.setdefault(cond.key, now)
            if now - since < cond.grace:
                return
            await self.center.incident_open(cond.key, cond.title, cond.message.format(duration=minutes(now - since)),
                                            cond.severity, now, category=cond.category, event_type=cond.event_type,
                                            started_at=since, telegram=cond.telegram)
            self._open[cond.key] = cond
            return
        since = self._since.pop(cond.key, None)
        was_open = self._open.pop(cond.key, None)
        if was_open is not None:
            elapsed = minutes(now - since) if since else "a while"
            await self.center.incident_resolve(cond.key, was_open.recovered_title,
                                               was_open.recovered_message.format(duration=elapsed), now)

    async def _restore(self) -> None:
        """Pick up incidents left open by the previous process, so that a recovery after a
        restart closes the same incident instead of leaving it open for ever."""
        for inc in await self.center.incidents("OPEN"):
            key = inc["key"]
            self._since.setdefault(key, datetime.fromisoformat(inc["started_at"]))
            self._open.setdefault(key, Condition(key, timedelta(0), inc["title"], inc["message"], inc["severity"],
                                                 f"{inc['title']}: recovered", "Recovered after {duration}.", inc["category"]))

    def _conditions(self, now: datetime) -> list[tuple[Condition, bool]]:
        state = self.market.state
        watchlist = self.settings.current.watchlist
        missing = [s for s in watchlist if not state.ticker_view(s).get("available")]
        stale = [s for s in watchlist if state.is_stale(s, now)]
        all_stale = bool(watchlist) and len(stale) == len(watchlist)
        out: list[tuple[Condition, bool]] = [
            (Condition("bybit_market_data", ALL_STALE_GRACE, "Bybit market data unavailable",
                       "Bybit market data unavailable for {duration}.", "CRITICAL", "Bybit market data connection restored",
                       "Bybit market data connection restored after {duration}."), all_stale),
        ]
        if self.env.market_stream_enabled:
            # A connected socket that has stopped delivering data is as down as a closed one.
            last_msg = state.ws_last_message_at
            silent = state.ws_connected and last_msg is not None and now - last_msg > WS_SILENCE
            out.append((Condition("bybit_websocket", WS_GRACE, "Bybit WebSocket disconnected",
                                  "Bybit WebSocket not delivering data for {duration}. Prices continue over the REST fallback where possible.",
                                  "WARNING", "Bybit WebSocket reconnected", "Bybit WebSocket reconnected after {duration}."),
                        not state.ws_connected or silent))
            # A stream that keeps dropping looks fine at any single moment, so count the reconnects.
            self._ws_history = [x for x in self._ws_history if now - x[0] <= WS_FLAP_WINDOW] + [(now, state.ws_reconnects)]
            flaps = state.ws_reconnects - self._ws_history[0][1]
            out.append((Condition("bybit_websocket_unstable", timedelta(0), "Bybit WebSocket keeps reconnecting",
                                  f"The stream reconnected {flaps} times in {minutes(WS_FLAP_WINDOW)}. Prices continue over the REST fallback where possible.",
                                  "WARNING", "Bybit WebSocket stable again", "The stream has been stable again after {duration}."),
                        flaps >= WS_FLAP_RECONNECTS))
        for s in watchlist:
            # A full outage is one incident, not one per coin.
            out.append((Condition(f"missing_price:{s}", SYMBOL_STALE_GRACE, f"{s}: missing exchange price",
                                  f"Bybit has returned no price for {s} for {{duration}}. Paper entries on {s} cannot fill.",
                                  "WARNING", f"{s}: exchange price available", f"{s} prices returned after {{duration}}.",
                                  "risk", "RISK_MISSING_PRICE"), s in missing and not all_stale))
            out.append((Condition(f"stale_feed:{s}", SYMBOL_STALE_GRACE, f"{s}: stale price feed",
                                  f"No fresh {s} price for {{duration}}. Paper risk checks block new entries on stale data.",
                                  "WARNING", f"{s}: price feed fresh again", f"{s} price feed recovered after {{duration}}.",
                                  "risk", "RISK_STALE_DATA"), s in stale and s not in missing and not all_stale))
        if self.env.scanner_enabled and self.scanner.status.get("is_leader"):
            grace = max(SCANNER_MIN_GRACE, timedelta(seconds=self.scanner.interval * 5))
            last = self.scanner.status.get("last_cycle_at")
            late = last is not None and now - datetime.fromisoformat(last) > grace
            # While Bybit is unreachable a cycle is slow because it is waiting on the network. That is
            # already reported as one incident, so it should not also read as the scanner having stopped.
            if self.probes.rest_ok is False:
                late = False
            out.append((Condition("scanner", timedelta(0), "Background scanner stopped",
                                  "The background scanner has not completed a cycle for more than "
                                  f"{minutes(grace)}. No new signals are being generated.", "CRITICAL",
                                  "Background scanner running again", "The scanner is completing cycles again after {duration}."),
                        late or not self.scanner.status.get("running")))
            failing = self.scanner.status.get("cycle_errors") or 0
            out.append((Condition("scanner_errors", timedelta(minutes=2), "Scanner failing on some coins",
                                  f"{failing} coin/timeframe evaluation(s) failing: {str(self.scanner.status.get('last_error'))[:300]}. "
                                  "Other coins continue to be scanned.", "CRITICAL", "Scanner errors cleared",
                                  "Every coin and timeframe is evaluating again after {duration}."), failing > 0))
        err = self.forward.status.get("last_error") if self.forward is not None else None
        out.append((Condition("paper_worker", timedelta(0), "Paper-trading worker failure",
                              f"The paper-trading tracker failed: {str(err)[:300]}", "CRITICAL",
                              "Paper-trading worker recovered", "The paper-trading tracker is working again after {duration}."),
                     bool(err)))
        p = self.probes
        if self._rest_down_since is not None:
            duration = now - self._rest_down_since
            severity = "CRITICAL" if duration >= REST_CRITICAL_AFTER else "WARNING"
        else:
            severity = "WARNING"
        out.append((Condition("bybit_rest", REST_GRACE, "Bybit REST API failing",
                              f"Bybit REST requests have failed for {{duration}} ({str(p.rest_error)[:200]}). Candles cannot refresh, so "
                              "no new signals are created from stale data.", severity, "Bybit REST API restored",
                              "Bybit REST requests are succeeding again after {duration}."), p.rest_ok is False))
        out.append((Condition("clock_skew", timedelta(0), "System clock is off",
                              f"This PC's clock differs from Bybit by {abs(p.clock_skew_s or 0):.0f} seconds. Candle timing uses the exchange "
                              "offset, but quotes and logs use the local clock.", "WARNING", "System clock back in sync",
                              "The clock is within tolerance again after {duration}."),
                     p.clock_skew_s is not None and abs(p.clock_skew_s) > MAX_CLOCK_SKEW_S))
        out.append((Condition("telegram_backlog", timedelta(0), "Telegram messages are backing up",
                              f"{p.backlog} Telegram message(s) have been waiting for more than {minutes(BACKLOG_AGE)}.", "WARNING",
                              "Telegram backlog cleared", "No Telegram messages are waiting after {duration}.", telegram=False),
                     bool(p.backlog)))
        hb = self.center.heartbeat_at
        if self.env.scanner_enabled:
            stalled = hb is not None and now - hb > WORKER_STALE
            out.append((Condition("notification_worker", timedelta(0), "Notification worker stalled",
                                  f"The notification worker has not run for more than {minutes(WORKER_STALE)}. Telegram messages are not being sent.",
                                  "CRITICAL", "Notification worker running again", "The notification worker recovered after {duration}."),
                        stalled))
        for task in self.tasks:
            name = task.get_name()
            crashed = task.done() and not task.cancelled() and task.exception() is not None
            out.append((Condition(f"task:{name}", timedelta(0), f"Background task stopped: {name}",
                                  f"The {name} task ended unexpectedly: {str(task.exception())[:300] if crashed else ''}. Restart the application.",
                                  "CRITICAL", f"Background task running: {name}", "Recovered after {duration}."), crashed))
        return out

    async def _probe_database(self) -> None:
        """A real write: `SELECT 1` succeeds even when the database cannot accept writes."""
        started = time.perf_counter()
        now = utcnow()

        async def write() -> None:
            async with self.center._session_factory()() as session:
                await session.execute(upsert_stmt(session, AppSetting, [{"key": PROBE_KEY, "value": {"at": now.isoformat()}}], ["key"], ["value"]))
                await session.commit()

        await asyncio.wait_for(write(), DB_PROBE_TIMEOUT_S)
        self.probes.db_latency_ms = round((time.perf_counter() - started) * 1000, 1)

    async def _check_database(self, now: datetime) -> bool:
        self.probes.db_checked_at = now
        try:
            await self._probe_database()
        except Exception as exc:  # noqa: BLE001
            self.probes.db_ok = False
            self.probes.db_error = f"{type(exc).__name__}: {exc}"[:300]
            if self._db_down_since is None:
                self._db_down_since = now
                log.error("database not accepting writes; notifications are buffered", extra={"error": str(exc)[:300]})
            return False
        self.probes.db_ok, self.probes.db_error = True, None
        if self._db_down_since is not None:
            # Only recordable now: the incident covers the outage it could not write during.
            down, self._db_down_since = self._db_down_since, None
            await self.center.incident_open("database", "Database persistence failure",
                                            f"Database writes failed for {minutes(now - down)}. Signals and paper records may be missing for that period.",
                                            "CRITICAL", now, started_at=down)
            await self.center.incident_resolve("database", "Database restored",
                                               f"Database connection restored after {minutes(now - down)}.", now)
        return True

    async def _probe_exchange(self, now: datetime) -> None:
        """Independent REST check, which also refreshes the exchange clock offset: measured once at
        startup it would slowly go stale on a machine that runs for weeks."""
        client = getattr(self.market, "client", None)
        if client is None or (self._last_probe is not None and now - self._last_probe < PROBE_EVERY):
            return
        self._last_probe = now
        p = self.probes
        p.rest_checked_at = now
        started = time.perf_counter()
        try:
            server = await client.server_time()
        except (ExchangeError, OSError) as exc:
            p.rest_ok, p.rest_error = False, str(exc)[:300]
            p.rest_failures += 1
            self._rest_down_since = self._rest_down_since or now
            return
        elapsed = time.perf_counter() - started
        p.rest_ok, p.rest_error, p.rest_failures, p.rest_last_ok_at = True, None, 0, now
        p.rest_latency_ms = round(elapsed * 1000, 1)
        self._rest_down_since = None
        if elapsed < MAX_CLOCK_SAMPLE_S:  # a slow reply measures the network and retries, not the clock
            offset = (server - utcnow()).total_seconds() + elapsed / 2
            p.clock_skew_s = round(-offset, 3)
            self.market.clock_offset_seconds = offset

    async def _check_backlog(self, now: datetime) -> None:
        async with self.center._session_factory()() as session:
            self.probes.backlog = (await session.execute(select(func.count()).select_from(NotificationDelivery).where(
                NotificationDelivery.status.in_(("PENDING", "RETRY", "SENDING")),
                NotificationDelivery.created_at < now - BACKLOG_AGE))).scalar_one()

    async def _check_readiness(self, now: datetime) -> None:
        """Promotion-gate transitions. The full gate is expensive, so it is only assessed once a
        variant has the minimum number of closed trades, and at most hourly."""
        if self.forward is None or (self._last_readiness and now - self._last_readiness < READINESS_EVERY):
            return
        self._last_readiness = now
        gate = self.settings.current.validation.gate
        async with self.center._session_factory()() as session:
            closed = dict((await session.execute(select(PaperTrade.variant_key, func.count()).where(
                PaperTrade.status == "CLOSED").group_by(PaperTrade.variant_key))).all())
        if max(closed.values(), default=0) < gate.min_closed_trades:
            return
        overview = await self.forward.overview()
        for key, rep in overview["variants"].items():
            if rep["promotion"]["readiness"] == "READY_FOR_REVIEW":
                v = rep["variant"]
                await self.center.emit(validation_ready_event(key, v["id"], v["version"], now))

    async def _note_pause(self, last: datetime, now: datetime) -> None:
        """The whole app stopped, so nothing else can be trusted to explain it. Say so plainly,
        rather than blaming Bybit or the scanner for a PC that was asleep."""
        gap = now - last
        self.probes.pauses = [*self.probes.pauses[-19:], {"from": last.isoformat(), "to": now.isoformat(), "duration": minutes(gap)}]
        log.warning("application was paused", extra={"from": last.isoformat(), "minutes": round(gap.total_seconds() / 60)})
        title = "Monitoring was paused (PC asleep or app suspended)"
        await self.center.incident_open("app_paused", title,
                                        f"The application did not run for {minutes(gap)}, most likely because this PC was asleep. No market data, "
                                        "signals or paper-trade checks ran during that time. Missed candles are recorded as missed, not as live signals.",
                                        "WARNING", now, started_at=last, telegram=False)
        await self.center.incident_resolve("app_paused", "Monitoring resumed after a pause",
                                           f"The application is running again after {minutes(gap)}.", now)
        # Everything is out of date for a moment by definition: judge it again once it has caught up.
        self._settle_until = now + RESUME_SETTLE
        for key in [k for k in self._since if k not in self._open]:
            self._since.pop(key)

    async def check(self, now: datetime | None = None) -> None:
        explicit = now is not None
        now = now or utcnow()
        last = self._last_check
        self.heartbeat_at = now
        try:
            await self._run_checks(now, last)
        finally:
            # Measured from the end of a check: a slow check (a locked database, a network timeout)
            # is not the app being paused, only time between checks is.
            self._last_check = now if explicit else utcnow()

    async def _run_checks(self, now: datetime, last: datetime | None) -> None:
        if not await self._check_database(now):
            return
        if last is not None and now - last > PAUSE_GAP:
            await self._note_pause(last, now)
        for step in (self._probe_exchange, self._check_backlog, self._check_readiness):
            try:
                await step(now)
            except Exception:  # noqa: BLE001 - one failed check does not stop the others
                log.exception("health check failed", extra={"check": step.__name__})
        if self._settle_until is not None:
            if now < self._settle_until:
                return
            self._settle_until = None
        for cond, active in self._conditions(now):
            await self._track(cond, active, now)

    async def run(self, interval: float = CHECK_INTERVAL) -> None:
        await self._restore()
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the monitor alive
                log.exception("health monitor cycle failed")
            await asyncio.sleep(interval)
