from __future__ import annotations

from datetime import UTC, datetime, timedelta

TIMEFRAMES: dict[str, int] = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
}

# Candle alignment offset from the UTC epoch. The epoch (1970-01-01) was a Thursday; Bybit
# weekly candles open on Monday 00:00 UTC, i.e. 4 days after an epoch-aligned week.
TF_OFFSETS: dict[str, int] = {"1w": 4 * 24 * 60 * 60}


def utcnow() -> datetime:
    return datetime.now(UTC)


# Not offered in the app (no live subscription); used by research to replay ambiguous candles.
RESEARCH_TIMEFRAMES: dict[str, int] = {"1m": 60}


def tf_seconds(timeframe: str) -> int:
    try:
        return TIMEFRAMES[timeframe] if timeframe in TIMEFRAMES else RESEARCH_TIMEFRAMES[timeframe]
    except KeyError as exc:
        raise ValueError(f"unsupported timeframe {timeframe!r}") from exc


def tf_delta(timeframe: str) -> timedelta:
    return timedelta(seconds=tf_seconds(timeframe))


def ms_to_dt(ms: int | str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, UTC)


def dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def floor_time(dt: datetime, timeframe: str) -> datetime:
    """Open time of the candle containing ``dt`` (candles are aligned to the UTC epoch)."""
    step = tf_seconds(timeframe)
    offset = TF_OFFSETS.get(timeframe, 0)
    ts = int(dt.timestamp())
    return datetime.fromtimestamp(ts - (ts - offset) % step, UTC)


def is_aligned(dt: datetime, timeframe: str) -> bool:
    return (int(dt.timestamp()) - TF_OFFSETS.get(timeframe, 0)) % tf_seconds(timeframe) == 0


def last_closed_open_time(now: datetime, timeframe: str) -> datetime:
    """Open time of the most recent candle whose close time is <= ``now``."""
    return floor_time(now, timeframe) - tf_delta(timeframe)
