from __future__ import annotations

import hashlib
from datetime import datetime


def signal_id(exchange: str, symbol: str, timeframe: str, direction: str, candle_open_time: datetime,
              strategy_version: str, strategy_name: str | None = None) -> str:
    """Deterministic identifier: the same setup on the same candle always maps to the same id,
    which makes persistence idempotent and prevents duplicate alerts."""
    parts = [exchange, symbol, timeframe, direction, candle_open_time.isoformat(), strategy_version]
    if strategy_name:  # Phase 2: breakout and pullback may fire on the same candle
        parts.append(strategy_name)
    key = "|".join(parts)
    return hashlib.sha256(key.encode()).hexdigest()
