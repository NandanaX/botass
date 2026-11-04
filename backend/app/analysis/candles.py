"""Rule-based candlestick pattern recognition.

Notation per bar: range ``r = high - low``, body ``b = |close - open|``, upper wick
``uw = high - max(open, close)``, lower wick ``lw = min(open, close) - low``. ``P`` is
``CandleParams``. A pattern "ends" on its last candle and is only known once that candle
has closed.

Single candle
* Doji: ``b <= P.doji_body_pct * r``. Dragonfly: doji, ``uw <= 0.1 r`` and ``lw >= 0.6 r``.
  Gravestone: doji, ``lw <= 0.1 r`` and ``uw >= 0.6 r``.
* Hammer shape: ``b <= P.small_body_pct * r``, ``lw >= P.long_wick_ratio * b``, ``uw <= 0.25 r``.
  After a downtrend it is a **hammer** (bullish); after an uptrend a **hanging man** (bearish).
* Inverted-hammer shape: mirror (long upper wick). After a downtrend an **inverted
  hammer** (bullish); after an uptrend a **shooting star** (bearish).
* Marubozu: ``uw, lw <= P.marubozu_wick_pct * r`` and ``b >= 0.8 r``; direction = colour.

Two candles (previous ``1``, current ``0``)
* Engulfing: opposite colours, current body covers the previous body and is larger.
* Harami: previous body ``>= 0.5 r1``; current body inside it and ``<= 0.5 b1``; opposite colour.
* Piercing: previous bearish with body ``>= 0.5 r1``; current bullish, opens at/below the
  previous close and closes above the previous body midpoint but below its open.
  Dark cloud cover is the mirror.

Three candles
* Morning star: long bearish, small-bodied middle, bullish close above the first body's
  midpoint. Evening star is the mirror.
* Three white soldiers: three bullish candles, rising closes, bodies ``>= 0.5 r``, each
  opening inside the previous body, upper wicks ``<= 0.3 b``. Three black crows: mirror.

Trend context uses EMA20 slope and the close's side of EMA20 *before* the pattern's first
candle. A pattern never creates a trade by itself.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.analysis.frame import AnalysisFrame
from app.indicators.zones import Zone

Direction = Literal["BULLISH", "BEARISH", "NEUTRAL"]


@dataclass(slots=True)
class CandlePattern:
    name: str
    direction: Direction
    start_index: int
    end_index: int
    start_time: str
    end_time: str
    high: float
    low: float
    body_pct: float  # last candle body / range
    upper_wick_pct: float
    lower_wick_pct: float
    trend_context: Literal["UP", "DOWN", "FLAT"]
    in_context: bool  # direction agrees with the reversal/continuation context it needs
    nearest_level: dict[str, Any] | None
    level_distance_atr: float | None
    at_level: bool
    rvol: float | None
    volume_confirmed: bool
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parts(fr: AnalysisFrame, i: int) -> tuple[float, float, float, float, float, float, float, float]:
    f = fr.f
    o, h, lo, c = float(f.open[i]), float(f.high[i]), float(f.low[i]), float(f.close[i])
    r = h - lo
    b = abs(c - o)
    return o, h, lo, c, r, b, h - max(o, c), min(o, c) - lo


def trend_before(fr: AnalysisFrame, start: int) -> Literal["UP", "DOWN", "FLAT"]:
    f = fr.f
    j = start - 1
    lb = fr.params.candles.trend_lookback
    if j - lb < 0 or not (math.isfinite(f.ema_fast[j]) and math.isfinite(f.ema_fast[j - lb])):
        return "FLAT"
    up = f.ema_fast[j] > f.ema_fast[j - lb] and f.close[j] > f.ema_fast[j]
    down = f.ema_fast[j] < f.ema_fast[j - lb] and f.close[j] < f.ema_fast[j]
    return "UP" if up else "DOWN" if down else "FLAT"


def _raw_patterns(fr: AnalysisFrame, i: int) -> list[tuple[str, Direction, int, str]]:
    """(name, direction, start_index, rule detail) for patterns ending at bar ``i``."""
    P = fr.params.candles
    out: list[tuple[str, Direction, int, str]] = []
    o, h, lo, c, r, b, uw, lw = _parts(fr, i)
    if r <= 0:
        return out
    bull, bear = c > o, c < o
    tr = trend_before(fr, i)
    # single candle
    if b <= P.doji_body_pct * r:
        if uw <= 0.1 * r and lw >= 0.6 * r:
            out.append(("Dragonfly doji", "BULLISH", i, f"body {b / r:.0%} of range, lower wick {lw / r:.0%}"))
        elif lw <= 0.1 * r and uw >= 0.6 * r:
            out.append(("Gravestone doji", "BEARISH", i, f"body {b / r:.0%} of range, upper wick {uw / r:.0%}"))
        else:
            out.append(("Doji", "NEUTRAL", i, f"body {b / r:.0%} of range (indecision)"))
    else:
        small = b <= P.small_body_pct * r
        if small and lw >= P.long_wick_ratio * b and uw <= 0.25 * r:
            if tr == "DOWN":
                out.append(("Hammer", "BULLISH", i, f"lower wick {lw / max(b, 1e-12):.1f}x body after a downtrend"))
            elif tr == "UP":
                out.append(("Hanging man", "BEARISH", i, f"lower wick {lw / max(b, 1e-12):.1f}x body after an uptrend"))
        if small and uw >= P.long_wick_ratio * b and lw <= 0.25 * r:
            if tr == "DOWN":
                out.append(("Inverted hammer", "BULLISH", i, f"upper wick {uw / max(b, 1e-12):.1f}x body after a downtrend"))
            elif tr == "UP":
                out.append(("Shooting star", "BEARISH", i, f"upper wick {uw / max(b, 1e-12):.1f}x body after an uptrend"))
        if uw <= P.marubozu_wick_pct * r and lw <= P.marubozu_wick_pct * r and b >= 0.8 * r:
            out.append(("Bullish marubozu" if bull else "Bearish marubozu", "BULLISH" if bull else "BEARISH", i,
                        f"body {b / r:.0%} of range with almost no wicks"))
    # two candles
    if i >= 1:
        o1, h1, l1, c1, r1, b1, _, _ = _parts(fr, i - 1)
        bull1, bear1 = c1 > o1, c1 < o1
        if bear1 and bull and o <= c1 and c >= o1 and b > b1:
            out.append(("Bullish engulfing", "BULLISH", i - 1, "bullish body engulfs the previous bearish body"))
        if bull1 and bear and o >= c1 and c <= o1 and b > b1:
            out.append(("Bearish engulfing", "BEARISH", i - 1, "bearish body engulfs the previous bullish body"))
        if r1 > 0 and b1 >= 0.5 * r1 and max(o, c) <= max(o1, c1) and min(o, c) >= min(o1, c1) and b <= 0.5 * b1 and b > 0:
            if bear1 and bull:
                out.append(("Bullish harami", "BULLISH", i - 1, "small bullish body inside the previous bearish body"))
            elif bull1 and bear:
                out.append(("Bearish harami", "BEARISH", i - 1, "small bearish body inside the previous bullish body"))
        mid1 = (o1 + c1) / 2
        if r1 > 0 and bear1 and b1 >= 0.5 * r1 and bull and o <= c1 and mid1 < c < o1:
            out.append(("Piercing pattern", "BULLISH", i - 1, "closes above the midpoint of the previous bearish body"))
        if r1 > 0 and bull1 and b1 >= 0.5 * r1 and bear and o >= c1 and o1 < c < mid1:
            out.append(("Dark cloud cover", "BEARISH", i - 1, "closes below the midpoint of the previous bullish body"))
    # three candles
    if i >= 2:
        o2, h2, l2, c2, r2, b2, _, _ = _parts(fr, i - 2)
        o1, h1, l1, c1, r1, b1, uw1, _ = _parts(fr, i - 1)
        if r2 > 0 and r1 > 0:
            mid2 = (o2 + c2) / 2
            small1 = b1 <= P.small_body_pct * r1
            if c2 < o2 and b2 >= 0.5 * r2 and small1 and bull and c > mid2:
                out.append(("Morning star", "BULLISH", i - 2, "long bearish, small middle body, bullish close above "
                                                              "the first body's midpoint"))
            if c2 > o2 and b2 >= 0.5 * r2 and small1 and bear and c < mid2:
                out.append(("Evening star", "BEARISH", i - 2, "long bullish, small middle body, bearish close below "
                                                              "the first body's midpoint"))
        rows = [_parts(fr, k) for k in (i - 2, i - 1, i)]
        if all(x[3] > x[0] and x[4] > 0 and x[5] >= 0.5 * x[4] and x[6] <= 0.3 * x[5] for x in rows) and \
                rows[0][3] < rows[1][3] < rows[2][3] and \
                all(min(rows[k - 1][0], rows[k - 1][3]) <= rows[k][0] <= max(rows[k - 1][0], rows[k - 1][3]) for k in (1, 2)):
            out.append(("Three white soldiers", "BULLISH", i - 2, "three strong bullish candles with rising closes"))
        if all(x[3] < x[0] and x[4] > 0 and x[5] >= 0.5 * x[4] and x[7] <= 0.3 * x[5] for x in rows) and \
                rows[0][3] > rows[1][3] > rows[2][3] and \
                all(min(rows[k - 1][0], rows[k - 1][3]) <= rows[k][0] <= max(rows[k - 1][0], rows[k - 1][3]) for k in (1, 2)):
            out.append(("Three black crows", "BEARISH", i - 2, "three strong bearish candles with falling closes"))
    return out


REVERSAL_CONTEXT = {
    "Hammer": "DOWN", "Inverted hammer": "DOWN", "Dragonfly doji": "DOWN", "Bullish engulfing": "DOWN",
    "Bullish harami": "DOWN", "Piercing pattern": "DOWN", "Morning star": "DOWN",
    "Shooting star": "UP", "Hanging man": "UP", "Gravestone doji": "UP", "Bearish engulfing": "UP",
    "Bearish harami": "UP", "Dark cloud cover": "UP", "Evening star": "UP",
}


def detect_candles(fr: AnalysisFrame, i: int, zones: list[Zone] | None = None) -> list[CandlePattern]:
    """Patterns completed on the closed candle ``i`` with their context."""
    f, P = fr.f, fr.params.candles
    atr = float(f.atr[i]) if math.isfinite(f.atr[i]) else math.nan
    out: list[CandlePattern] = []
    for name, direction, start, detail in _raw_patterns(fr, i):
        o, h, lo, c, r, b, uw, lw = _parts(fr, i)
        tr = trend_before(fr, start)
        need = REVERSAL_CONTEXT.get(name)
        in_context = (need == tr) if need else (direction == "NEUTRAL" or
                                               (direction == "BULLISH" and tr != "DOWN") or
                                               (direction == "BEARISH" and tr != "UP"))
        hi = float(max(f.high[start: i + 1]))
        low = float(min(f.low[start: i + 1]))
        lvl = dist = None
        at_level = False
        if zones and math.isfinite(atr) and atr > 0:
            want = "SUPPORT" if direction == "BULLISH" else "RESISTANCE" if direction == "BEARISH" else None
            cands = [z for z in zones if z.status != "INVALIDATED" and (want is None or z.zone_type == want)]
            if cands:
                ref = low if direction == "BULLISH" else hi if direction == "BEARISH" else c
                z = min(cands, key=lambda z: 0 if z.lower <= ref <= z.upper else min(abs(ref - z.lower), abs(ref - z.upper)))
                d = 0.0 if z.lower <= ref <= z.upper else min(abs(ref - z.lower), abs(ref - z.upper)) / atr
                lvl = {"timeframe": z.timeframe, "zone_type": z.zone_type, "lower": z.lower, "upper": z.upper}
                dist = round(d, 2)
                at_level = d <= P.level_distance_atr
        rv = float(f.rvol[i]) if math.isfinite(f.rvol[i]) else None
        vol_ok = rv is not None and rv >= 1.2
        expl = (f"{name} ({direction.lower()}) at {fr.iso(i)}: {detail}. Prior trend {tr.lower()}"
                + (f"; {'at' if at_level else f'{dist:.1f} ATR from'} {lvl['timeframe']} {lvl['zone_type'].lower()} "
                   f"{lvl['lower']:.6g}-{lvl['upper']:.6g}" if lvl else "")
                + (f"; volume {rv:.2f}x average" if rv is not None else "") + ".")
        out.append(CandlePattern(
            name=name, direction=direction, start_index=start, end_index=i, start_time=fr.iso(start),
            end_time=fr.iso(i), high=hi, low=low, body_pct=round(b / r, 3) if r else 0.0,
            upper_wick_pct=round(uw / r, 3) if r else 0.0, lower_wick_pct=round(lw / r, 3) if r else 0.0,
            trend_context=tr, in_context=bool(in_context), nearest_level=lvl, level_distance_atr=dist,
            at_level=at_level, rvol=round(rv, 2) if rv is not None else None, volume_confirmed=vol_ok, explanation=expl))
    return out


def scan_candles(fr: AnalysisFrame, start: int, end: int) -> list[CandlePattern]:
    """All patterns completed on bars ``start..end`` (no zone context; for markers and validation)."""
    out: list[CandlePattern] = []
    for i in range(max(0, start), min(end, len(fr) - 1) + 1):
        out.extend(detect_candles(fr, i, None))
    return out
