"""Trend, momentum, volatility and volume classifications plus divergence detection.

Divergence (confirmed pivots only, reported from the second pivot's confirmation bar and
kept for ``recent`` bars):

* Bearish regular divergence: the last two confirmed swing highs make a higher high while
  RSI (or the MACD histogram) at those bars makes a lower high.
* Bullish regular divergence: lower low in price, higher low in the oscillator.

Overbought/oversold readings are context, not signals: RSI above 70 in an uptrend is
reported as "stretched" and is never counted as a sell by itself.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.analysis.frame import AnalysisFrame
from app.analysis.indicators import slope


def divergences(fr: AnalysisFrame, i: int, recent: int = 20) -> list[dict[str, Any]]:
    f, pv = fr.f, fr.pivots
    out: list[dict[str, Any]] = []
    for is_high in (True, False):
        pts = [(int(pv.index[k]), float(pv.price[k]), int(pv.confirmed_at[k])) for k in range(len(pv.index))
               if pv.confirmed_at[k] <= i and bool(pv.is_high[k]) == is_high]
        if len(pts) < 2:
            continue
        (a, pa, _), (b, pb, cb) = pts[-2], pts[-1]
        if i - cb > recent or b - a > 120:
            continue
        for name, osc in (("RSI", f.rsi), ("MACD histogram", f.macd_hist)):
            oa, ob = osc[a], osc[b]
            if not (math.isfinite(oa) and math.isfinite(ob)):
                continue
            if is_high and pb > pa and ob < oa:
                out.append({"kind": f"Bearish {name} divergence", "direction": -1, "indicator": name,
                            "time": fr.iso(cb), "text": f"Bearish {name} divergence: price higher high "
                            f"{pa:.6g}->{pb:.6g} ({fr.iso(a)}, {fr.iso(b)}) while {name} fell {oa:.3g}->{ob:.3g}."})
            if not is_high and pb < pa and ob > oa:
                out.append({"kind": f"Bullish {name} divergence", "direction": 1, "indicator": name,
                            "time": fr.iso(cb), "text": f"Bullish {name} divergence: price lower low "
                            f"{pa:.6g}->{pb:.6g} ({fr.iso(a)}, {fr.iso(b)}) while {name} rose {oa:.3g}->{ob:.3g}."})
    return out


def trend_class(fr: AnalysisFrame, i: int, structure_cls: str) -> dict[str, Any]:
    f = fr.f
    c = float(f.close[i])
    mas = [("EMA9", fr.ema9[i]), ("EMA20", f.ema_fast[i]), ("EMA50", f.ema_mid[i]), ("EMA100", fr.ema100[i]),
           ("EMA200", f.ema_slow[i])]
    avail = [(n, float(v)) for n, v in mas if math.isfinite(v)]
    bull_pairs = sum(1 for (_, a), (_, b) in zip(avail, avail[1:], strict=False) if a > b)
    bear_pairs = sum(1 for (_, a), (_, b) in zip(avail, avail[1:], strict=False) if a < b)
    pairs = max(1, len(avail) - 1)
    adx_v = float(fr.adx[i]) if math.isfinite(fr.adx[i]) else None
    trending = adx_v is not None and adx_v >= fr.params.adx_trend
    if bull_pairs == pairs and c > avail[-1][1] if avail else False:
        ma = "BULLISH"
    elif bear_pairs == pairs and c < avail[-1][1] if avail else False:
        ma = "BEARISH"
    else:
        ma = "MIXED"
    if structure_cls == "CONSOLIDATION" or (adx_v is not None and adx_v < 20 and ma == "MIXED"):
        trend = "SIDEWAYS"
    elif ma == "BULLISH" or (ma == "MIXED" and structure_cls in ("BULLISH", "TRANSITION_UP") and trending):
        trend = "BULLISH"
    elif ma == "BEARISH" or (ma == "MIXED" and structure_cls in ("BEARISH", "TRANSITION_DOWN") and trending):
        trend = "BEARISH"
    else:
        trend = "SIDEWAYS"
    return {"trend": trend, "ma_alignment": ma, "bull_pairs": bull_pairs, "bear_pairs": bear_pairs, "pairs": pairs,
            "adx": adx_v, "trending": trending, "plus_di": float(fr.plus_di[i]) if math.isfinite(fr.plus_di[i]) else None,
            "minus_di": float(fr.minus_di[i]) if math.isfinite(fr.minus_di[i]) else None,
            "supertrend": int(fr.supertrend_dir[i]), "supertrend_line": float(fr.supertrend[i]) if math.isfinite(fr.supertrend[i]) else None,
            "mas": {n: v for n, v in avail}, "sma50": float(fr.sma50[i]) if math.isfinite(fr.sma50[i]) else None,
            "sma200": float(f.sma_long[i]) if math.isfinite(f.sma_long[i]) else None}


def momentum_class(fr: AnalysisFrame, i: int) -> dict[str, Any]:
    f = fr.f
    rsi = float(f.rsi[i]) if math.isfinite(f.rsi[i]) else None
    hist = float(f.macd_hist[i]) if math.isfinite(f.macd_hist[i]) else None
    hist_prev = float(f.macd_hist[i - 1]) if i > 0 and math.isfinite(f.macd_hist[i - 1]) else None
    accel = None if hist is None or hist_prev is None else hist - hist_prev
    k = float(fr.stoch_k[i]) if math.isfinite(fr.stoch_k[i]) else None
    srk = float(fr.stochrsi_k[i]) if math.isfinite(fr.stochrsi_k[i]) else None
    if rsi is None or hist is None:
        label = "UNKNOWN"
    elif rsi > 55 and hist > 0:
        label = "STRONG BULLISH" if accel and accel > 0 else "BULLISH, DECELERATING"
    elif rsi < 45 and hist < 0:
        label = "STRONG BEARISH" if accel and accel < 0 else "BEARISH, DECELERATING"
    elif hist > 0:
        label = "MILD BULLISH"
    elif hist < 0:
        label = "MILD BEARISH"
    else:
        label = "NEUTRAL"
    stretch = None
    if rsi is not None:
        stretch = "OVERBOUGHT (stretched, not a sell signal by itself)" if rsi >= 70 else \
            "OVERSOLD (stretched, not a buy signal by itself)" if rsi <= 30 else None
    return {"label": label, "rsi": rsi, "rsi_slope": slope(f.rsi, i, 3), "macd": float(f.macd[i]) if math.isfinite(f.macd[i]) else None,
            "macd_signal": float(f.macd_signal[i]) if math.isfinite(f.macd_signal[i]) else None,
            "macd_hist": hist, "macd_accel": accel, "stoch_k": k,
            "stoch_d": float(fr.stoch_d[i]) if math.isfinite(fr.stoch_d[i]) else None,
            "stochrsi_k": srk, "stretch": stretch}


def volatility_class(fr: AnalysisFrame, i: int) -> dict[str, Any]:
    f = fr.f
    atr = float(f.atr[i]) if math.isfinite(f.atr[i]) else None
    atr_pct = atr / float(f.close[i]) * 100 if atr else None
    w = fr.bb_width
    window = w[max(0, i - 100): i + 1]
    window = window[np.isfinite(window)]
    pct = float((window < w[i]).mean() * 100) if len(window) >= 20 and math.isfinite(w[i]) else None
    prev = w[i - 5] if i >= 5 else math.nan
    if pct is None:
        label = "UNKNOWN"
    elif pct <= 20:
        label = "LOW (contraction)"
    elif pct >= 80:
        label = "HIGH (expansion)"
    else:
        label = "NORMAL"
    trend = None
    if math.isfinite(w[i]) and math.isfinite(prev):
        trend = "EXPANDING" if w[i] > prev * 1.1 else "CONTRACTING" if w[i] < prev * 0.9 else "STABLE"
    return {"label": label, "atr": atr, "atr_pct": atr_pct, "bb_width": float(w[i]) if math.isfinite(w[i]) else None,
            "bb_width_percentile": pct, "trend": trend}


def volume_class(fr: AnalysisFrame, i: int) -> dict[str, Any]:
    f = fr.f
    rv = float(f.rvol[i]) if math.isfinite(f.rvol[i]) else None
    obv_s = slope(fr.obv, i, 10)
    px_s = slope(f.close, i, 10)
    div = None
    if math.isfinite(obv_s) and math.isfinite(px_s):
        if px_s > 0 > obv_s:
            div = "BEARISH (price rising on falling OBV)"
        elif px_s < 0 < obv_s:
            div = "BULLISH (price falling on rising OBV)"
    vw = float(fr.vwap[i]) if fr.vwap is not None and math.isfinite(fr.vwap[i]) else None
    spike = rv is not None and rv >= 2.0
    return {"rvol": rv, "spike": spike, "obv_slope": obv_s if math.isfinite(obv_s) else None, "obv_divergence": div,
            "vwap": vw, "above_vwap": None if vw is None else bool(f.close[i] > vw),
            "candle_direction": 1 if f.close[i] > f.open[i] else -1 if f.close[i] < f.open[i] else 0}
