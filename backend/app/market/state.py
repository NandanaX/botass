"""Live market state (tickers and forming candles) kept in memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.timeutil import utcnow


@dataclass(slots=True)
class TickerState:
    data: dict[str, Any]
    updated_at: datetime
    source: str  # "websocket" | "rest"


@dataclass(slots=True)
class MarketState:
    stale_after_seconds: float = 30.0
    tickers: dict[str, TickerState] = field(default_factory=dict)
    live_candles: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    ws_connected: bool = False
    ws_last_message_at: datetime | None = None
    ws_reconnects: int = 0
    rest_fallback_active: bool = False
    last_error: str | None = None

    def update_ticker(self, symbol: str, data: dict[str, Any], source: str) -> dict[str, Any]:
        now = utcnow()
        current = self.tickers.get(symbol)
        fresh = {k: v for k, v in data.items() if v is not None}
        if "bid_price" in fresh or "ask_price" in fresh:
            fresh["quote_at"] = now.isoformat()  # age of the bid/ask, tracked separately from other fields
        merged = {**(current.data if current else {}), **fresh}
        self.tickers[symbol] = TickerState(merged, now, source)
        return self.ticker_view(symbol)

    def is_stale(self, symbol: str, now: datetime | None = None) -> bool:
        t = self.tickers.get(symbol)
        if t is None:
            return True
        return ((now or utcnow()) - t.updated_at).total_seconds() > self.stale_after_seconds

    def ticker_view(self, symbol: str) -> dict[str, Any]:
        t = self.tickers.get(symbol)
        if t is None:
            return {"symbol": symbol, "available": False, "stale": True, "updated_at": None, "source": None}
        return {**t.data, "symbol": symbol, "available": True, "stale": self.is_stale(symbol),
                "updated_at": t.updated_at.isoformat(), "source": t.source}

    def connection_view(self) -> dict[str, Any]:
        return {
            "ws_connected": self.ws_connected,
            "ws_last_message_at": self.ws_last_message_at.isoformat() if self.ws_last_message_at else None,
            "ws_reconnects": self.ws_reconnects,
            "rest_fallback_active": self.rest_fallback_active,
            "last_error": self.last_error,
        }
