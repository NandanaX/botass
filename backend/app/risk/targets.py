"""Take-profit targets as R multiples, checked against opposing support/resistance.

``R`` is the initial stop distance. LONG ``TP = entry + R x m``; SHORT ``TP = entry - R x m``.
Targets are rounded to the tick size *toward* the entry (never further away).

The nearest opposing zone (resistance for a LONG, support for a SHORT) on the entry
timeframe or a higher timeframe limits the realistic room. Every target at or beyond
the zone's near boundary is flagged as ``blocked``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.indicators.zones import Zone
from app.risk.sizing import round_price

Direction = Literal["LONG", "SHORT"]


@dataclass(slots=True)
class Target:
    label: str
    r_multiple: float
    price: float
    allocation_pct: float
    blocked: bool
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OpposingZone:
    zone: dict[str, Any]
    boundary: float
    room_r: float
    timeframe: str


def compute_targets(
    direction: Direction,
    entry: float,
    stop_distance: float,
    r_multiples: list[float],
    allocation_pct: list[float],
    opposing: OpposingZone | None = None,
    tick_size: float | None = None,
) -> list[Target]:
    if stop_distance <= 0:
        raise ValueError("stop distance must be positive")
    long = direction == "LONG"
    out: list[Target] = []
    for k, (m, alloc) in enumerate(zip(r_multiples, allocation_pct, strict=True), start=1):
        raw = entry + m * stop_distance if long else entry - m * stop_distance
        price = round_price(raw, tick_size, "down" if long else "up")
        if price <= 0:
            raise ValueError("computed target is not a positive price")
        blocked = opposing is not None and ((long and price >= opposing.boundary) or
                                            (not long and price <= opposing.boundary))
        note = None
        if blocked and opposing is not None:
            note = (f"Beyond the {opposing.timeframe} {'resistance' if long else 'support'} zone starting at "
                    f"{opposing.boundary:.6g} ({opposing.room_r:.2f}R away); price may react before this target.")
        out.append(Target(f"TP{k}", m, price, alloc, blocked, note))
    return out


def find_opposing_zone(direction: Direction, entry: float, stop_distance: float,
                       zones: list[Zone]) -> OpposingZone | None:
    """Nearest usable zone that stands between the entry and the targets."""
    long = direction == "LONG"
    best: tuple[float, Zone] | None = None
    for z in zones:
        if z.status == "INVALIDATED":
            continue
        if long:
            if z.zone_type != "RESISTANCE" or z.upper <= entry:
                continue
            boundary = max(z.lower, entry)
        else:
            if z.zone_type != "SUPPORT" or z.lower >= entry:
                continue
            boundary = min(z.upper, entry)
        dist = abs(boundary - entry)
        if best is None or dist < best[0]:
            best = (dist, z)
    if best is None:
        return None
    dist, z = best
    boundary = max(z.lower, entry) if long else min(z.upper, entry)
    return OpposingZone(z.to_dict(), boundary, dist / stop_distance, z.timeframe)
