"""Algorithmic market structure: swing labels, Break of Structure and Change of Character.

Definitions (all from confirmed swings; a swing with window ``k`` at bar ``j`` is known
from bar ``j + k``):

* Swing labels compare a newly confirmed swing with the previous swing of the same kind:
  ``HH``/``LH``/``EH`` for highs and ``HL``/``LL``/``EL`` for lows. "Equal" means within
  ``equal_tolerance_atr x ATR`` (ATR at the confirmation bar).
* A **structural break** needs a candle *close* beyond the most recent unbroken confirmed
  swing by ``break_buffer_atr x ATR``. A wick beyond it without such a close is recorded
  as a temporary ``WICK_BREAK`` and does not change structure.
* **BOS** (Break of Structure): a break in the direction of the current structural trend
  (or the first break, which sets the trend). **CHoCH** (Change of Character): a break
  against the current trend; the trend flips.
* Consolidation: the last four confirmed swings span less than ``range_atr x ATR``.
* Transition: a CHoCH happened and no BOS has confirmed the new direction yet.

The state machine runs forward over bars, so the result at bar ``i`` only depends on
bars ``<= i``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.analysis.frame import AnalysisFrame

SwingLabel = Literal["HH", "LH", "EH", "HL", "LL", "EL", "H", "L"]


@dataclass(slots=True)
class Swing:
    index: int
    confirmed_at: int
    price: float
    kind: Literal["high", "low"]
    label: SwingLabel
    time: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StructureEvent:
    kind: Literal["BOS", "CHOCH", "WICK_BREAK"]
    direction: Literal["BULLISH", "BEARISH"]
    index: int  # bar whose close (or wick) broke the level
    time: str
    level: float
    swing_index: int
    swing_time: str
    close: float
    label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StructureState:
    index: int
    trend: Literal["UP", "DOWN", "UNKNOWN"]
    classification: str  # BULLISH | BEARISH | CONSOLIDATION | TRANSITION_UP | TRANSITION_DOWN | UNDETERMINED
    swings: list[Swing] = field(default_factory=list)
    events: list[StructureEvent] = field(default_factory=list)
    last_high: Swing | None = None
    last_low: Swing | None = None
    equal_highs: bool = False
    equal_lows: bool = False
    swing_range_atr: float | None = None
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _run(fr: AnalysisFrame, until: int) -> tuple[list[Swing], list[StructureEvent], str, int | None, int | None]:
    f, sp = fr.f, fr.params.structure
    pv = fr.pivots
    order = sorted(range(len(pv.index)), key=lambda k: (int(pv.confirmed_at[k]), int(pv.index[k])))
    swings: list[Swing] = []
    events: list[StructureEvent] = []
    trend = "UNKNOWN"
    last_choch: int | None = None
    last_bos: int | None = None
    hi: Swing | None = None
    lo: Swing | None = None
    hi_broken = lo_broken = True
    hi_wicked = lo_wicked = False
    prev_hi: Swing | None = None
    prev_lo: Swing | None = None
    p = 0
    for i in range(min(until, len(f) - 1) + 1):
        atr = f.atr[i] if math.isfinite(f.atr[i]) else math.nan
        while p < len(order) and int(pv.confirmed_at[order[p]]) <= i:
            k = order[p]
            p += 1
            j, price, is_high = int(pv.index[k]), float(pv.price[k]), bool(pv.is_high[k])
            tol = sp.equal_tolerance_atr * (f.atr[int(pv.confirmed_at[k])] if math.isfinite(f.atr[int(pv.confirmed_at[k])]) else 0)
            if is_high:
                ref = prev_hi
                label: SwingLabel = "H" if ref is None else "EH" if abs(price - ref.price) <= tol else (
                    "HH" if price > ref.price else "LH")
                s = Swing(j, int(pv.confirmed_at[k]), price, "high", label, fr.iso(j))
                prev_hi = hi = s
                hi_broken, hi_wicked = False, False
            else:
                ref = prev_lo
                label = "L" if ref is None else "EL" if abs(price - ref.price) <= tol else (
                    "HL" if price > ref.price else "LL")
                s = Swing(j, int(pv.confirmed_at[k]), price, "low", label, fr.iso(j))
                prev_lo = lo = s
                lo_broken, lo_wicked = False, False
            swings.append(s)
        if not math.isfinite(atr):
            continue
        buf = sp.break_buffer_atr * atr
        c = float(f.close[i])
        if hi is not None and not hi_broken:
            if c > hi.price + buf:
                kind = "BOS" if trend in ("UP", "UNKNOWN") else "CHOCH"
                events.append(StructureEvent(kind, "BULLISH", i, fr.iso(i), hi.price, hi.index, hi.time, c,
                                             f"{'Bullish BOS' if kind == 'BOS' else 'Bullish CHoCH'}: close {c:.6g} "
                                             f"above confirmed swing high {hi.price:.6g} ({hi.time})"))
                if kind == "CHOCH":
                    last_choch = i
                else:
                    last_bos = i
                trend = "UP"
                hi_broken = True
            elif f.high[i] > hi.price + buf and not hi_wicked:
                hi_wicked = True
                events.append(StructureEvent("WICK_BREAK", "BULLISH", i, fr.iso(i), hi.price, hi.index, hi.time, c,
                                             f"Wick above swing high {hi.price:.6g} without a confirming close "
                                             f"(temporary, structure unchanged)"))
        if lo is not None and not lo_broken:
            if c < lo.price - buf:
                kind = "BOS" if trend in ("DOWN", "UNKNOWN") else "CHOCH"
                events.append(StructureEvent(kind, "BEARISH", i, fr.iso(i), lo.price, lo.index, lo.time, c,
                                             f"{'Bearish BOS' if kind == 'BOS' else 'Bearish CHoCH'}: close {c:.6g} "
                                             f"below confirmed swing low {lo.price:.6g} ({lo.time})"))
                if kind == "CHOCH":
                    last_choch = i
                else:
                    last_bos = i
                trend = "DOWN"
                lo_broken = True
            elif f.low[i] < lo.price - buf and not lo_wicked:
                lo_wicked = True
                events.append(StructureEvent("WICK_BREAK", "BEARISH", i, fr.iso(i), lo.price, lo.index, lo.time, c,
                                             f"Wick below swing low {lo.price:.6g} without a confirming close "
                                             f"(temporary, structure unchanged)"))
    return swings, events, trend, last_choch, last_bos


def structure_at(fr: AnalysisFrame, i: int) -> StructureState:
    swings, events, trend, last_choch, last_bos = _run(fr, i)
    f, sp = fr.f, fr.params.structure
    st = StructureState(index=i, trend=trend, classification="UNDETERMINED")  # type: ignore[arg-type]
    st.swings = swings[-12:]
    st.events = [e for e in events if e.kind != "WICK_BREAK"][-8:] + [e for e in events if e.kind == "WICK_BREAK"][-3:]
    st.events.sort(key=lambda e: e.index)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    st.last_high = highs[-1] if highs else None
    st.last_low = lows[-1] if lows else None
    st.equal_highs = bool(highs and highs[-1].label == "EH")
    st.equal_lows = bool(lows and lows[-1].label == "EL")
    atr = f.atr[i]
    recent = swings[-4:]
    if len(recent) >= 4 and math.isfinite(atr) and atr > 0:
        rng = max(s.price for s in recent) - min(s.price for s in recent)
        st.swing_range_atr = rng / atr
    if trend == "UNKNOWN" or len(swings) < 4:
        st.classification = "UNDETERMINED"
        st.explanation = "Not enough confirmed swings and structural breaks to classify structure."
        return st
    hl = (st.last_high.label if st.last_high else "-", st.last_low.label if st.last_low else "-")
    if last_choch is not None and (last_bos is None or last_choch > last_bos):
        st.classification = "TRANSITION_UP" if trend == "UP" else "TRANSITION_DOWN"
        st.explanation = (f"Change of character at {fr.iso(last_choch)}: structure turned "
                          f"{'bullish' if trend == 'UP' else 'bearish'} but no break of structure has confirmed "
                          f"the new direction yet. Last swings {hl[0]}/{hl[1]}.")
    elif st.swing_range_atr is not None and st.swing_range_atr < sp.range_atr:
        st.classification = "CONSOLIDATION"
        st.explanation = (f"Last four confirmed swings span only {st.swing_range_atr:.1f} ATR "
                          f"(< {sp.range_atr:g}): price is consolidating. Last structural trend "
                          f"{trend.lower()}.")
    else:
        st.classification = "BULLISH" if trend == "UP" else "BEARISH"
        st.explanation = (f"{'Bullish' if trend == 'UP' else 'Bearish'} structure: last break was "
                          f"{'up' if trend == 'UP' else 'down'} (BOS at {fr.iso(last_bos) if last_bos is not None else 'n/a'}); "
                          f"last swings {hl[0]}/{hl[1]}.")
    return st


def all_structure_events(fr: AnalysisFrame) -> list[StructureEvent]:
    """Every confirmed BOS/CHoCH over the whole series (for validation and chart markers)."""
    return [e for e in _run(fr, len(fr) - 1)[1] if e.kind != "WICK_BREAK"]
