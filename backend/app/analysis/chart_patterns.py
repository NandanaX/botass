"""Deterministic classical chart pattern detection (non-repainting).

Patterns are built from *major* confirmed pivots (``ChartPatternParams.pivot_window``).
Detection runs only on bars where a new major pivot confirms; each candidate is recorded
once in an event log with the pivots known at that bar, and its geometry is frozen. Its
status then evolves with later closes only:

* ``DEVELOPING``: geometry valid, no close beyond the breakout boundary yet.
* ``CONFIRMED``: a close beyond the breakout boundary by ``breakout_buffer_atr x ATR``.
* ``INVALIDATED``: a close beyond the invalidation level before confirmation.
* ``EXPIRED``: neither within ``max_age_bars`` of the last pivot.

The view at bar ``i`` is reconstructed from instances detected at bars ``<= i`` with
status computed from closes ``<= i``, so history never changes when new candles arrive.

Geometry (``tol = equal_tolerance_atr x ATR``, ``flat = flat_slope_atr x ATR`` per bar)

* Double top: highs ``H1, H2`` with low ``L`` between, ``|H1 - H2| <= tol``, height
  ``mean(H1, H2) - L >= min_height_atr x ATR``. Neckline ``L``; confirmed on a close below
  it; invalidated on a close above ``max(H1, H2)``; target ``L - height``. Double bottom:
  mirror.
* Head and shoulders: highs ``LS < H > RS`` (head above both shoulders by ``tol``),
  ``|LS - RS| <= 1.5 tol``, neckline through the two lows between them. Confirmed on a close
  below the neckline, invalidated above the head; target neckline minus the head's height
  above it. Inverse: mirror.
* Boundary patterns use least-squares lines through the last 2-3 highs and lows (they
  must alternate). Ascending triangle: flat top, rising lows. Descending: flat bottom,
  falling highs. Symmetrical: falling highs and rising lows. Rising wedge: both rising,
  lows steeper (converging). Falling wedge: both falling, highs steeper. Rectangle: both
  flat. Channel: both sloped the same way and parallel within ``flat``. Breakout is a
  close beyond either boundary; the pattern's expected direction is used for
  invalidation of directional patterns. Target: pattern height added at the breakout.
* Bull flag: an impulse of at least ``pole_min_atr x ATR`` into a swing high within 20
  bars, then a pullback with lower highs retracing at most half the pole within 40 bars.
  Confirmed on a close above the flag's upper line; invalidated below the flag low;
  target = breakout + pole. Bear flag: mirror.

Ambiguous structures (no rule satisfied) produce no pattern.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np

from app.analysis.frame import AnalysisFrame

Status = Literal["DEVELOPING", "CONFIRMED", "INVALIDATED", "EXPIRED"]


@dataclass(slots=True)
class Line:
    i1: int
    p1: float
    i2: int
    p2: float
    label: str

    def at(self, i: int) -> float:
        if self.i2 == self.i1:
            return self.p1
        return self.p1 + (self.p2 - self.p1) * (i - self.i1) / (self.i2 - self.i1)


@dataclass(slots=True)
class PatternInstance:
    id: str
    name: str
    category: Literal["reversal", "continuation"]
    expected: Literal["BULLISH", "BEARISH", "EITHER"]
    pivots: list[tuple[int, float, str]]  # (bar, price, "high"/"low")
    upper: Line | None  # boundary used for an upside breakout
    lower: Line | None  # boundary used for a downside breakout
    invalidation_up: float | None  # close above -> invalid (bearish patterns)
    invalidation_down: float | None  # close below -> invalid (bullish patterns)
    height: float
    detected_at: int
    last_pivot: int
    rule: str


@dataclass(slots=True)
class ChartPattern:
    id: str
    name: str
    category: str
    expected: str
    status: Status
    breakout_direction: str | None
    detected_at: str
    detected_index: int
    resolved_at: str | None
    resolved_index: int | None
    pivots: list[dict[str, Any]]
    lines: list[dict[str, Any]]
    breakout_level: float | None
    invalidation_level: float | None
    target: float | None
    height: float
    explanation: str
    version: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fit(points: list[tuple[int, float]]) -> tuple[float, float]:
    """Least-squares slope/intercept (price per bar)."""
    x = np.array([p[0] for p in points], dtype=float)
    y = np.array([p[1] for p in points], dtype=float)
    if len(points) == 1 or np.ptp(x) == 0:
        return 0.0, float(y.mean())
    s, b = np.polyfit(x, y, 1)
    return float(s), float(b)


def _alternating(pv: list[tuple[int, float, str]]) -> list[tuple[int, float, str]]:
    out: list[tuple[int, float, str]] = []
    for p in pv:
        if out and out[-1][2] == p[2]:
            keep = (p if (p[1] > out[-1][1]) == (p[2] == "high") else out[-1])
            out[-1] = keep
        else:
            out.append(p)
    return out


def _sig(name: str, pts: list[tuple[int, float, str]]) -> str:
    return hashlib.sha1((name + "|" + ",".join(str(p[0]) for p in pts)).encode()).hexdigest()[:12]


def _candidates(fr: AnalysisFrame, t: int) -> list[PatternInstance]:
    f, P = fr.f, fr.params.patterns
    atr = float(f.atr[t])
    if not math.isfinite(atr) or atr <= 0:
        return []
    mp = fr.major_pivots
    known = [(int(mp.index[k]), float(mp.price[k]), "high" if mp.is_high[k] else "low")
             for k in range(len(mp.index)) if mp.confirmed_at[k] <= t and mp.index[k] >= t - P.max_age_bars]
    known.sort()
    seq = _alternating(known)[-P.lookback_pivots:]
    if len(seq) < 3:
        return []
    tol, flat, min_h = P.equal_tolerance_atr * atr, P.flat_slope_atr * atr, P.min_height_atr * atr
    out: list[PatternInstance] = []
    last_pivot = seq[-1][0]
    highs = [p for p in seq if p[2] == "high"]
    lows = [p for p in seq if p[2] == "low"]

    # --- double top / bottom (most recent same-kind pair with the opposite pivot between)
    for kind, name, exp in (("high", "Double top", "BEARISH"), ("low", "Double bottom", "BULLISH")):
        same = [k for k, p in enumerate(seq) if p[2] == kind]
        if len(same) >= 2 and same[-1] - same[-2] == 2:
            a, m, b = seq[same[-2]], seq[same[-2] + 1], seq[same[-1]]
            height = abs((a[1] + b[1]) / 2 - m[1])
            if abs(a[1] - b[1]) <= tol and height >= min_h:
                neck = Line(m[0], m[1], m[0], m[1], "neckline")
                extreme = max(a[1], b[1]) if kind == "high" else min(a[1], b[1])
                pts = [a, m, b]
                out.append(PatternInstance(
                    _sig(name, pts), name, "reversal", exp, pts,
                    upper=neck if kind == "low" else None, lower=neck if kind == "high" else None,
                    invalidation_up=extreme if kind == "high" else None,
                    invalidation_down=extreme if kind == "low" else None,
                    height=height, detected_at=t, last_pivot=b[0],
                    rule=f"two {'tops' if kind == 'high' else 'bottoms'} {a[1]:.6g} and {b[1]:.6g} within "
                         f"{P.equal_tolerance_atr:g} ATR, neckline {m[1]:.6g}, height {height / atr:.1f} ATR"))

    # --- head and shoulders / inverse
    for kind, name, exp in (("high", "Head and shoulders", "BEARISH"), ("low", "Inverse head and shoulders", "BULLISH")):
        idx = [k for k, p in enumerate(seq) if p[2] == kind]
        if len(idx) >= 3 and idx[-1] - idx[-3] == 4:
            ls, h, rs = seq[idx[-3]], seq[idx[-2]], seq[idx[-1]]
            n1, n2 = seq[idx[-3] + 1], seq[idx[-2] + 1]
            sign = 1 if kind == "high" else -1
            if sign * (h[1] - ls[1]) > tol and sign * (h[1] - rs[1]) > tol and abs(ls[1] - rs[1]) <= 1.5 * tol:
                neck = Line(n1[0], n1[1], n2[0], n2[1], "neckline")
                height = abs(h[1] - neck.at(h[0]))
                if height >= min_h:
                    pts = [ls, n1, h, n2, rs]
                    out.append(PatternInstance(
                        _sig(name, pts), name, "reversal", exp, pts,
                        upper=neck if kind == "low" else None, lower=neck if kind == "high" else None,
                        invalidation_up=h[1] if kind == "high" else None,
                        invalidation_down=h[1] if kind == "low" else None,
                        height=height, detected_at=t, last_pivot=rs[0],
                        rule=f"head {h[1]:.6g} beyond both shoulders ({ls[1]:.6g}, {rs[1]:.6g}) by more than "
                             f"{P.equal_tolerance_atr:g} ATR; neckline through {n1[1]:.6g} and {n2[1]:.6g}"))

    # --- flags (pole into the last major swing of the pole direction, then a counter pullback)
    for kind, name, exp in (("high", "Bull flag", "BULLISH"), ("low", "Bear flag", "BEARISH")):
        idx = [k for k, p in enumerate(seq) if p[2] == kind]
        if not idx:
            continue
        k0 = None
        for k in reversed(idx):
            if len(seq) - 1 - k >= 2:  # at least a counter swing and a new same-kind swing after
                k0 = k
                break
        if k0 is None:
            continue
        top = seq[k0]
        lo_i = max(0, top[0] - 20)
        seg = f.low[lo_i: top[0] + 1] if kind == "high" else f.high[lo_i: top[0] + 1]
        start = float(seg.min() if kind == "high" else seg.max())
        pole = abs(top[1] - start)
        after = seq[k0 + 1:]
        if pole < P.pole_min_atr * atr or not after or after[-1][0] - top[0] > 40:
            continue
        same_after = [p for p in after if p[2] == kind]
        opp_after = [p for p in after if p[2] != kind]
        sign = 1 if kind == "high" else -1
        if not same_after or not opp_after:
            continue
        retrace = max(sign * (top[1] - p[1]) for p in opp_after)
        if retrace > 0.5 * pole or any(sign * (p[1] - top[1]) >= 0 for p in same_after):
            continue
        s_up, b_up = _fit([(top[0], top[1])] + [(p[0], p[1]) for p in same_after])
        s_lo, b_lo = _fit([(p[0], p[1]) for p in opp_after])
        if sign * s_up > flat:  # flag must slope against (or sideways to) the pole
            continue
        upper_line = Line(top[0], s_up * top[0] + b_up, after[-1][0], s_up * after[-1][0] + b_up, "flag upper")
        lower_line = Line(opp_after[0][0], s_lo * opp_after[0][0] + b_lo, after[-1][0],
                          s_lo * after[-1][0] + b_lo, "flag lower")
        pts = [top] + after
        extreme_opp = (min if kind == "high" else max)(p[1] for p in opp_after)
        out.append(PatternInstance(
            _sig(name, pts), name, "continuation", exp, pts,
            upper=upper_line if kind == "high" else None, lower=lower_line if kind == "low" else None,
            invalidation_up=extreme_opp if kind == "low" else None,
            invalidation_down=extreme_opp if kind == "high" else None,
            height=pole, detected_at=t, last_pivot=after[-1][0],
            rule=f"{'up' if kind == 'high' else 'down'}ward pole {pole / atr:.1f} ATR into {top[1]:.6g}, then a "
                 f"pullback retracing {retrace / pole:.0%} of it"))
        if kind == "high":
            out[-1].lower = lower_line
        else:
            out[-1].upper = upper_line

    # --- boundary patterns from the last 2-3 highs and lows
    hs, ls_ = highs[-3:], lows[-3:]
    if len(hs) >= 2 and len(ls_) >= 2:
        first = min(hs[0][0], ls_[0][0])
        su, bu = _fit([(p[0], p[1]) for p in hs])
        sl, bl = _fit([(p[0], p[1]) for p in ls_])
        up0, lo0 = su * first + bu, sl * first + bl
        up1, lo1 = su * last_pivot + bu, sl * last_pivot + bl
        height = up0 - lo0
        fu, fl = abs(su) <= flat, abs(sl) <= flat
        name = exp = None
        if height >= min_h and up1 > lo1:
            converging = (up1 - lo1) < (up0 - lo0) * 0.85
            if fu and fl:
                name, exp = "Rectangle range", "EITHER"
            elif fu and sl > flat and converging:
                name, exp = "Ascending triangle", "BULLISH"
            elif fl and su < -flat and converging:
                name, exp = "Descending triangle", "BEARISH"
            elif su < -flat and sl > flat and converging:
                name, exp = "Symmetrical triangle", "EITHER"
            elif su > flat and sl > flat and converging and sl > su:
                name, exp = "Rising wedge", "BEARISH"
            elif su < -flat and sl < -flat and converging and su < sl:
                name, exp = "Falling wedge", "BULLISH"
            elif not fu and not fl and np.sign(su) == np.sign(sl) and abs(su - sl) <= 2 * flat:
                name, exp = ("Ascending channel", "EITHER") if su > 0 else ("Descending channel", "EITHER")
        if name:
            pts = sorted(hs + ls_)
            upper = Line(first, up0, last_pivot, up1, "upper boundary")
            lower = Line(first, lo0, last_pivot, lo1, "lower boundary")
            out.append(PatternInstance(
                _sig(name, pts), name, "reversal" if "wedge" in name else "continuation", exp, pts,
                upper=upper, lower=lower,
                invalidation_up=None, invalidation_down=None, height=height, detected_at=t, last_pivot=last_pivot,
                rule=f"upper slope {su / atr:+.3f} ATR/bar through {len(hs)} highs, lower slope {sl / atr:+.3f} "
                     f"ATR/bar through {len(ls_)} lows, height {height / atr:.1f} ATR"))
    return out


def pattern_log(fr: AnalysisFrame) -> list[PatternInstance]:
    """Every pattern instance in detection order (computed once per frame and cached)."""
    cached = fr.cache.get("pattern_log")
    if cached is not None:
        return cached
    seen: set[str] = set()
    log: list[PatternInstance] = []
    for t in sorted(set(int(x) for x in fr.major_pivots.confirmed_at)):
        if t >= len(fr):
            continue
        for c in _candidates(fr, t):
            if c.id not in seen:
                seen.add(c.id)
                log.append(c)
    fr.cache["pattern_log"] = log
    return log


def resolve(fr: AnalysisFrame, inst: PatternInstance, i: int) -> ChartPattern:
    """Status of ``inst`` as of bar ``i`` (closes ``<= i`` only)."""
    f, P = fr.f, fr.params.patterns
    status: Status = "DEVELOPING"
    direction = None
    resolved = None
    for k in range(inst.detected_at, i + 1):
        atr = float(f.atr[k]) if math.isfinite(f.atr[k]) else 0.0
        buf = P.breakout_buffer_atr * atr
        c = float(f.close[k])
        if inst.invalidation_up is not None and c > inst.invalidation_up + buf:
            status, resolved = "INVALIDATED", k
            break
        if inst.invalidation_down is not None and c < inst.invalidation_down - buf:
            status, resolved = "INVALIDATED", k
            break
        up = inst.upper.at(k) if inst.upper else None
        dn = inst.lower.at(k) if inst.lower else None
        if up is not None and c > up + buf:
            direction = "BULLISH"
        elif dn is not None and c < dn - buf:
            direction = "BEARISH"
        if direction:
            if inst.expected not in ("EITHER", direction):
                status = "INVALIDATED"
            else:
                status = "CONFIRMED"
            resolved = k
            break
        if k - inst.last_pivot > P.max_age_bars:
            status, resolved = "EXPIRED", k
            break
    end = resolved if resolved is not None else i
    lines = []
    for ln in (inst.upper, inst.lower):
        if ln is not None:
            x0 = min(ln.i1, inst.pivots[0][0])
            lines.append({"t1": fr.unix(x0), "p1": ln.at(x0), "t2": fr.unix(end), "p2": ln.at(end), "label": ln.label})
    if inst.name.startswith("Double") or "shoulders" in inst.name:
        pts = inst.pivots
        lines.append({"t1": fr.unix(pts[0][0]), "p1": pts[0][1], "t2": fr.unix(pts[-1][0]), "p2": pts[-1][1],
                      "label": "pattern", "dashed": True})
    breakout_dir = direction if status == "CONFIRMED" else None
    level = None
    target = None
    if status == "CONFIRMED" and resolved is not None:
        level = (inst.upper.at(resolved) if direction == "BULLISH" and inst.upper else
                 inst.lower.at(resolved) if inst.lower else None)
        if level is not None:
            target = level + inst.height if direction == "BULLISH" else level - inst.height
    else:
        exp = inst.expected
        if exp in ("BULLISH", "EITHER") and inst.upper:
            level = inst.upper.at(i)
            target = level + inst.height if exp == "BULLISH" else None
        if exp == "BEARISH" and inst.lower:
            level = inst.lower.at(i)
            target = level - inst.height
    inval = inst.invalidation_up if inst.invalidation_up is not None else inst.invalidation_down
    if inval is None and inst.expected != "EITHER":
        inval = (inst.lower.at(end) if inst.expected == "BULLISH" and inst.lower else
                 inst.upper.at(end) if inst.upper else None)
    text = (f"{inst.name}: {inst.rule}. Detected {fr.iso(inst.detected_at)} (after the last pivot confirmed). ")
    if status == "CONFIRMED":
        text += (f"Confirmed {fr.iso(resolved)} by a close {'above' if direction == 'BULLISH' else 'below'} "
                 f"{level:.6g}; theoretical measured-move target {target:.6g} (not a forecast).")
    elif status == "INVALIDATED":
        text += f"Invalidated {fr.iso(resolved)}."
    elif status == "EXPIRED":
        text += "Expired without a breakout."
    else:
        text += ("Developing: no confirming close yet"
                 + (f"; breakout level {level:.6g}" if level is not None else "")
                 + (f", invalidation {inval:.6g}" if inval is not None else "") + ".")
    return ChartPattern(
        id=inst.id, name=inst.name, category=inst.category, expected=inst.expected, status=status,
        breakout_direction=breakout_dir, detected_at=fr.iso(inst.detected_at), detected_index=inst.detected_at,
        resolved_at=fr.iso(resolved) if resolved is not None else None, resolved_index=resolved,
        pivots=[{"time": fr.unix(p[0]), "price": p[1], "kind": p[2]} for p in inst.pivots],
        lines=lines, breakout_level=level, invalidation_level=inval, target=target, height=inst.height,
        explanation=text, version=fr.params.version)


def patterns_at(fr: AnalysisFrame, i: int, recent_bars: int | None = None) -> list[ChartPattern]:
    """Patterns known at bar ``i``: developing ones, plus those resolved within ``recent_bars``."""
    recent = recent_bars if recent_bars is not None else fr.params.patterns.max_age_bars // 3
    out = []
    for inst in pattern_log(fr):
        if inst.detected_at > i:
            break
        cp = resolve(fr, inst, i)
        if cp.status == "DEVELOPING" or (cp.resolved_index is not None and i - cp.resolved_index <= recent):
            out.append(cp)
    # one instance per (pattern name, status): the most recent, to avoid stacking near-duplicates
    latest: dict[tuple[str, str], ChartPattern] = {}
    for cp in out:
        latest[(cp.name, cp.status)] = cp
    return sorted(latest.values(), key=lambda c: c.detected_index)
