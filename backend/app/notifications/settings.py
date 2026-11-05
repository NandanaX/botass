"""User preferences of the notification center (part of the runtime settings). Secrets are
never stored here; ``alerts.telegram_enabled`` stays the master switch for Telegram."""

from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

Severity = Literal["INFO", "SUCCESS", "WARNING", "CRITICAL"]
Category = Literal["signal", "trade", "risk", "market", "system", "validation"]

SEVERITY_RANK: dict[str, int] = {"INFO": 0, "SUCCESS": 1, "WARNING": 2, "CRITICAL": 3}
CATEGORIES: tuple[str, ...] = ("signal", "trade", "risk", "market", "system", "validation")


class CategoryPrefs(BaseModel):
    in_app: bool = True
    telegram: bool = True
    min_severity: Severity = "INFO"


class GroupingPrefs(BaseModel):
    enabled: bool = True
    correlated_min_signals: int = Field(3, ge=2, le=50)
    correlated_window_minutes: int = Field(60, ge=5, le=1440)


class NotificationSettings(BaseModel):
    in_app_enabled: bool = True
    min_severity: Severity = "INFO"
    categories: dict[Category, CategoryPrefs] = Field(default_factory=lambda: {c: CategoryPrefs() for c in CATEGORIES})
    # Recorded in the history but not shown or counted as unread. A watchlist candidate has no
    # entry, stop or target yet, and most candles have several, so they start muted.
    muted_events: list[str] = Field(default_factory=lambda: ["SIGNAL_DETECTED", "WATCHLIST_DIGEST"])
    # Per-event overrides of the Telegram defaults in ``app.notifications.events.TAXONOMY``.
    telegram_events: dict[str, bool] = Field(default_factory=dict)
    # A trade the paper account did not take stays a signal-level record, so it stays in-app only.
    telegram_portfolio_only: bool = True
    strategies: list[str] = Field(default_factory=list, description="empty = all")
    symbols: list[str] = Field(default_factory=list, description="empty = all")
    timeframes: list[str] = Field(default_factory=list, description="empty = all")
    sound: bool = False
    grouping: GroupingPrefs = Field(default_factory=GroupingPrefs)
    retention_days: int = Field(180, ge=7, le=3650, description="CRITICAL events are kept regardless")
    timezone: str = "Asia/Colombo"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as exc:  # noqa: BLE001 - ZoneInfo raises several types
            raise ValueError(f"unknown time zone {v!r}") from exc
        return v

    @field_validator("categories")
    @classmethod
    def _all_categories(cls, v: dict[str, CategoryPrefs]) -> dict[str, CategoryPrefs]:
        return {c: v.get(c, CategoryPrefs()) for c in CATEGORIES}

    @field_validator("strategies")
    @classmethod
    def _strategies(cls, v: list[str]) -> list[str]:
        if any(s not in ("A", "B", "C") for s in v):
            raise ValueError("strategies must be A, B or C")
        return v
