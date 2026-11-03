"""Support/resistance *zones* built from confirmed swing pivots, without look-ahead.

A zone evaluated at bar ``i`` uses only pivots whose confirmation bar is ``<= i`` (a swing
with strength ``k`` at bar ``j`` is known at ``j + k``) and only closes up to ``i`` for its
status. Evaluating at ``i`` on the full history therefore equals evaluating on history
truncated at ``i``.

Algorithm
---------
1. Take confirmed pivots inside ``lookback`` bars.
2. Sort them by price and cluster greedily: a pivot joins the current cluster while it is
   within ``cluster_atr_mult x ATR(i)`` of the cluster's lowest price, so a zone is never
   wider than that tolerance. Zones narrower than ``min_width_atr x ATR`` are widened
   around their centre.
3. Clusters with fewer than ``min_tests`` pivots are dropped. Each pivot is one confirmed
   *test*; a test is a *rejection* when that candle closed back outside the zone.
4. Status from the closes after the zone formed (its ``min_tests``-th pivot confirmed):
   a close beyond a boundary by ``break_buffer_atr x ATR`` puts price on that side.
   No side change: ``ACTIVE``. One or more: ``BROKEN`` (its role has flipped, e.g. broken
   resistance now below price). ``invalidate_after_crossings`` or more: ``INVALIDATED``.
5. Role relative to the close of bar ``i``: below price ``SUPPORT``, above ``RESISTANCE``;
   inside, the side price came from decides.
6. Strength (0-100) combines tests, rejections, recency and status. It ranks zones; it is
   not a probability that price will react.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np

from app.indicators.core import FloatArray

ZoneType = Literal["SUPPORT", "RESISTANCE"]
ZoneStatus = Literal["ACTIVE", "BROKEN", "INVALIDATED"]


@dataclass(frozen=True, slots=True)
class PivotArrays:
    """Confirmed swing pivots as parallel arrays sorted by confirmation bar."""

    index: np.ndarray  # bar of the extreme
    confirmed_at: np.ndarray  # first bar at which the pivot is known
    price: np.ndarray
    is_high: np.ndarray  # bool

    @classmethod
    def from_pivots(cls, pivots: list) -> PivotArrays:
        return cls(
            np.array([p.index for p in pivots], dtype=np.int64),
            np.array([p.confirmed_at for p in pivots], dtype=np.int64),
            np.array([p.price for p in pivots], dtype=float),
            np.array([p.kind == "high" for p in pivots], dtype=bool),
        )


@dataclass(slots=True)
class Zone:
    id: str
    timeframe: str
    lower: float
    upper: float
    zone_type: ZoneType
    origin: Literal["swing_highs", "swing_lows", "mixed"]
    status: ZoneStatus
    tests: int
    rejections: int
    crossings: int
    first_test_index: int
    last_test_index: int
    formed_index: int
    last_break_index: int | None
    strength: float
    price_inside: bool
    first_test_time: str = ""
    last_test_time: str = ""
    explanation: str = ""
    symbol: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def mid(self) -> float:
        return (self.lower + self.upper) / 2

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mid"] = self.mid
        return d


def _fmt(x: float) -> str:
    if abs(x) >= 1000:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:,.4f}".rstrip("0").rstrip(".")
    return f"{x:.6g}"


def _iso(ns: int) -> str:
    return np.datetime_as_string(np.datetime64(int(ns), "ns"), unit="s") + "Z"


def zone_id(timeframe: str, origin: str, first_open_ns: int, symbol: str | None = None) -> str:
    """Stable id: same first pivot and origin give the same id on every later bar."""
    raw = f"{symbol or ''}|{timeframe}|{origin}|{first_open_ns}"
    return "z_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def detect_zones(
    *,
    timeframe: str,
    open_time: np.ndarray,
    close: FloatArray,
    pivots: PivotArrays,
    atr_value: float,
    at_index: int,
    lookback: int = 300,
    cluster_atr_mult: float = 0.5,
    min_width_atr: float = 0.15,
    min_tests: int = 2,
    break_buffer_atr: float = 0.1,
    invalidate_after_crossings: int = 3,
    max_zones_per_side: int | None = 4,
    symbol: str | None = None,
) -> list[Zone]:
    """Zones known at the close of bar ``at_index``. Sorted by distance from that close,
    supports first. ``max_zones_per_side=None`` returns every qualifying zone."""
    if at_index < 0 or not math.isfinite(atr_value) or atr_value <= 0 or len(pivots.index) == 0:
        return []
    start = max(0, at_index - lookback)
    mask = (pivots.confirmed_at <= at_index) & (pivots.index >= start)
    if not mask.any():
        return []
    p_idx = pivots.index[mask]
    p_conf = pivots.confirmed_at[mask]
    p_price = pivots.price[mask]
    p_high = pivots.is_high[mask]
    order = np.argsort(p_price, kind="stable")
    tol = cluster_atr_mult * atr_value
    clusters: list[np.ndarray] = []
    current: list[int] = []
    low_of_current = math.nan
    for k in order:
        if current and p_price[k] - low_of_current <= tol:
            current.append(int(k))
        else:
            if current:
                clusters.append(np.array(current))
            current, low_of_current = [int(k)], float(p_price[k])
    if current:
        clusters.append(np.array(current))

    price_now = float(close[at_index])
    buf = break_buffer_atr * atr_value
    min_w = min_width_atr * atr_value
    zones: list[Zone] = []
    for members in clusters:
        if len(members) < min_tests:
            continue
        lower, upper = float(p_price[members].min()), float(p_price[members].max())
        if upper - lower < min_w:
            c = (upper + lower) / 2
            lower, upper = c - min_w / 2, c + min_w / 2
        highs = int(p_high[members].sum())
        lows = len(members) - highs
        by_time = members[np.argsort(p_idx[members], kind="stable")]
        if highs > lows:
            origin = "swing_highs"
        elif lows > highs:
            origin = "swing_lows"
        else:
            origin = "mixed"
        # Rejection: the pivot candle closed back outside the zone on the side it came from.
        rejections = 0
        for k in members:
            c = close[p_idx[k]]
            if (p_high[k] and c < lower) or (not p_high[k] and c > upper):
                rejections += 1
        formed = int(np.sort(p_conf[members])[min_tests - 1])
        seg = close[formed: at_index + 1]
        side = np.where(seg > upper + buf, 1, np.where(seg < lower - buf, -1, 0))
        nz = side[side != 0]
        crossings = int(np.count_nonzero(np.diff(nz))) if len(nz) > 1 else 0
        last_break: int | None = None
        if crossings:
            changes = np.flatnonzero(np.diff(nz)) + 1
            nz_pos = np.flatnonzero(side)
            last_break = formed + int(nz_pos[changes[-1]])
        if crossings >= invalidate_after_crossings:
            status: ZoneStatus = "INVALIDATED"
        elif crossings > 0:
            status = "BROKEN"
        else:
            status = "ACTIVE"
        inside = lower <= price_now <= upper
        if price_now > upper:
            ztype: ZoneType = "SUPPORT"
        elif price_now < lower:
            ztype = "RESISTANCE"
        else:
            came_from = int(nz[-1]) if len(nz) else (1 if origin == "swing_lows" else -1)
            ztype = "SUPPORT" if came_from > 0 else "RESISTANCE"
        first_i, last_i = int(p_idx[by_time[0]]), int(p_idx[by_time[-1]])
        tests = len(members)
        recency = math.exp(-(at_index - last_i) / max(1.0, lookback / 3))
        strength = (40 * min(tests, 5) / 5 + 20 * rejections / tests + 25 * recency
                    + {"ACTIVE": 15, "BROKEN": 8, "INVALIDATED": 0}[status])
        zone = Zone(
            id=zone_id(timeframe, origin, int(open_time[first_i]), symbol),
            timeframe=timeframe, lower=lower, upper=upper, zone_type=ztype, origin=origin, status=status,
            tests=tests, rejections=rejections, crossings=crossings, first_test_index=first_i,
            last_test_index=last_i, formed_index=formed, last_break_index=last_break,
            strength=round(strength, 1), price_inside=inside,
            first_test_time=_iso(open_time[first_i]), last_test_time=_iso(open_time[last_i]), symbol=symbol,
        )
        zone.explanation = explain_zone(zone, highs, lows)
        zones.append(zone)

    supports = sorted((z for z in zones if z.zone_type == "SUPPORT"), key=lambda z: price_now - z.upper)
    resistances = sorted((z for z in zones if z.zone_type == "RESISTANCE"), key=lambda z: z.lower - price_now)
    if max_zones_per_side is not None:
        supports, resistances = supports[:max_zones_per_side], resistances[:max_zones_per_side]
    return supports + resistances


def explain_zone(z: Zone, highs: int, lows: int) -> str:
    kinds = []
    if highs:
        kinds.append(f"{highs} confirmed swing high{'s' if highs != 1 else ''}")
    if lows:
        kinds.append(f"{lows} confirmed swing low{'s' if lows != 1 else ''}")
    text = (f"{z.timeframe} {z.zone_type.lower()} zone {_fmt(z.lower)}-{_fmt(z.upper)} formed by "
            f"{' and '.join(kinds)} (first {z.first_test_time}, last {z.last_test_time}). "
            f"{z.rejections} of {z.tests} tests closed back outside the zone.")
    if z.status == "ACTIVE":
        text += " No candle has closed through it since it formed."
    elif z.status == "BROKEN":
        text += (f" Price has closed through it {z.crossings} time{'s' if z.crossings != 1 else ''}; "
                 f"it now acts as {z.zone_type.lower()} (role reversal).")
    else:
        text += f" Price crossed it {z.crossings} times, so it is treated as invalidated."
    return text


def nearest(zones: list[Zone], zone_type: ZoneType, usable_only: bool = True) -> Zone | None:
    cands = [z for z in zones if z.zone_type == zone_type and (not usable_only or z.status != "INVALIDATED")]
    return cands[0] if cands else None
