"""Signal forensics: rebuild a stored signal from the data available at its decision time
and explain its outcome with evidence.

For a stored signal at closed candle ``i`` (open time ``T``):

1. **Reconstruction.** Candles are truncated at ``T`` (nothing later is visible), higher
   timeframes at the decision time (signal close). Features are computed on the same
   window length the live scanner uses (``live_window`` candles) and, as a check, on the
   full history. ``evaluate_at`` is re-run and its decision compared with what was stored.
2. **Outcome verification.** The stored outcome is replayed independently: (a) gross, OHLC,
   no costs (should equal the stored methodology), (b) with fees, slippage and funding,
   and (c) on 1-minute candles when available, which reveals the real intrabar order.
3. **Diagnostics** (all measured at the decision close): regime, higher-timeframe regime,
   entry quality (extension, run, room, swing distance, momentum), stop distance vs recent
   noise, correlated same-direction signals, BTC context. Post-signal facts (MFE/MAE,
   whether the target was reached after the stop, failed breakout) come from later
   candles and are reported separately as *outcome evidence*, never as decision inputs.
4. **Causes**: every hypothesis is ``confirmed`` (measured and consistent with how the
   trade failed), ``suspected`` (condition present, causal link not provable from one
   trade) or ``undetermined``. Nothing is inferred without a measurement.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from app.indicators.zones import Zone
from app.research.candidates import noise_distance
from app.research.entry_quality import EntryQualityParams, classify_entry, entry_features
from app.research.regime import RegimeParams, regime_alignment, regime_arrays
from app.research.simulate import (
    ZERO_COSTS,
    CostModel,
    path_with_lower_tf,
    prepare_funding,
    resolve_ambiguous_with_lower_tf,
    simulate_trade,
)
from app.risk.sizing import InstrumentRules, SizingConfig
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams
from app.strategy.setups import zones_at
from app.timeutil import tf_seconds

LIVE_WINDOW = 1000


def _iso(ns: int) -> str:
    return pd.Timestamp(int(ns), tz="UTC").isoformat()


def _r(x: float | None, d: int = 4) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), d)


def reconstruct(signal: dict[str, Any], df: pd.DataFrame, htf_dfs: dict[str, pd.DataFrame], params: StrategyParams,
                window: int | None = LIVE_WINDOW, sizing: SizingConfig | None = None,
                rules: InstrumentRules | None = None) -> dict[str, Any]:
    """Re-run the decision at the signal candle using only data available then.

    Without instrument ``rules`` prices are not tick-rounded, so prices are compared with
    a relative tolerance of 0.1%% (rounding only); with rules they must match exactly."""
    tf = signal["timeframe"]
    t_open = pd.Timestamp(signal["candle_open_time"])
    hist = df[df["open_time"] <= t_open].reset_index(drop=True)
    if not len(hist) or hist["open_time"].iloc[-1] != t_open:
        return {"ok": False, "error": "signal candle not found in history"}
    if window:
        hist = hist.iloc[-window:].reset_index(drop=True)
    decision = t_open + pd.Timedelta(seconds=tf_seconds(tf))
    htf_cut = {k: v[v["open_time"] + pd.Timedelta(seconds=tf_seconds(k)) <= decision].reset_index(drop=True)
               for k, v in htf_dfs.items()}
    if window:
        htf_cut = {k: v.iloc[-window:].reset_index(drop=True) for k, v in htf_cut.items()}
    f = compute_features(hist, tf, params)
    htf = {k: compute_features(v, k, params) for k, v in htf_cut.items() if len(v)}
    i = len(f) - 1
    ev = evaluate_at(f, i, params, htf, sizing=sizing, rules=rules, context=True, symbol=signal["symbol"])
    st = ev.setup
    stored = {"direction": signal["direction"], "strategy": signal.get("strategy_type"),
              "entry_reference": signal["entry_reference"], "stop_loss": signal["stop_loss"],
              "take_profit": signal["take_profit"], "score": signal["score"]}
    recomputed = None if st is None else {"direction": st.direction, "strategy": st.strategy,
                                          "entry_reference": st.entry_reference, "stop_loss": st.stop_loss,
                                          "take_profit": st.take_profit, "score": st.score}
    diffs = []
    tol = 1e-9 if rules is not None else 1e-3
    if recomputed is None:
        diffs.append("no QUALIFIED setup when recomputed")
    else:
        for k in stored:
            a, b = stored[k], recomputed[k]
            if isinstance(a, float) and isinstance(b, float):
                if abs(a - b) > tol * abs(a):
                    diffs.append(f"{k}: stored {a} vs recomputed {b}")
            elif a != b:
                diffs.append(f"{k}: stored {a} vs recomputed {b}")
    return {"ok": True, "matches": not diffs, "differences": diffs, "stored": stored, "recomputed": recomputed,
            "candles_used": len(f), "window": window, "features": f, "htf_features": htf, "index": i,
            "evaluation": ev}


def _zone_dist(zones: list[Zone], kind: str, price: float, atr: float) -> dict[str, Any] | None:
    cands = [z for z in zones if z.zone_type == kind and z.status != "INVALIDATED"]
    if not cands:
        return None
    if kind == "SUPPORT":
        z = max(cands, key=lambda z: z.upper)
        d = (price - z.upper) / atr
    else:
        z = min(cands, key=lambda z: z.lower)
        d = (z.lower - price) / atr
    return {"timeframe": z.timeframe, "lower": z.lower, "upper": z.upper, "tests": z.tests, "strength": z.strength,
            "distance_atr": _r(d, 3)}


def analyze_signal(
    signal: dict[str, Any], df: pd.DataFrame, htf_dfs: dict[str, pd.DataFrame], params: StrategyParams,
    max_bars: int, costs: CostModel = CostModel(), funding: pd.DataFrame | None = None,
    minute: pd.DataFrame | None = None, peers: list[dict[str, Any]] | None = None,
    btc: dict[str, pd.DataFrame] | None = None, corr_fn=None,
    rp: RegimeParams | None = None, ep: EntryQualityParams | None = None, lookback: int = 100,
    sizing: SizingConfig | None = None, rules: InstrumentRules | None = None,
) -> dict[str, Any]:
    rec = reconstruct(signal, df, htf_dfs, params, LIVE_WINDOW, sizing, rules)
    if not rec["ok"]:
        return {"signal_id": signal["id"], "error": rec["error"]}
    f = rec["features"]
    i = rec["index"]
    tf = signal["timeframe"]
    direction = signal["direction"]
    long = direction == "LONG"
    rec_full = reconstruct(signal, df, htf_dfs, params, None, sizing, rules)
    ra = regime_arrays(f, rp)
    atr = float(f.atr[i])
    entry_ref = float(signal["entry_reference"])
    stop, target = float(signal["stop_loss"]), float(signal["take_profit"])
    plan = signal.get("plan") or {}
    struct_level = (plan.get("stop") or {}).get("structural_level")
    feat = entry_features(f, ra, i, direction, plan.get("room_r"),
                          struct_level if signal.get("strategy_type") == "breakout" else None)
    ecls, ewhy = classify_entry(feat, direction, ep)
    noise = noise_distance(f, i, direction)
    stop_dist = abs(entry_ref - stop)
    zones = zones_at(f, i, params, signal["symbol"])
    htf_regimes = {}
    for k, hf in rec["htf_features"].items():
        hra = regime_arrays(hf, rp)
        htf_regimes[k] = hra.at(len(hf) - 1)

    # ---- outcome replay on the full future (labels only)
    bar_ns = tf_seconds(tf) * 1_000_000_000
    fut = df.reset_index(drop=True)
    ff = compute_features(fut, tf, params)
    j = int(np.searchsorted(ff.open_time, int(f.open_time[i])))
    fund = prepare_funding(funding)
    gross = simulate_trade(direction, ff.open, ff.high, ff.low, ff.close, ff.open_time, bar_ns, j, stop, target,
                           max_bars, ZERO_COSTS, None, allow_open=True)
    net = simulate_trade(direction, ff.open, ff.high, ff.low, ff.close, ff.open_time, bar_ns, j, stop, target,
                         max_bars, costs, fund, allow_open=True)
    ambiguous_resolution = None
    if gross.reason == "AMBIGUOUS_STOP" and minute is not None:
        ambiguous_resolution = resolve_ambiguous_with_lower_tf(direction, stop, target,
                                                               int(ff.open_time[gross.exit_index]), bar_ns, minute)
    minute_path = None
    if minute is not None and gross.status != "INVALIDATED":
        minute_path = path_with_lower_tf(direction, int(ff.open_time[j + 1]), stop, target,
                                         max_bars * tf_seconds(tf) // 60, minute)
    ev_stored = signal.get("evaluation") or {}
    stored_r = ev_stored.get("r_multiple")
    verification = {
        "stored_status": signal.get("evaluation_status"),
        "stored_r": stored_r,
        "stored_exit_time": ev_stored.get("exit_time"),
        "replay_gross": {"status": gross.status, "reason": gross.reason, "r": _r(gross.gross_r),
                         "exit_time": _iso(ff.open_time[gross.exit_index]) if gross.exit_index >= 0 else None,
                         "bars_held": gross.bars_held},
        "replay_net": {"r": _r(net.net_r), "cost_r": _r(net.cost_r), "funding_r": _r(net.funding_r),
                       "exit_fill": _r(net.exit_price, 8), "entry_fill": _r(net.entry_fill, 8)},
        "minute_path": minute_path,
        "ambiguous_bar_resolution": ambiguous_resolution,
        "reproduced": stored_r is not None and gross.gross_r is not None and math.isfinite(gross.gross_r)
        and abs(stored_r - gross.gross_r) < 1e-3,
    }
    if minute_path and gross.status == "FILLED":
        mp_reason = minute_path["reason"]
        verification["minute_agrees"] = (mp_reason.startswith("STOP") == gross.reason.startswith(("STOP", "AMBIG"))
                                         if mp_reason != "OPEN" else None)

    # ---- market context before the signal (bars <= i)
    lb = max(0, i - lookback)
    look = slice(lb, i + 1)
    bars_24h = max(1, 86400 // tf_seconds(tf))
    k24 = max(0, i - bars_24h)
    hi24, lo24 = float(np.max(f.high[k24:i + 1])), float(np.min(f.low[k24:i + 1]))
    before = {
        "lookback_bars": i - lb,
        "change_24h_pct": _r(100 * (f.close[i] / f.close[k24] - 1), 3),
        "change_24h_atr": _r((f.close[i] - f.close[k24]) / atr, 2),
        "distance_from_24h_low_atr": _r((f.close[i] - lo24) / atr, 2),
        "distance_from_24h_high_atr": _r((hi24 - f.close[i]) / atr, 2),
        "range_lookback_high": float(np.max(f.high[look])), "range_lookback_low": float(np.min(f.low[look])),
    }
    after_n = min(len(ff) - 1, j + 1 + 24)
    fut_hi = float(np.max(ff.high[j + 1:after_n + 1])) if after_n > j else math.nan
    fut_lo = float(np.min(ff.low[j + 1:after_n + 1])) if after_n > j else math.nan
    after = {
        "bars": after_n - j,
        "max_favorable_atr": _r(((fut_hi - entry_ref) if long else (entry_ref - fut_lo)) / atr, 2),
        "max_adverse_atr": _r(((entry_ref - fut_lo) if long else (fut_hi - entry_ref)) / atr, 2),
        "mfe_r": _r(gross.mfe_r, 3), "mae_r": _r(gross.mae_r, 3),
        "target_reached_after_stop": gross.post_exit_target,
    }
    # Opposing levels known at decision time (support for a SHORT, resistance for a LONG) and
    # whether each one held while the trade was open (never broken by more than 0.25 ATR).
    swing = f.structure.last_swing_low[i] if not long else f.structure.last_swing_high[i]
    sr_zone = _zone_dist(zones, "SUPPORT" if not long else "RESISTANCE", float(f.close[i]), atr)
    levels = [("24h " + ("low" if not long else "high"), lo24 if not long else hi24)]
    if math.isfinite(swing):
        levels.append(("last confirmed swing " + ("low" if not long else "high"), float(swing)))
    if sr_zone:
        levels.append((f"{sr_zone['timeframe']} {'support' if not long else 'resistance'} zone",
                       sr_zone["upper"] if not long else sr_zone["lower"]))
    last = gross.exit_index if gross.exit_index >= 0 else j + 1
    path_lo, path_hi = float(np.min(ff.low[j + 1:last + 1])), float(np.max(ff.high[j + 1:last + 1]))
    opposing = []
    for label, price in levels:
        dist = (f.close[i] - price) / atr if not long else (price - f.close[i]) / atr
        if not math.isfinite(dist) or dist < 0:
            continue
        held = (path_lo >= price - 0.25 * atr) if not long else (path_hi <= price + 0.25 * atr)
        opposing.append({"level": label, "price": _r(price, 8), "distance_atr": _r(dist, 2), "held": bool(held)})
    after["opposing_levels"] = opposing
    failed_breakout = None
    if signal.get("strategy_type") == "breakout" and struct_level is not None and gross.exit_index >= 0:
        seg = ff.close[j + 1:gross.exit_index + 1]
        failed_breakout = bool(len(seg) and (np.min(seg) < struct_level if long else np.max(seg) > struct_level))

    # ---- correlated signals
    cluster = []
    t_dec = pd.Timestamp(signal["candle_close_time"])
    for p in peers or []:
        if p["id"] == signal["id"] or p["direction"] != direction:
            continue
        dt = abs((pd.Timestamp(p["candle_close_time"]) - t_dec).total_seconds()) / 60
        if dt <= 60:
            c = corr_fn(signal["symbol"], p["symbol"], t_dec.value) if corr_fn else math.nan
            cluster.append({"id": p["id"], "symbol": p["symbol"], "timeframe": p["timeframe"],
                            "minutes_apart": round(dt), "corr_1h": _r(c, 3),
                            "same_symbol": p["symbol"] == signal["symbol"]})

    btc_ctx = None
    if btc and signal["symbol"] != "BTCUSDT" and tf in btc:
        bdf = btc[tf][btc[tf]["open_time"] <= pd.Timestamp(signal["candle_open_time"])].iloc[-LIVE_WINDOW:]
        if len(bdf) > params.warmup_bars:
            bf = compute_features(bdf.reset_index(drop=True), tf, params)
            bra = regime_arrays(bf, rp)
            bi = len(bf) - 1
            bk = max(0, bi - bars_24h)
            btc_ctx = {**bra.at(bi), "alignment": regime_alignment(str(bra.label[bi]), direction),
                       "change_24h_pct": _r(100 * (bf.close[bi] / bf.close[bk] - 1), 3)}

    diagnostics = {
        "regime": {**ra.at(i), "alignment": regime_alignment(str(ra.label[i]), direction)},
        "htf_regimes": {k: {**v, "alignment": regime_alignment(v["regime"], direction)} for k, v in htf_regimes.items()},
        "entry_quality": {"class": ecls, "reasons": ewhy, **feat},
        "stop": {"distance_atr": _r(stop_dist / atr, 3), "noise_q80_atr": _r(noise / atr, 3) if math.isfinite(noise)
                 else None, "inside_noise": bool(math.isfinite(noise) and stop_dist < noise),
                 "method": (plan.get("stop") or {}).get("method"), "widened": (plan.get("stop") or {}).get("widened")},
        "nearest_support": _zone_dist(zones, "SUPPORT", float(f.close[i]), atr),
        "nearest_resistance": _zone_dist(zones, "RESISTANCE", float(f.close[i]), atr),
        "room_r": plan.get("room_r"),
        "indicators": {"rsi": _r(f.rsi[i], 2), "macd_hist": _r(f.macd_hist[i], 8), "atr": _r(atr, 8),
                       "atr_pct": _r(100 * atr / f.close[i], 3), "rvol": _r(f.rvol[i], 2),
                       "ema_fast": _r(f.ema_fast[i], 8), "ema_mid": _r(f.ema_mid[i], 8), "ema_slow": _r(f.ema_slow[i], 8)},
        "before": before,
        "btc": btc_ctx,
        "correlated_signals": cluster,
        "score": {"score": signal["score"], "components": signal.get("score_breakdown")},
    }
    causes = classify_causes(signal, diagnostics, after, verification, failed_breakout, gross.reason)
    return {
        "signal_id": signal["id"], "symbol": signal["symbol"], "timeframe": tf, "direction": direction,
        "strategy": signal.get("strategy_type"), "strategy_version": signal.get("strategy_version"),
        "signal_open_time": signal["candle_open_time"], "decision_time": signal["candle_close_time"],
        "entry_reference": entry_ref, "stop_loss": stop, "take_profit": target,
        "reconstruction": {"matches": rec["matches"], "differences": rec["differences"],
                           "candles_used": rec["candles_used"],
                           "full_history_matches": rec_full["ok"] and rec_full["matches"],
                           "full_history_differences": rec_full.get("differences", [])},
        "verification": verification,
        "diagnostics": diagnostics,
        "outcome_evidence": {**after, "failed_breakout": failed_breakout, "exit_reason": gross.reason},
        "causes": causes,
        "chart": chart_payload(ff, j, gross.exit_index, lookback, signal, zones),
    }


def classify_causes(signal: dict[str, Any], d: dict[str, Any], after: dict[str, Any], ver: dict[str, Any],
                    failed_breakout: bool | None, exit_reason: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lost = exit_reason.startswith(("STOP", "AMBIG"))
    direction = signal["direction"]
    eq = d["entry_quality"]

    def add(code: str, status: str, evidence: str) -> None:
        out.append({"code": code, "status": status, "evidence": evidence})

    if not lost:
        add("not_a_loss", "confirmed", f"exit reason {exit_reason}; no failure to explain")
        return out
    # 1/4 overextension or reversal
    if eq["class"] in ("EXTENDED", "EXHAUSTION_RISK"):
        never = after.get("mfe_r") is not None and after["mfe_r"] < 0.3
        add("late_entry", "confirmed" if never else "suspected",
            f"entry classified {eq['class']} ({'; '.join(eq['reasons'])}); MFE {after.get('mfe_r')}R"
            + (" - price never moved meaningfully in the trade direction" if never else ""))
    # 2 near support/resistance
    # Confirmed only when a level within 1 ATR of the entry was known at decision time AND
    # held (was not broken by > 0.25 ATR) while the trade was open, i.e. price turned there.
    near = [lv for lv in after.get("opposing_levels", []) if lv["distance_atr"] is not None and lv["distance_atr"] < 1.0]
    if near:
        held = [lv for lv in near if lv["held"]]
        add("entry_near_opposing_level", "confirmed" if held else "suspected",
            "; ".join(f"{lv['level']} {lv['distance_atr']} ATR away ({'held' if lv['held'] else 'broken'})"
                      for lv in near))
    # 3 momentum weakening
    if eq.get("momentum_fading"):
        add("momentum_weakening", "suspected", "MACD histogram moved against the trade on the 2 candles before entry")
    # 5 stop inside noise
    st = d["stop"]
    if st["inside_noise"]:
        add("stop_inside_noise", "confirmed" if after.get("target_reached_after_stop") else "suspected",
            f"stop {st['distance_atr']} ATR vs 80th-percentile 3-bar adverse move {st['noise_q80_atr']} ATR"
            + ("; price reached the target after stopping out" if after.get("target_reached_after_stop") else ""))
    elif after.get("target_reached_after_stop"):
        add("stopped_before_target", "suspected", "target was reached after the stop, although the stop was "
                                                  "outside measured noise")
    # 6 failed breakout
    if failed_breakout:
        add("failed_breakout", "confirmed", "a candle closed back through the broken zone before the exit")
    # 7 HTF/BTC alignment
    against = [k for k, v in d["htf_regimes"].items() if v["alignment"] == "AGAINST"]
    if against:
        add("htf_regime_against", "suspected", f"higher-timeframe regime against the trade on {', '.join(against)}")
    if d["regime"]["regime"] in ("EXHAUSTION", "HIGH_VOL_EXPANSION", "CONFLICTED", "SIDEWAYS"):
        add("unfavourable_regime", "suspected", f"signal-timeframe regime {d['regime']['label']}")
    if d.get("btc") and d["btc"]["alignment"] == "AGAINST":
        add("btc_against", "suspected", f"BTC regime {d['btc']['label']} against a {direction}")
    # 8 correlated cluster
    cl = d["correlated_signals"]
    if cl:
        hi = [c for c in cl if c["same_symbol"] or (c["corr_1h"] is not None and c["corr_1h"] >= 0.7)]
        add("correlated_cluster", "confirmed" if hi else "suspected",
            f"{len(cl)} other {direction} signal(s) within 60 min ({', '.join(c['symbol'] + ' ' + c['timeframe'] for c in cl)})"
            + (f"; {len(hi)} with 1h return correlation >= 0.7 or the same coin" if hi else ""))
    # 9 score overstated
    if signal["score"] >= 80:
        add("score_overstated", "suspected", f"score {signal['score']}/100 on a losing trade; whether the score "
                                             "predicts outcomes is tested in the calibration study")
    # 10 execution assumptions
    mp = ver.get("minute_path")
    if exit_reason == "AMBIGUOUS_STOP":
        res = ver.get("ambiguous_bar_resolution")
        add("ambiguous_candle", "confirmed" if res == "TARGET" else "suspected",
            f"stop and target in the same candle; 1-minute data says {res or 'still ambiguous / unavailable'}")
    if mp and ver.get("minute_agrees") is False:
        add("execution_model_mismatch", "confirmed", f"1-minute replay exits by {mp['reason']} at {mp['exit_time']}")
    if not any(c["status"] == "confirmed" for c in out):
        add("undetermined", "undetermined", "no measured condition explains the loss with confidence; a single "
                                            "trade can lose for random reasons")
    return out


def chart_payload(ff, j: int, exit_index: int, lookback: int, signal: dict[str, Any], zones: list[Zone]
                  ) -> dict[str, Any]:
    lo = max(0, j - lookback)
    hi = min(len(ff) - 1, max(exit_index, j + 1) + 20)
    candles = [{"time": int(ff.open_time[k] // 1_000_000_000), "open": float(ff.open[k]), "high": float(ff.high[k]),
                "low": float(ff.low[k]), "close": float(ff.close[k]), "volume": float(ff.volume[k])}
               for k in range(lo, hi + 1)]
    return {"candles": candles, "signal_time": int(ff.open_time[j] // 1_000_000_000),
            "entry_time": int(ff.open_time[min(j + 1, len(ff) - 1)] // 1_000_000_000),
            "exit_time": int(ff.open_time[exit_index] // 1_000_000_000) if exit_index >= 0 else None,
            "zones": [z.to_dict() for z in zones]}
