"""Price levels beyond the Phase 2 zones: previous day/week extremes, moving-average levels,
trendlines from confirmed swings, cross-timeframe zone de-duplication and explicit
support/resistance flip retests. All causal at bar ``i``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np

from app.analysis.frame import AnalysisFrame
from app.indicators.zones import Zone


@dataclass(slots=True)
class Level:
    kind: str  # prev_day_high | prev_day_low | prev_week_high | prev_week_low | ema20 | ema50 | ema200 | sma200
    price: float
    role: Literal["SUPPORT", "RESISTANCE"]
    distance_atr: float | None
    label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Trendline:
    kind: Literal["support", "resistance"]
    t1: int
    p1: float
    t2: int
    p2: float
    value_now: float
    slope_atr_per_bar: float
    touches: int
    broken: bool
    label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _period_extremes(fr: AnalysisFrame, i: int, seconds: int) -> tuple[float, float] | None:
    f = fr.f
    sec = f.open_time // 1_000_000_000
    cur = sec[i] // seconds
    prev = cur - 1
    if seconds == 7 * 86400:  # weeks start Monday 00:00 UTC (epoch was a Thursday)
        cur = (sec[i] - 4 * 86400) // seconds
        prev = cur - 1
        key = (sec[: i + 1] - 4 * 86400) // seconds
    else:
        key = sec[: i + 1] // seconds
    mask = key == prev
    if not mask.any():
        return None
    return float(f.high[: i + 1][mask].max()), float(f.low[: i + 1][mask].min())


def key_levels(fr: AnalysisFrame, i: int) -> list[Level]:
    f = fr.f
    c = float(f.close[i])
    atr = float(f.atr[i]) if math.isfinite(f.atr[i]) and f.atr[i] > 0 else None
    out: list[Level] = []

    def add(kind: str, price: float, label: str) -> None:
        if math.isfinite(price):
            out.append(Level(kind, price, "SUPPORT" if price < c else "RESISTANCE",
                             round(abs(c - price) / atr, 2) if atr else None, label))

    tf_sec = int(f.close_time[0] - f.open_time[0]) // 1_000_000_000 if len(f) else 0
    if tf_sec < 86400:
        d = _period_extremes(fr, i, 86400)
        if d:
            add("prev_day_high", d[0], "previous day high")
            add("prev_day_low", d[1], "previous day low")
    if tf_sec < 7 * 86400:
        w = _period_extremes(fr, i, 7 * 86400)
        if w:
            add("prev_week_high", w[0], "previous week high")
            add("prev_week_low", w[1], "previous week low")
    add("ema20", float(f.ema_fast[i]), "EMA 20 (dynamic)")
    add("ema50", float(f.ema_mid[i]), "EMA 50 (dynamic)")
    add("ema200", float(f.ema_slow[i]), "EMA 200 (dynamic)")
    add("sma200", float(f.sma_long[i]), "SMA 200 (dynamic)")
    return sorted(out, key=lambda lv: lv.distance_atr if lv.distance_atr is not None else 1e9)


def trendlines(fr: AnalysisFrame, i: int) -> list[Trendline]:
    """Support line through the last two confirmed rising swing lows; resistance line through
    the last two falling swing highs. A line counts as broken after a close beyond it."""
    f = fr.f
    atr = float(f.atr[i]) if math.isfinite(f.atr[i]) and f.atr[i] > 0 else None
    if atr is None:
        return []
    pv = fr.major_pivots
    known = [(int(pv.index[k]), float(pv.price[k]), bool(pv.is_high[k]))
             for k in range(len(pv.index)) if pv.confirmed_at[k] <= i]
    out: list[Trendline] = []
    for is_high, kind in ((False, "support"), (True, "resistance")):
        pts = [p for p in known if p[2] == is_high][-2:]
        if len(pts) < 2:
            continue
        (a, pa, _), (b, pb, _) = pts
        if b == a or (kind == "support" and pb <= pa) or (kind == "resistance" and pb >= pa):
            continue
        slope = (pb - pa) / (b - a)
        val = pa + slope * (i - a)
        seg = np.arange(b + 1, i + 1)
        line_vals = pa + slope * (seg - a)
        closes = f.close[b + 1: i + 1]
        broken = bool(np.any(closes < line_vals - 0.1 * atr)) if kind == "support" else \
            bool(np.any(closes > line_vals + 0.1 * atr))
        lows_or_highs = f.low[a: i + 1] if kind == "support" else f.high[a: i + 1]
        lv = pa + slope * (np.arange(a, i + 1) - a)
        touches = int(np.sum(np.abs(lows_or_highs - lv) <= 0.2 * atr))
        out.append(Trendline(kind, fr.unix(a), pa, fr.unix(b), pb, float(val), round(slope / atr, 4), touches, broken,
                             f"{'Rising support' if kind == 'support' else 'Falling resistance'} trendline through "
                             f"{fr.iso(a)} and {fr.iso(b)}; now {val:.6g}{' (broken)' if broken else ''}"))
    return out


TF_RANK = {"5m": 1, "15m": 2, "30m": 3, "1h": 4, "4h": 5, "1d": 6, "1w": 7}


def merge_zones(groups: dict[str, list[Zone]]) -> list[dict[str, Any]]:
    """De-duplicate overlapping zones from different timeframes: keep the highest-timeframe
    zone and record the others as confluence (they describe the same price area)."""
    flat = sorted((z for zs in groups.values() for z in zs), key=lambda z: -TF_RANK.get(z.timeframe, 0))
    kept: list[dict[str, Any]] = []
    for z in flat:
        for k in kept:
            if z.lower <= k["upper"] and z.upper >= k["lower"] and z.zone_type == k["zone_type"]:
                k["confluence"].append(z.timeframe)
                break
        else:
            d = z.to_dict()
            d["confluence"] = []
            kept.append(d)
    return sorted(kept, key=lambda d: -d["upper"])


def flip_retest(fr: AnalysisFrame, z: Zone, i: int) -> dict[str, Any] | None:
    """For a BROKEN zone: did price come back to it after the break and hold the new role?
    Support flip (broken resistance, price above): a later low reaches ``upper + 0.1 ATR``
    while every close stays above ``lower``. Mirror for resistance flips."""
    if z.status != "BROKEN" or z.last_break_index is None or z.last_break_index >= i:
        return None
    f = fr.f
    atr = float(f.atr[i]) if math.isfinite(f.atr[i]) else 0.0
    s = z.last_break_index + 1
    if z.zone_type == "SUPPORT":
        touched = np.flatnonzero(f.low[s: i + 1] <= z.upper + 0.1 * atr)
        held = bool(np.all(f.close[s: i + 1] > z.lower))
    else:
        touched = np.flatnonzero(f.high[s: i + 1] >= z.lower - 0.1 * atr)
        held = bool(np.all(f.close[s: i + 1] < z.upper))
    if not len(touched):
        return {"retested": False, "held": held, "note": "broken, not yet retested"}
    return {"retested": True, "held": held, "retest_time": fr.iso(s + int(touched[0])),
            "note": (f"broken {'resistance now support' if z.zone_type == 'SUPPORT' else 'support now resistance'}: "
                     f"retested {fr.iso(s + int(touched[0]))} and {'held' if held else 'failed'}")}
