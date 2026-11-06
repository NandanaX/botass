"""Event taxonomy and validation of incoming events. Every notification comes from a real
backend event; a malformed one is rejected here and never reaches the database."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.jsonutil import jsonable
from app.notifications.settings import SEVERITY_RANK, NotificationSettings, Severity
from app.timeutil import utcnow

LOCAL_RECIPIENT = "local"  # single-user today; the column allows per-user scoping later
MAX_PAYLOAD_BYTES = 64_000
_SECRET_MARKERS = ("token", "secret", "password", "api_key", "apikey", "credential", "chat_id")

# event type -> (category, default severity, Telegram default). "critical" = only when CRITICAL.
TAXONOMY: dict[str, tuple[str, str, bool | str]] = {
    "SIGNAL_DETECTED": ("signal", "INFO", False),
    "SIGNAL_QUALIFIED": ("signal", "SUCCESS", True),
    "WATCHLIST_DIGEST": ("signal", "INFO", True),  # early look on Telegram; never a signal
    "SIGNAL_REJECTED": ("signal", "INFO", False),
    "SIGNAL_EXPIRED": ("signal", "INFO", False),
    "SIGNAL_INVALIDATED": ("signal", "INFO", False),
    "STRATEGY_D_QUALIFIED": ("signal", "INFO", False),  # research shadow; Telegram needs strategy_d_telegram
    "STRATEGY_D_WATCHLIST": ("signal", "INFO", False),
    "STRATEGY_D_HALTED": ("risk", "WARNING", False),  # research account; in-app only
    "STRATEGY_D_RESET": ("risk", "WARNING", False),
    "STRATEGY_EVALUATION": ("trade", "INFO", False),
    "MARKET_SIGNAL_GROUP": ("market", "INFO", False),
    "MARKET_PRICE_ALERT": ("market", "INFO", False),  # Telegram is sent by the price-alert service
    "PAPER_ENTRY_FILLED": ("trade", "INFO", False),
    "PAPER_ENTRY_MISSED": ("trade", "WARNING", False),
    "PAPER_ORDER_UNFILLED": ("trade", "INFO", False),
    "PAPER_TP1_HIT": ("trade", "SUCCESS", True),
    "PAPER_TP2_HIT": ("trade", "SUCCESS", True),
    "PAPER_TP3_HIT": ("trade", "SUCCESS", True),
    "PAPER_STOP_LOSS_HIT": ("trade", "WARNING", True),
    "PAPER_TRADE_CLOSED": ("trade", "INFO", True),
    "RISK_MAX_OPEN_RISK": ("risk", "WARNING", "critical"),
    "RISK_CORRELATED_REJECTED": ("risk", "INFO", "critical"),
    "RISK_DAILY_LOSS_LIMIT": ("risk", "CRITICAL", "critical"),
    "RISK_DRAWDOWN_LIMIT": ("risk", "CRITICAL", "critical"),
    "CIRCUIT_BREAKER_RESET": ("risk", "WARNING", True),
    "RISK_STALE_DATA": ("risk", "WARNING", "critical"),
    "RISK_MISSING_PRICE": ("risk", "WARNING", "critical"),
    "INCIDENT_OPENED": ("system", "WARNING", "critical"),
    "INCIDENT_ESCALATED": ("system", "CRITICAL", "critical"),
    "INCIDENT_RESOLVED": ("system", "SUCCESS", False),
    "VALIDATION_DECISION": ("validation", "INFO", False),
    "VALIDATION_PERIOD_STARTED": ("validation", "INFO", False),
    "VALIDATION_READY_FOR_REVIEW": ("validation", "SUCCESS", False),
}


def _scrub(value: Any) -> Any:
    """Drop anything credential-like from a structured payload, however deeply nested."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()
                if not any(m in str(k).lower() for m in _SECRET_MARKERS)}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


class EventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=200, description="idempotency key; never derived from the title")
    event_type: str
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(default="", max_length=4000)
    category: str | None = None
    severity: Severity | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    recipient: str = LOCAL_RECIPIENT
    symbol: str | None = Field(default=None, max_length=32)
    timeframe: str | None = Field(default=None, max_length=8)
    strategy_id: str | None = Field(default=None, max_length=8)
    strategy_version: str | None = Field(default=None, max_length=80)
    signal_id: str | None = Field(default=None, max_length=64)
    paper_trade_id: str | None = Field(default=None, max_length=64)
    incident_id: int | None = None
    group_key: str | None = Field(default=None, max_length=200)
    is_group_summary: bool = False
    occurred_at: datetime = Field(default_factory=utcnow)
    telegram: bool | None = Field(default=None, description="None = follow preferences; False = never via the outbox")
    telegram_html: str | None = None
    telegram_not_after: datetime | None = None

    @field_validator("event_type")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in TAXONOMY:
            raise ValueError(f"unknown event type {v!r}")
        return v

    @field_validator("strategy_id")
    @classmethod
    def _strategy(cls, v: str | None) -> str | None:
        if v is not None and v not in ("A", "B", "C", "D"):
            raise ValueError("strategy must be A, B, C or D")
        return v

    @field_validator("payload")
    @classmethod
    def _payload(cls, v: dict[str, Any]) -> dict[str, Any]:
        clean = _scrub(jsonable(v))
        if len(json.dumps(clean, allow_nan=False)) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload too large")
        return clean

    @model_validator(mode="after")
    def _defaults(self) -> EventIn:
        category, severity, _ = TAXONOMY[self.event_type]
        self.category = self.category or category
        self.severity = self.severity or severity  # type: ignore[assignment]
        return self


def route(ev: EventIn, prefs: NotificationSettings, telegram_on: bool) -> tuple[bool, bool]:
    """Decide (muted, send to Telegram). Everything is recorded either way: muting only hides an
    event from the feed and the unread count, so critical incidents always stay in the history."""
    cat = prefs.categories.get(ev.category)  # type: ignore[call-overload]
    rank = SEVERITY_RANK[ev.severity or "INFO"]
    passes = ((not prefs.strategies or ev.strategy_id is None or ev.strategy_id in prefs.strategies)
              and (not prefs.symbols or ev.symbol is None or ev.symbol in prefs.symbols)
              and (not prefs.timeframes or ev.timeframe is None or ev.timeframe in prefs.timeframes)
              and rank >= SEVERITY_RANK[prefs.min_severity])
    if cat is not None:
        passes = passes and rank >= SEVERITY_RANK[cat.min_severity]
    muted = (not passes or not prefs.in_app_enabled or (cat is not None and not cat.in_app)
             or ev.event_type in prefs.muted_events)
    send = False
    if telegram_on and passes and ev.telegram is not False and (cat is None or cat.telegram):
        default = TAXONOMY[ev.event_type][2]
        rule = prefs.telegram_events.get(ev.event_type, default)
        send = ev.telegram is True or rule is True or (rule == "critical" and ev.severity == "CRITICAL")
    return muted, send
