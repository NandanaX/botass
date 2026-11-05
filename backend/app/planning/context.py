"""Everything a plan needs from the market, read at the signal candle's close (bars ``<= i`` only).

Used by both the historical study and the live card, so the two see the same inputs. Later candles
are never read: pivots count only once confirmed, and the noise window ends at ``i``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from app.planning.gate import GateInput
from app.planning.stops import StopContext
from app.research.candidates import noise_distance
from app.research.entry_quality import entry_features
from app.research.regime import RegimeArrays
from app.strategy.features import Features


def _finite(x: Any) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


@dataclass(slots=True)
class DecisionContext:
    direction: str
    strategy: str
    production_status: str
    production_reason_codes: list[str]
    entry: float
    atr: float
    atr_pct: float
    original_stop: float | None
    original_target: float | None
    zone_level: float | None
    zone_lower: float | None
    zone_upper: float | None
    zone_timeframe: str | None
    swing_level: float | None
    noise_distance: float | None
    opposing_boundary: float | None
    opposing_timeframe: str | None
    major_swing: float | None
    measured_move: float | None
    rvol: float | None
    structure: str
    mtf_confirmed: bool | None
    htf_trends: dict[str, str] = field(default_factory=dict)
    features: dict[str, Any] = field(default_factory=dict)
    regime: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def stop_context(self, tick_size: float | None = None) -> StopContext:
        return StopContext(self.direction, self.entry, self.atr, self.original_stop, self.swing_level, self.zone_level,
                           self.noise_distance, tick_size)

    def gate_input(self, stop: float | None, stop_refused: str | None = None, same_symbol_open: bool = False,
                   correlated_open: int = 0) -> GateInput:
        ft = self.features
        return GateInput(self.direction, self.strategy, self.entry, self.atr, stop, self.production_status,
                         self.production_reason_codes, self.opposing_boundary, self.htf_trends, ft.get("ext_fast_atr"),
                         ft.get("run") or 0, ft.get("rsi"), ft.get("break_dist_atr"), same_symbol_open, correlated_open,
                         stop_refused)


def major_swing_beyond(f: Features, i: int, direction: str, entry: float, lookback: int = 300) -> float | None:
    """Furthest confirmed swing on the profit side within ``lookback`` bars (highest high for a LONG)."""
    pv = f.pivots
    long = direction == "LONG"
    m = (pv.confirmed_at <= i) & (pv.index >= i - lookback) & (pv.is_high == long)
    prices = pv.price[m]
    prices = prices[prices > entry] if long else prices[prices < entry]
    if not len(prices):
        return None
    return float(prices.max() if long else prices.min())


def measured_move(strategy: str, direction: str, level: float | None, swing: float | None) -> float | None:
    """Breakouts only: the broken level plus the height from the last swing on the other side up to it."""
    if strategy != "breakout" or level is None or swing is None:
        return None
    long = direction == "LONG"
    height = level - swing if long else swing - level
    if height <= 0:
        return None
    return level + height if long else level - height


def build_context(f: Features, ra: RegimeArrays, i: int, st: Any) -> DecisionContext:
    """``st`` is a triggered :class:`SignalSetup` from ``evaluate_at`` at bar ``i``."""
    direction = st.direction
    long = direction == "LONG"
    plan = st.plan or {}
    entry = float(st.entry_reference)
    atr = float(st.atr)
    zone = plan.get("zone") or {}
    level = (plan.get("stop") or {}).get("structural_level")
    if level is None and zone:
        level = zone.get("lower") if long else zone.get("upper")
    swing = _finite(f.structure.last_swing_low[i] if long else f.structure.last_swing_high[i])
    opposing = plan.get("opposing_zone") or {}
    near = None
    if st.strategy == "breakout" and zone:
        near = zone.get("upper") if long else zone.get("lower")
    noise = noise_distance(f, i, direction)
    label = ra.label[i] if ra is not None else None
    return DecisionContext(
        direction=direction, strategy=st.strategy, production_status=st.status,
        production_reason_codes=[r.get("code", "") for r in (st.reasons or [])],
        entry=entry, atr=atr, atr_pct=100 * atr / entry,
        original_stop=_finite(st.stop_loss), original_target=_finite(st.take_profit),
        zone_level=_finite(level), zone_lower=_finite(zone.get("lower")), zone_upper=_finite(zone.get("upper")),
        zone_timeframe=zone.get("timeframe"), swing_level=swing, noise_distance=_finite(noise),
        opposing_boundary=_finite(opposing.get("boundary")), opposing_timeframe=opposing.get("timeframe"),
        major_swing=major_swing_beyond(f, i, direction, entry),
        measured_move=measured_move(st.strategy, direction, _finite(near), swing),
        rvol=_finite(f.rvol[i]), structure=str(int(f.structure.structure[i])), mtf_confirmed=st.mtf_confirmed,
        htf_trends={h.timeframe: h.trend for h in (st.mtf_details or [])},
        features=entry_features(f, ra, i, direction, plan.get("room_r"), _finite(near)),
        regime=str(label) if label is not None else None,
    )
