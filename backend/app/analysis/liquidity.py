"""Optional Fibonacci and liquidity-style analysis (experimental; validate before relying on it).

* Fibonacci: anchored on the last *confirmed* major swing pair (a high and a low).
  Retracements 23.6/38.2/50/61.8/78.6 % and extensions 127.2/161.8 % of that leg.
* Equal highs/lows: two or more confirmed swing highs (lows) within
  ``equal_level_tolerance_atr x ATR`` that price has not closed beyond.
* Sweep: a wick beyond a confirmed swing level with the close back inside, within
  ``sweep_lookback`` bars. Failed breakout: a close beyond the level followed within 3 bars
  by a close back on the original side. Reclaim: a level closed below, then closed back
  above (and the mirror).
* Fair value gap (3-candle imbalance): bullish when ``low[i] > high[i-2]`` by at least
  ``fvg_min_atr x ATR``; bearish when ``high[i] < low[i-2]``. It is "filled" once price
  trades back through it.

These are price-based definitions only. They do not reveal institutional orders or
liquidation levels, and nothing here is labelled as such.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.analysis.frame import AnalysisFrame

RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSIONS = (1.272, 1.618)


@dataclass(slots=True)
class Fibonacci:
    direction: Literal["UP", "DOWN"]  # leg direction (low->high = UP)
    anchor_high: float
    anchor_low: float
    high_time: str
    low_time: str
    levels: list[dict[str, Any]]
    price_position: float | None  # retracement depth of the current close (0 = leg end)
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fibonacci(fr: AnalysisFrame, i: int) -> Fibonacci | None:
    pv = fr.major_pivots
    known = [(int(pv.index[k]), float(pv.price[k]), bool(pv.is_high[k]))
             for k in range(len(pv.index)) if pv.confirmed_at[k] <= i]
    if not known:
        return None
    last_h = next((p for p in reversed(known) if p[2]), None)
    last_l = next((p for p in reversed(known) if not p[2]), None)
    if last_h is None or last_l is None or last_h[1] <= last_l[1]:
        return None
    up = last_h[0] > last_l[0]  # leg went low -> high
    hi, lo = last_h[1], last_l[1]
    rng = hi - lo
    levels = []
    for r in RETRACEMENTS:
        levels.append({"ratio": r, "kind": "retracement", "price": hi - rng * r if up else lo + rng * r})
    for r in EXTENSIONS:
        levels.append({"ratio": r, "kind": "extension", "price": lo + rng * r if up else hi - rng * r})
    c = float(fr.f.close[i])
    pos = ((hi - c) / rng) if up else ((c - lo) / rng)
    return Fibonacci("UP" if up else "DOWN", hi, lo, fr.iso(last_h[0]), fr.iso(last_l[0]), levels, round(pos, 3),
                     f"Fibonacci of the last confirmed {'up' if up else 'down'} leg {lo:.6g}-{hi:.6g}; the close is at a "
                     f"{pos:.1%} retracement of it.")


def equal_levels(fr: AnalysisFrame, i: int) -> list[dict[str, Any]]:
    f, P = fr.f, fr.params.liquidity
    atr = float(f.atr[i]) if math.isfinite(f.atr[i]) and f.atr[i] > 0 else None
    if atr is None:
        return []
    pv = fr.pivots
    out = []
    for is_high, name in ((True, "Equal highs"), (False, "Equal lows")):
        pts = [(int(pv.index[k]), float(pv.price[k])) for k in range(len(pv.index))
               if pv.confirmed_at[k] <= i and bool(pv.is_high[k]) == is_high and pv.index[k] >= i - 200]
        pts.sort(key=lambda p: p[1])
        cluster: list[tuple[int, float]] = []
        for p in pts + [(-1, math.inf)]:
            if cluster and p[1] - cluster[0][1] <= P.equal_level_tolerance_atr * atr:
                cluster.append(p)
                continue
            if len(cluster) >= 2:
                level = max(q[1] for q in cluster) if is_high else min(q[1] for q in cluster)
                last = max(q[0] for q in cluster)
                after = f.close[last + 1: i + 1]
                intact = not len(after) or (after.max() <= level if is_high else after.min() >= level)
                if intact:
                    out.append({"kind": name, "price": level, "touches": len(cluster),
                                "times": [fr.iso(q[0]) for q in sorted(cluster)],
                                "explanation": f"{name}: {len(cluster)} confirmed swings within "
                                               f"{P.equal_level_tolerance_atr:g} ATR at {level:.6g}, not closed beyond."})
            cluster = [p]
    return out


def sweeps_and_reclaims(fr: AnalysisFrame, i: int) -> list[dict[str, Any]]:
    f, P = fr.f, fr.params.liquidity
    pv = fr.pivots
    start = max(0, i - P.sweep_lookback)
    out: list[dict[str, Any]] = []
    for k in range(len(pv.index)):
        if pv.confirmed_at[k] > i or pv.index[k] < start - P.sweep_lookback:
            continue
        lvl, is_high, conf = float(pv.price[k]), bool(pv.is_high[k]), int(pv.confirmed_at[k])
        broke_close = None
        for b in range(max(conf + 1, start), i + 1):
            hi, lo, c = float(f.high[b]), float(f.low[b]), float(f.close[b])
            if is_high:
                if broke_close is None and c > lvl:
                    broke_close = b
                elif broke_close is not None and c < lvl:
                    kind = "Failed breakout" if b - broke_close <= 3 else "Lost level"
                    out.append({"kind": kind, "direction": "BEARISH", "price": lvl, "time": fr.iso(b),
                                "explanation": f"{kind}: closed above swing high {lvl:.6g} at {fr.iso(broke_close)}, "
                                               f"back below at {fr.iso(b)}."})
                    break
                elif broke_close is None and hi > lvl and c < lvl:
                    out.append({"kind": "Sweep", "direction": "BEARISH", "price": lvl, "time": fr.iso(b),
                                "explanation": f"Sweep: wick above swing high {lvl:.6g} at {fr.iso(b)} with the close "
                                               f"back below it."})
                    break
            else:
                if broke_close is None and c < lvl:
                    broke_close = b
                elif broke_close is not None and c > lvl:
                    kind = "Failed breakdown" if b - broke_close <= 3 else "Reclaim"
                    out.append({"kind": kind, "direction": "BULLISH", "price": lvl, "time": fr.iso(b),
                                "explanation": f"{kind}: closed below swing low {lvl:.6g} at {fr.iso(broke_close)}, "
                                               f"back above at {fr.iso(b)}."})
                    break
                elif broke_close is None and lo < lvl and c > lvl:
                    out.append({"kind": "Sweep", "direction": "BULLISH", "price": lvl, "time": fr.iso(b),
                                "explanation": f"Sweep: wick below swing low {lvl:.6g} at {fr.iso(b)} with the close "
                                               f"back above it."})
                    break
    out.sort(key=lambda e: e["time"])
    return out[-6:]


def fair_value_gaps(fr: AnalysisFrame, i: int) -> list[dict[str, Any]]:
    f, P = fr.f, fr.params.liquidity
    out = []
    for b in range(max(2, i - 150), i + 1):
        atr = float(f.atr[b]) if math.isfinite(f.atr[b]) else None
        if not atr:
            continue
        if f.low[b] - f.high[b - 2] >= P.fvg_min_atr * atr:
            lo_, hi_, d = float(f.high[b - 2]), float(f.low[b]), "BULLISH"
        elif f.low[b - 2] - f.high[b] >= P.fvg_min_atr * atr:
            lo_, hi_, d = float(f.high[b]), float(f.low[b - 2]), "BEARISH"
        else:
            continue
        later = slice(b + 1, i + 1)
        filled = bool(len(f.low[later]) and (f.low[later].min() <= lo_ if d == "BULLISH" else f.high[later].max() >= hi_))
        if not filled:
            out.append({"direction": d, "lower": lo_, "upper": hi_, "time": fr.unix(b - 1), "created": fr.iso(b),
                        "explanation": f"{d.title()} 3-candle imbalance {lo_:.6g}-{hi_:.6g} created {fr.iso(b)}, "
                                       f"not yet filled."})
    return out[-P.max_fvgs:]
