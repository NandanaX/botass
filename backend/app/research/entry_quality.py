"""Entry Quality Engine: is the entry early, confirmed, late or exhausted?

Measured at the signal candle close (bars ``<= i`` only), always *in the trade direction*
(positive = price has already moved the trade's way):

* ``ext_fast_atr`` / ``ext_mid_atr``: distance from EMA(fast) / EMA(mid) in ATR.
* ``run``: consecutive closes in the trade direction (negative if against).
* ``accel_atr``: 3-bar change in ATR (recent acceleration).
* ``room_r``: reward room to the nearest opposing zone in R (``None`` if no zone found).
* ``swing_room_atr``: distance to the last confirmed opposing swing (swing low for a SHORT,
  swing high for a LONG) in ATR; small values mean the entry sits on nearby support/resistance.
* ``momentum_fading``: MACD histogram moved against the trade for the last 2 bars.
* ``rsi`` and ``rvol``; ``sweep``: the signal candle wicked beyond the last opposing swing
  and closed back (possible liquidity sweep against the trade).
* ``break_dist_atr``: for breakouts, close distance beyond the broken level.

Classification (first match wins; thresholds in :class:`EntryQualityParams`):

* ``INVALID``: ``room_r < min_room_r`` (target blocked) or the stop cannot be placed.
* ``EXHAUSTION_RISK``: ``ext_fast_atr >= exhaustion_ext_atr``; or ``run >= exhaustion_run``
  with RSI beyond the extreme band in the trade direction; or extended and momentum fading.
* ``EXTENDED``: ``ext_fast_atr >= extended_ext_atr`` or ``ext_mid_atr >= extended_mid_atr``
  or ``swing_room_atr < min_swing_room_atr``.
* ``EARLY``: ``ext_fast_atr <= early_ext_atr`` (still at the short-term mean).
* ``CONFIRMED``: otherwise.

These are *diagnostic definitions*. Whether rejecting EXTENDED or EXHAUSTION_RISK entries
improves results is tested out of sample by the experiment framework before it is used.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from app.research.regime import RegimeArrays
from app.strategy.features import Features

ENTRY_CLASSES = ["EARLY", "CONFIRMED", "EXTENDED", "EXHAUSTION_RISK", "INVALID"]


class EntryQualityParams(BaseModel):
    min_room_r: float = Field(1.0, ge=0, le=20)
    exhaustion_ext_atr: float = Field(2.5, ge=0, le=20)
    exhaustion_run: int = Field(5, ge=2, le=50)
    rsi_extreme_low: float = Field(30, ge=0, le=100)
    rsi_extreme_high: float = Field(70, ge=0, le=100)
    extended_ext_atr: float = Field(1.5, ge=0, le=20)
    extended_mid_atr: float = Field(3.0, ge=0, le=50)
    min_swing_room_atr: float = Field(0.5, ge=0, le=20)
    early_ext_atr: float = Field(0.25, ge=-5, le=5)


def _f(x: float) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), 4)


def entry_features(f: Features, ra: RegimeArrays, i: int, direction: str, room_r: float | None,
                   break_level: float | None = None) -> dict[str, Any]:
    s = 1.0 if direction == "LONG" else -1.0
    atr = float(f.atr[i])
    swing = f.structure.last_swing_low[i] if direction == "SHORT" else f.structure.last_swing_high[i]
    # A SHORT's room to the last swing low is close - swing_low (negative once broken).
    swing_room = math.nan
    if math.isfinite(swing):
        swing_room = (f.close[i] - swing) / atr if direction == "SHORT" else (swing - f.close[i]) / atr
    fading = (i >= 2 and all(math.isfinite(f.macd_hist[k]) for k in (i, i - 1, i - 2))
              and s * (f.macd_hist[i] - f.macd_hist[i - 1]) < 0 and s * (f.macd_hist[i - 1] - f.macd_hist[i - 2]) < 0)
    opp_swing = f.structure.last_swing_low[i] if direction == "LONG" else f.structure.last_swing_high[i]
    sweep = bool(math.isfinite(opp_swing) and (
        (direction == "LONG" and f.low[i] < opp_swing < f.close[i])
        or (direction == "SHORT" and f.high[i] > opp_swing > f.close[i])))
    accel = s * (f.close[i] - f.close[i - 3]) / atr if i >= 3 else math.nan
    return {
        "ext_fast_atr": _f(s * ra.ext_fast[i]),
        "ext_mid_atr": _f(s * ra.ext_mid[i]),
        "run": int(s * ra.run[i]),
        "accel_atr": _f(accel),
        "rsi": _f(f.rsi[i]),
        "rvol": _f(f.rvol[i]),
        "room_r": _f(room_r) if room_r is not None else None,
        "swing_room_atr": _f(swing_room),
        "momentum_fading": bool(fading),
        "sweep": sweep,
        "break_dist_atr": _f(s * (f.close[i] - break_level) / atr) if break_level is not None else None,
    }


def classify_entry(feat: dict[str, Any], direction: str, ep: EntryQualityParams | None = None
                   ) -> tuple[str, list[str]]:
    ep = ep or EntryQualityParams()
    why: list[str] = []
    ext = feat.get("ext_fast_atr")
    ext_m = feat.get("ext_mid_atr")
    rsi = feat.get("rsi")
    room = feat.get("room_r")
    swing_room = feat.get("swing_room_atr")
    if ext is None:
        return "INVALID", ["indicators unavailable"]
    if room is not None and room < ep.min_room_r:
        return "INVALID", [f"only {room:.2f}R of room before the nearest opposing zone (min {ep.min_room_r:g}R)"]
    rsi_extreme = rsi is not None and (rsi <= ep.rsi_extreme_low if direction == "SHORT" else rsi >= ep.rsi_extreme_high)
    if ext >= ep.exhaustion_ext_atr:
        why.append(f"price is {ext:.2f} ATR beyond EMA-fast in the trade direction (>= {ep.exhaustion_ext_atr:g})")
    if feat["run"] >= ep.exhaustion_run and rsi_extreme:
        why.append(f"{feat['run']} consecutive closes in the trade direction with RSI {rsi:.1f}")
    if ext >= ep.extended_ext_atr and feat.get("momentum_fading"):
        why.append("extended while MACD momentum fades against the trade")
    if why:
        return "EXHAUSTION_RISK", why
    if ext >= ep.extended_ext_atr:
        why.append(f"{ext:.2f} ATR beyond EMA-fast (>= {ep.extended_ext_atr:g})")
    if ext_m is not None and ext_m >= ep.extended_mid_atr:
        why.append(f"{ext_m:.2f} ATR beyond EMA-mid (>= {ep.extended_mid_atr:g})")
    if swing_room is not None and 0 <= swing_room < ep.min_swing_room_atr:
        side = "swing low (support)" if direction == "SHORT" else "swing high (resistance)"
        why.append(f"only {swing_room:.2f} ATR from the last confirmed {side}")
    if why:
        return "EXTENDED", why
    if ext <= ep.early_ext_atr:
        return "EARLY", [f"price is {ext:.2f} ATR from EMA-fast (still near the short-term mean)"]
    return "CONFIRMED", [f"{ext:.2f} ATR from EMA-fast, room {f'{room:.1f}R' if room is not None else 'unbounded'}"]
