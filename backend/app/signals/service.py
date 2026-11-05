"""Signal persistence (idempotent), alert dispatch and outcome evaluation."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.alerts.telegram import SendResult, TelegramClient, format_signal_message
from app.api.hub import EventHub
from app.config import Settings
from app.db.models import Notification, Signal
from app.db.session import upsert_stmt
from app.jsonutil import jsonable
from app.notifications.messages import signal_not_after, signal_outcome_event, signal_qualified_event
from app.settings_store import RuntimeSettings
from app.signals.evaluation import evaluate_signal_outcome
from app.signals.identity import signal_id
from app.strategy.engine import Evaluation
from app.timeutil import tf_delta, utcnow

log = logging.getLogger(__name__)


def signal_to_dict(s: Signal) -> dict[str, Any]:
    return {
        "id": s.id,
        "exchange": s.exchange,
        "symbol": s.symbol,
        "timeframe": s.timeframe,
        "direction": s.direction,
        "strategy_name": s.strategy_name,
        "strategy_version": s.strategy_version,
        "created_at": s.created_at.isoformat(),
        "candle_open_time": s.candle_open_time.isoformat(),
        "candle_close_time": s.candle_close_time.isoformat(),
        "entry_reference": s.entry_reference,
        "stop_loss": s.stop_loss,
        "take_profit": s.take_profit,
        "risk_reward": s.risk_reward,
        "atr": s.atr,
        "score": s.score,
        "score_breakdown": s.score_breakdown,
        "conditions": s.conditions,
        "indicators": s.indicators,
        "explanation": s.explanation,
        "mtf_confirmed": s.mtf_confirmed,
        "mtf_details": s.mtf_details,
        "strategy_type": s.strategy_type,
        "setup_status": s.setup_status,
        "plan": s.plan,
        "notification_status": s.notification_status,
        "evaluation_status": s.evaluation_status,
        "evaluation": s.evaluation,
        "evaluated_at": s.evaluated_at.isoformat() if s.evaluated_at else None,
    }


def build_signal_row(exchange: str, symbol: str, ev: Evaluation) -> dict[str, Any]:
    setup = ev.setup
    assert setup is not None
    open_time = pd.Timestamp(ev.open_time).to_pydatetime()
    close_time = pd.Timestamp(ev.close_time).to_pydatetime()
    return {
        "id": signal_id(exchange, symbol, ev.timeframe, setup.direction, open_time, setup.strategy_version,
                        setup.strategy_name),
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": ev.timeframe,
        "direction": setup.direction,
        "strategy_name": setup.strategy_name,
        "strategy_version": setup.strategy_version,
        "created_at": utcnow(),
        "candle_open_time": open_time,
        "candle_close_time": close_time,
        "entry_reference": setup.entry_reference,
        "stop_loss": setup.stop_loss,
        "take_profit": setup.take_profit,
        "risk_reward": setup.risk_reward,
        "atr": setup.atr,
        "score": int(setup.score),
        "score_breakdown": jsonable([asdict(c) for c in setup.score_components]),
        "conditions": jsonable([asdict(c) for c in setup.conditions]),
        "indicators": jsonable(ev.indicators),
        "explanation": setup.explanation,
        "mtf_confirmed": None if setup.mtf_confirmed is None else bool(setup.mtf_confirmed),
        "mtf_details": jsonable([asdict(h) for h in setup.mtf_details]),
        "strategy_type": setup.strategy,
        "setup_status": setup.status,
        "plan": jsonable({**setup.plan, "reasons": setup.reasons,
                          "zones": ev.zones, "htf_zones": ev.htf_zones}),
        "notification_status": "PENDING",
        "evaluation_status": "PENDING",
    }


class SignalService:
    def __init__(self, session_factory, hub: EventHub, env: Settings,
                 telegram: TelegramClient | None = None) -> None:
        self._session_factory = session_factory
        self.hub = hub
        self.env = env
        self.telegram = telegram
        self.center = None  # NotificationService (Phase 7), set by the container
        self.settings = None  # SettingsStore, set by the container

    async def persist(self, row: dict[str, Any]) -> bool:
        """Insert the signal; return False if the same signal id already existed. The notification
        commits with it, so a crash right after this cannot lose the alert (the outbox retries it)."""
        async with self._session_factory()() as session:
            stmt = upsert_stmt(session, Signal, [row], ["id"]).returning(Signal.id)
            inserted = (await session.execute(stmt)).scalar_one_or_none()
            if inserted is not None and self.center is not None:
                await self.center.add(session, self._qualified_event(row))
            await session.commit()
        return inserted is not None

    def _qualified_event(self, row: dict[str, Any]):
        message = format_signal_message({**row, "candle_close_time": row["candle_close_time"].strftime("%Y-%m-%d %H:%M")})
        age = self.settings.current.alerts.max_signal_age_candles if self.settings is not None else 1
        return signal_qualified_event(row, message, signal_not_after(row["candle_close_time"], tf_delta(row["timeframe"]), age))

    async def handle_setup(self, exchange: str, symbol: str, ev: Evaluation, settings: RuntimeSettings,
                           now: datetime, decision: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Persist and alert the best QUALIFIED setup of ``ev`` (``ev.setup``). ``decision`` is
        the decision engine's verdict (stored with the plan; blocking only in enforce mode)."""
        if ev.setup is None or ev.setup.status != "QUALIFIED":
            return None
        row = build_signal_row(exchange, symbol, ev)
        if decision is not None:
            row["plan"] = {**row["plan"], "decision": jsonable(decision)}
        if not await self.persist(row):
            log.info("duplicate signal ignored", extra={"signal_id": row["id"]})
            return None
        age = now - row["candle_close_time"]
        status = await self._dispatch(row, settings, too_old=age > tf_delta(ev.timeframe) *
                                      settings.alerts.max_signal_age_candles, now=now)
        if self.center is not None and status not in ("SENT", "FAILED"):
            await self.center.suppress_signal_delivery(row["id"], status)
        async with self._session_factory()() as session:
            sig = await session.get(Signal, row["id"])
            assert sig is not None
            sig.notification_status = status
            await session.commit()
            data = signal_to_dict(sig)
        log.info("signal created", extra={"signal_id": row["id"], "symbol": symbol, "timeframe": ev.timeframe,
                                          "direction": row["direction"], "score": row["score"],
                                          "notification": status})
        await self.hub.broadcast("signal", data)
        if self.center is not None:
            await self.center.publish()
        return data

    async def _dispatch(self, row: dict[str, Any], settings: RuntimeSettings, too_old: bool,
                        now: datetime | None = None) -> str:
        if not settings.alerts.telegram_enabled:
            return "DISABLED"
        if self.telegram is None:
            return "NOT_CONFIGURED"
        if row["score"] < settings.alerts.min_score:
            return "BELOW_THRESHOLD"
        if too_old:
            return "SKIPPED_STALE"
        decision = (row.get("plan") or {}).get("decision") or {}
        if decision.get("mode") == "enforce" and decision.get("outcome") != "QUALIFIED":
            return "BLOCKED_BY_DECISION"
        if self.center is not None:
            sent = await self.center.deliver_signal(row["id"], now)
            if sent is None:  # muted by notification preferences
                return "DISABLED"
            return "SENT" if sent.ok else "FAILED"
        message = format_signal_message({**row, "candle_close_time": row["candle_close_time"].strftime("%Y-%m-%d %H:%M")})
        result = await self.telegram.send_message(message)
        await self._log_notification(row["id"], "signal", message, result)
        return "SENT" if result.ok else "FAILED"

    async def _log_notification(self, signal_id_: str | None, kind: str, message: str, result: SendResult) -> None:
        async with self._session_factory()() as session:
            session.add(Notification(
                signal_id=signal_id_, channel="telegram", kind=kind, status="SENT" if result.ok else "FAILED",
                attempts=result.attempts, error=result.error, message=message,
                sent_at=utcnow() if result.ok else None))
            await session.commit()

    async def send_test(self) -> SendResult:
        if self.telegram is None:
            return SendResult(False, 0, "Telegram is not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
        message = ("✅ <b>CryptoSignal AI</b> test notification.\n"
                   f"Sent {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC. Alerts are delivered to this chat.")
        result = await self.telegram.send_message(message)
        await self._log_notification(None, "test", message, result)
        return result

    async def evaluate_pending(self, symbol: str, timeframe: str, candles: pd.DataFrame, max_bars: int,
                               fee_pct: float = 0.055, slippage_pct: float = 0.02) -> int:
        """Apply the outcome methodology to PENDING signals of one symbol/timeframe."""
        if candles.empty:
            return 0
        updated = 0
        async with self._session_factory()() as session:
            pending = (await session.execute(select(Signal).where(
                Signal.symbol == symbol, Signal.timeframe == timeframe, Signal.evaluation_status == "PENDING"
            ))).scalars().all()
            for sig in pending:
                if sig.candle_open_time < candles["open_time"].iloc[0]:
                    # Older than the cached window; evaluation needs candles from the DB.
                    continue
                result = evaluate_signal_outcome(sig.direction, pd.Timestamp(sig.candle_open_time), sig.stop_loss,
                                                 sig.take_profit, candles, max_bars, sig.plan, fee_pct, slippage_pct)
                if result is None:
                    continue
                sig.evaluation_status = result["status"]
                if result["status"] == "INVALIDATED":
                    sig.setup_status = "EXPIRED"  # the hypothetical entry never became valid
                sig.evaluation = result
                sig.evaluated_at = utcnow()
                updated += 1
                if self.center is not None and result["status"] in ("INVALIDATED", "EXPIRED"):
                    await self.center.add(session, signal_outcome_event(sig, result["status"], result.get("note")))
            await session.commit()
        if updated and self.center is not None:
            await self.center.publish()
        return updated
