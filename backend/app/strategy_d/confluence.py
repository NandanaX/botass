"""The weighted alignment score and the higher-timeframe rules compared in research.

The score counts how far the timeframes agree with a setup's direction, with one vote per timeframe so
that correlated indicators aren't counted repeatedly. Timeframes are themselves correlated, so it is
still not independent evidence, and it says nothing about the chance of a win. Trade expectancy is
measured separately. A confirmed 15m entry is required whatever the score.
"""

from __future__ import annotations

from typing import Any

from app.strategy_d.params import DParams

WANT = {"LONG": "BULLISH", "SHORT": "BEARISH"}
OPPOSITE = {"LONG": "BEARISH", "SHORT": "BULLISH"}
NEUTRAL = ("SIDEWAYS", "CONFLICTED")


def alignment(direction: str, bias: str) -> str:
    if bias == WANT[direction]:
        return "aligned"
    if bias == OPPOSITE[direction]:
        return "opposed"
    return "neutral" if bias in NEUTRAL else "missing"


def score(direction: str, biases: dict[str, str], confirmed_15m: bool, p: DParams) -> dict[str, Any]:
    """The score, and each timeframe's contribution to it."""
    parts = []
    for tf in ("1d", "4h", "1h"):
        a = alignment(direction, biases.get(tf, "INSUFFICIENT_DATA"))
        value = {"aligned": 1.0, "neutral": p.neutral_value}.get(a, 0.0)
        parts.append({"timeframe": tf, "weight": p.weights[tf], "bias": biases.get(tf, "INSUFFICIENT_DATA"), "alignment": a, "value": value, "points": p.weights[tf] * value})
    entry = 1.0 if confirmed_15m else 0.0
    parts.append({"timeframe": "15m", "weight": p.weights["15m"], "bias": "CONFIRMED" if confirmed_15m else "NOT_CONFIRMED", "alignment": "aligned" if confirmed_15m else "missing", "value": entry, "points": p.weights["15m"] * entry})
    total = sum(x["points"] for x in parts)
    return {"score": round(total, 2), "out_of": sum(p.weights.values()), "parts": parts,
            "note": "Rule agreement across timeframes. Not a probability of success."}


def h1_allows_pullback(direction: str, states: dict[str, dict[str, Any]]) -> bool:
    """1H may lean against the 4H trend while it hasn't changed character against the setup."""
    s = states.get("1h") or {}
    a = alignment(direction, s.get("bias", "INSUFFICIENT_DATA"))
    if a in ("aligned", "neutral"):
        return True
    if a == "missing":
        return False
    return not (s.get("last_break") == "CHOCH" and s.get("last_break_dir") == (-1 if direction == "LONG" else 1))


def rule_passes(rule: str, direction: str, states: dict[str, dict[str, Any]], total: float, mtf_confirmed: bool | None, p: DParams) -> bool:
    a = {tf: alignment(direction, (states.get(tf) or {}).get("bias", "INSUFFICIENT_DATA")) for tf in ("1d", "4h", "1h")}
    if rule == "d_default":
        return total >= p.qualify_score and a["4h"] not in ("opposed", "missing")
    if rule == "strict_1d_4h_1h":
        return all(v == "aligned" for v in a.values())
    if rule == "4h_1h_1d_context":
        return a["4h"] == "aligned" and a["1h"] == "aligned" and a["1d"] != "missing"
    if rule == "4h_with_1h_pullback":
        return a["4h"] == "aligned" and h1_allows_pullback(direction, states)
    if rule == "existing_c":
        return mtf_confirmed is True
    if rule == "no_filter":
        return True
    raise ValueError(f"unknown rule {rule}")


RULES = {"d_default": "Strategy D default: score at least 70 and 4H not opposed",
         "strict_1d_4h_1h": "Strict: 1D, 4H and 1H all aligned",
         "4h_1h_1d_context": "4H and 1H aligned, 1D as context only",
         "4h_with_1h_pullback": "4H aligned, 1H may be pulling back",
         "existing_c": "Strategy C's check: all its higher timeframes agree",
         "no_filter": "15m trigger only, no higher-timeframe filter"}
