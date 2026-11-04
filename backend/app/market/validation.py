"""Validation of exchange market data before it is stored or analysed."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime

from app.timeutil import is_aligned, tf_delta

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CandleIssue:
    open_time: datetime
    reason: str


def candle_problem(row: dict, timeframe: str) -> str | None:
    """Return a description of what is wrong with a candle, or None if it is valid."""
    try:
        o, h, lo, c = (float(row[k]) for k in ("open", "high", "low", "close"))
        v = float(row["volume"])
    except (KeyError, TypeError, ValueError):
        return "missing or non-numeric OHLCV field"
    if not all(math.isfinite(x) for x in (o, h, lo, c, v)):
        return "non-finite value"
    if min(o, h, lo, c) <= 0:
        return "non-positive price"
    if v < 0:
        return "negative volume"
    if h < max(o, c) or lo > min(o, c) or h < lo:
        return "inconsistent high/low"
    ot: datetime = row["open_time"]
    if ot.tzinfo is None:
        return "naive timestamp"
    if not is_aligned(ot, timeframe):
        return "open time not aligned to timeframe"
    return None


def validate_candles(rows: list[dict], timeframe: str, context: str = "") -> list[dict]:
    """Drop invalid candles (logged), de-duplicate and sort ascending by open time."""
    valid: dict[datetime, dict] = {}
    for row in rows:
        problem = candle_problem(row, timeframe)
        if problem:
            log.warning("dropping invalid candle", extra={"context": context, "reason": problem,
                                                          "open_time": str(row.get("open_time"))})
            continue
        valid[row["open_time"]] = row
    return [valid[k] for k in sorted(valid)]


def find_gaps(open_times: list[datetime], timeframe: str) -> list[tuple[datetime, datetime]]:
    """Return (first_missing_open_time, last_missing_open_time) ranges for missing candles."""
    step = tf_delta(timeframe)
    gaps: list[tuple[datetime, datetime]] = []
    for prev, cur in zip(open_times, open_times[1:], strict=False):
        if cur - prev > step:
            gaps.append((prev + step, cur - step))
    return gaps


def filter_closed(rows: list[dict], timeframe: str, now: datetime) -> list[dict]:
    """Keep only candles whose close time (open + interval) is at or before ``now``."""
    step = tf_delta(timeframe)
    return [r for r in rows if r["open_time"] + step <= now]
