"""Deterministic human-readable technical analysis report.

Every sentence is generated from the structured analysis object. No external language
model is used, so no price, pattern, indicator value or probability can be invented. An
LLM could later rephrase ``sections``, but must receive only this object.
"""

from __future__ import annotations

from typing import Any


def _p(x: Any) -> str:
    if not isinstance(x, (int, float)):
        return "n/a"
    if abs(x) >= 1000:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:,.4f}".rstrip("0").rstrip(".")
    return f"{x:.6g}"


def _z(z: dict[str, Any] | None) -> str:
    return "none detected" if not z else f"{_p(z['lower'])}-{_p(z['upper'])} ({z['timeframe']}, {z['tests']} tests, {z['status'].lower()})"


def build_report(symbol: str, a: dict[str, Any], mtf: dict[str, Any] | None, source: dict[str, Any]) -> dict[str, Any]:
    s: list[dict[str, Any]] = []
    tf = a["timeframe"]
    s.append({"title": "1. Current market condition", "lines": [
        f"{symbol} {tf}: last confirmed candle {a['candle_open_time']} closed at {_p(a['close'])}.",
        f"Trend {a['trend'].lower()}, structure {a['structure'].lower().replace('_', ' ')}, momentum "
        f"{a['momentum'].lower()}, volatility {a['volatility'].lower()}.",
        f"Evidence bias: {a['bias']} (alignment {a['alignment_score']}/100; how one-sided the evidence is, "
        f"not a probability of success).",
    ]})
    lines = []
    if mtf:
        for row in mtf["timeframes"]:
            lines.append(f"{row['timeframe']}: {row['bias']} | trend {row['trend'].lower()} | structure "
                         f"{row['structure'].lower().replace('_', ' ')} | last candle {row['candle_open_time']}")
        lines.append(mtf["summary"])
        lines += mtf["conflicts"]
    s.append({"title": "2. Higher-timeframe bias", "lines": lines or ["Multi-timeframe data not available."]})
    sd = a["structure_detail"]
    ev_lines = [e["label"] + f" ({e['time']})" for e in sd["events"][-4:]]
    s.append({"title": f"3. {tf} market structure", "lines": [sd["explanation"]] + ev_lines})
    lv = [f"Nearest support: {_z(a['nearest_support'])}.", f"Nearest resistance: {_z(a['nearest_resistance'])}."]
    for k in a.get("levels", [])[:6]:
        lv.append(f"{k['label']}: {_p(k['price'])} ({k['role'].lower()}"
                  + (f", {k['distance_atr']} ATR away)" if k["distance_atr"] is not None else ")"))
    for t in a.get("trendlines", []):
        lv.append(t["label"] + ".")
    for fl in a.get("flips", []):
        lv.append(f"{fl['timeframe']} zone {_p(fl['lower'])}-{_p(fl['upper'])}: {fl['note']}.")
    lv.append("Levels are areas where price has reacted before, not guaranteed turning points.")
    s.append({"title": "4. Important support and resistance", "lines": lv})
    pats = [p["explanation"] for p in a["chart_patterns"]] or ["No classical chart pattern meets the geometric rules."]
    s.append({"title": "5. Chart patterns", "lines": pats})
    cdl = [p["explanation"] + (" In context." if p["in_context"] else " Out of context (weak).")
           for p in a["candlestick_patterns"]] or ["No candlestick pattern on the last three closed candles."]
    cdl.append("Candlestick patterns never trigger a trade by themselves.")
    s.append({"title": "6. Candlestick analysis", "lines": cdl})
    md = a["momentum_detail"]
    td = a["trend_detail"]
    tm = [f"MA alignment {td['ma_alignment'].lower()}; ADX {_p(td['adx'])} ({'trending' if td['trending'] else 'weak trend'}); "
          f"Supertrend {'up' if td['supertrend'] > 0 else 'down' if td['supertrend'] < 0 else 'undefined'}.",
          f"RSI {_p(md['rsi'])}, MACD histogram {_p(md['macd_hist'])}, Stochastic %K {_p(md['stoch_k'])}, "
          f"Stoch RSI %K {_p(md['stochrsi_k'])}." + (f" RSI is {md['stretch']}." if md["stretch"] else "")]
    tm += [d["text"] for d in a["divergences"]]
    s.append({"title": "7. Trend and momentum evidence", "lines": tm})
    vd = a["volume_detail"]
    vf = [f"Relative volume {_p(vd['rvol'])}x" + (" (spike)" if vd["spike"] else "")
          + (f"; OBV divergence {vd['obv_divergence'].lower()}" if vd["obv_divergence"] else "")
          + (f"; close {'above' if vd['above_vwap'] else 'below'} VWAP {_p(vd['vwap'])}" if vd["vwap"] else "") + "."]
    fut = a.get("futures")
    if fut:
        vf.append(fut["summary"])
        for name, src in (fut.get("sources") or {}).items():
            vf.append(f"Source {name}: {src.get('status')}" + (f" ({src.get('note')})" if src.get("note") else "") + ".")
    s.append({"title": "8. Volume and futures context", "lines": vf})
    scen = []
    for sc in a.get("scenarios", []):
        line = f"{sc['name']}: {sc['status']}."
        if sc.get("trigger"):
            line += f" Trigger: {sc['trigger']}"
        scen.append(line)
        if sc.get("opposing_evidence"):
            scen.append("   Opposing evidence: " + " | ".join(sc["opposing_evidence"][:3]))
    s.append({"title": "9. Bullish and bearish scenarios", "lines": scen})
    q = [sc for sc in a.get("scenarios", []) if sc.get("status") == "QUALIFIED"]
    if q:
        plan = []
        for sc in q:
            tps = ", ".join(f"{t['label']} {_p(t['price'])}" for t in sc.get("targets") or [])
            plan.append(f"{sc['direction']} entry {_p(sc['entry'])}, stop {_p(sc['stop_loss'])}, {tps}, R:R 1:"
                        f"{_p(sc['risk_reward'])}.")
            if sc.get("sizing"):
                z = sc["sizing"]
                plan.append(f"Size {z['quantity']:g} at {z['leverage']:g}x, notional {_p(z['notional'])}, planned risk "
                            f"about {_p(z['estimated_loss'])} USDT.")
    else:
        plan = ["No scenario is QUALIFIED: no entry, stop-loss or position size is proposed."]
    s.append({"title": "10. Entry / stop-loss / take-profit", "lines": plan})
    avoid = next((sc.get("reasons") for sc in a.get("scenarios", []) if sc["direction"] == "NONE"), []) or []
    s.append({"title": "11. Reasons to avoid trading", "lines": avoid or ["None identified by the rules."]})
    inval = []
    for sc in a.get("scenarios", []):
        for x in sc.get("invalidation") or []:
            inval.append(f"{sc['direction']}: {x}")
    s.append({"title": "12. Invalidation conditions", "lines": inval or ["No active setup to invalidate."]})
    text = "\n\n".join(sec["title"] + "\n" + "\n".join("- " + ln for ln in sec["lines"]) for sec in s)
    return {
        "symbol": symbol, "timeframe": tf, "generated_from_candle": a["candle_open_time"],
        "data_source": source, "sections": s,
        "text": f"Technical analysis: {symbol} {tf}\n{source.get('note', '')}\n\n" + text
                + "\n\nResearch analysis only. Not financial advice; nothing is executed.",
        "method": "deterministic template (no language model)",
    }
