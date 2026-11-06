"""Strategy D's decision for one closed 15m candle. Pure: candles in, decision out.

Only 15m setups can become D trades. They come from four entry types: breakout, breakout retest,
trend pullback and structure continuation, using the same rules mirrored for each direction. Breakouts
and pullbacks come from the existing engine, read only. Higher timeframes only qualify or reject them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.planning.stops import StopContext, plan_stop
from app.risk.stops import compute_stop
from app.strategy.engine import evaluate_at
from app.strategy_d.confluence import rule_passes, score
from app.strategy_d.context import TfData, nearest_levels, state_at_decision
from app.strategy_d.params import CONTEXT_TIMEFRAMES, DParams

STOP_ATR = {"atr_2_0": 2.0, "atr_2_5": 2.5, "atr_3_0": 3.0}
STOP_LABEL = {"atr_2_0": "2.0 ATR", "atr_2_5": "2.5 ATR", "atr_3_0": "3.0 ATR", "structural": "Structural + 0.5 ATR"}
ENTRY_LABEL = {"breakout": "breakout confirmation", "retest": "breakout retest", "pullback": "trend pullback", "continuation": "structure continuation"}
ROUND_TRIP_COST_PCT = 2 * 0.055 + 0.02 + 0.05  # the research cost model: fees, entry and stop slippage


@dataclass(slots=True)
class Trigger:
    direction: str
    entry_type: str
    detail: str
    zone_level: float | None = None
    blocked: str | None = None  # the engine's objection that applies whatever the stop


def _trend_ok(f: Any, i: int, direction: str) -> bool:
    return bool(f.ema_fast[i] > f.ema_mid[i] > f.ema_slow[i]) if direction == "LONG" else bool(f.ema_fast[i] < f.ema_mid[i] < f.ema_slow[i])


def detect_retest(f: Any, i: int, p: DParams, direction: str) -> Trigger | None:
    """The first return to a level broken within the lookback, closing back beyond it in the trade's direction."""
    if not _trend_ok(f, i, direction):
        return None
    sign = 1 if direction == "LONG" else -1
    atr = float(f.atr[i])
    level_of = f.prior_high if direction == "LONG" else f.prior_low
    touched = (lambda k, lvl: f.low[k] <= lvl + p.retest_touch_atr * atr) if sign > 0 else (lambda k, lvl: f.high[k] >= lvl - p.retest_touch_atr * atr)
    for k in range(i - 1, max(0, i - 1 - p.retest_lookback_bars), -1):
        level = float(level_of[k])
        if not math.isfinite(level) or sign * (f.close[k] - level) <= 0 or sign * (f.close[k - 1] - float(level_of[k - 1])) > 0:
            continue  # only the candle that first closed beyond the level is the break
        if any(touched(m, level) for m in range(k + 1, i)):
            return None  # the first retest has passed
        if touched(i, level) and sign * (f.close[i] - level) > 0 and sign * (f.close[i] - f.open[i]) > 0:
            return Trigger(direction, "retest", f"retest of the level {level:.6g} broken {i - k} candles ago", level)
        return None
    return None


def detect_continuation(f: Any, i: int, direction: str) -> Trigger | None:
    """A close beyond the last confirmed swing, in an existing swing structure of that direction."""
    if i < 1 or not _trend_ok(f, i, direction):
        return None
    sign = 1 if direction == "LONG" else -1
    swing = f.structure.last_swing_high if direction == "LONG" else f.structure.last_swing_low
    now, before = float(swing[i]), float(swing[i - 1])
    if not (math.isfinite(now) and math.isfinite(before)):
        return None
    if int(f.structure.structure[i]) != sign or sign * (f.close[i] - now) <= 0 or sign * (f.close[i - 1] - before) > 0 or sign * (f.close[i] - f.open[i]) <= 0:
        return None
    return Trigger(direction, "continuation", f"close beyond the confirmed swing {now:.6g}", now)


def triggers_at(data: dict[str, TfData], i: int, p: DParams, symbol: str | None = None) -> tuple[list[Trigger], dict[tuple[str, str], Any]]:
    """Every confirmed 15m entry on this candle, plus the existing engine's setups that produced them."""
    f = data["15m"].f
    htf = {tf: data[tf].f for tf in ("1h", "4h") if tf in data}
    ev = evaluate_at(f, i, p.base, htf, context=False, symbol=symbol)
    found: list[Trigger] = []
    setups: dict[tuple[str, str], Any] = {}
    for st in ev.setups:
        if (st.plan or {}).get("confirmation_candle") is None:
            continue
        setups[(st.direction, st.strategy)] = st
        vol = [r["message"] for r in st.reasons if r.get("code") == "volatility_out_of_range"]
        level = ((st.plan.get("stop") or {}).get("structural_level"))
        found.append(Trigger(st.direction, st.strategy, st.explanation.split(". ")[0][:160], level, vol[0] if vol else None))
    for direction in ("LONG", "SHORT"):
        for t in (detect_retest(f, i, p, direction), detect_continuation(f, i, direction)):
            if t is not None:
                found.append(t)
    return found, setups


PRIORITY = ("breakout", "retest", "pullback", "continuation")


def pick_per_direction(found: list[Trigger]) -> list[Trigger]:
    """One decision per direction on a candle, so a candle matching two entry types isn't two signals."""
    out = []
    for direction in ("LONG", "SHORT"):
        mine = sorted((t for t in found if t.direction == direction), key=lambda t: PRIORITY.index(t.entry_type))
        if mine:
            out.append(mine[0])
    return out


def stop_for(method: str, direction: str, entry: float, atr: float, swing: float | None, zone: float | None, tick: float | None = None) -> tuple[float | None, str]:
    if method in STOP_ATR:
        k = STOP_ATR[method]
        return compute_stop(direction, entry, atr, "atr", None, k, tick_size=tick).price, f"{k:g} ATR from the entry"  # type: ignore[arg-type]
    plan = plan_stop("E_structural", StopContext(direction, entry, atr, None, swing, zone, None, tick))
    return plan.price, plan.justification


def trade_plan(direction: str, entry: float, stop: float, p: DParams) -> dict[str, Any]:
    sign = 1 if direction == "LONG" else -1
    risk = abs(entry - stop)
    targets = [{"label": f"TP{n + 1}", "price": entry + sign * m * risk, "r": m, "weight": w, "move_pct": 100 * m * risk / entry}
               for n, (m, w) in enumerate(zip(p.target_r, p.target_weights, strict=True))]
    gross = sum(t["r"] * t["weight"] for t in targets)
    cost_r = ROUND_TRIP_COST_PCT / 100 * entry / risk
    return {"entry_reference": entry, "stop_loss": stop, "stop_move_pct": 100 * risk / entry, "targets": targets,
            "gross_r_all_targets": gross, "net_r_all_targets": gross - cost_r, "net_r_at_stop": -1 - cost_r, "cost_r": cost_r,
            "plan_note": "What the plan pays if every target or the stop is reached. It isn't an expected result."}


def decide(data: dict[str, TfData], i: int, p: DParams, symbol: str | None = None, tick: float | None = None,
           rule: str = "d_default", stop_method: str | None = None) -> list[dict[str, Any]]:
    """Technical decisions for 15m candle ``i``, before portfolio limits. Empty means no 15m setup: NO TRADE."""
    found, setups = triggers_at(data, i, p, symbol)
    if not found:
        return []
    f = data["15m"].f
    decision_ns = int(f.close_time[i])
    states = {tf: state_at_decision(data.get(tf), tf, decision_ns, p).to_dict() for tf in CONTEXT_TIMEFRAMES}
    biases = {tf: s["bias"] for tf, s in states.items()}
    entry, atr = float(f.close[i]), float(f.atr[i])
    levels = nearest_levels({tf: data[tf] for tf in ("15m", "1h", "4h") if tf in data}, symbol, entry, decision_ns, p)
    method = stop_method or p.stop_method
    out = []
    for t in pick_per_direction(found):
        long = t.direction == "LONG"
        conf = score(t.direction, biases, True, p)
        setup = setups.get((t.direction, t.entry_type))
        swing = float(f.structure.last_swing_low[i] if long else f.structure.last_swing_high[i])
        stop, stop_note = stop_for(method, t.direction, entry, atr, swing if math.isfinite(swing) else None, t.zone_level, tick)
        conflicts = [f"{tf.upper()} is {b.lower().replace('_', ' ')}" for tf, b in biases.items() if b != ("BULLISH" if long else "BEARISH")]
        side = "resistance" if long else "support"
        walls = [v[side] for tf, v in levels.items() if v[side] is not None]
        boundary = (min(walls) if long else max(walls)) if walls else None
        row: dict[str, Any] = {"direction": t.direction, "entry_type": t.entry_type, "entry_label": ENTRY_LABEL[t.entry_type], "trigger": t.detail,
                               "states": states, "confluence": conf, "conflicts": conflicts, "levels": levels, "opposing_boundary": boundary,
                               "extension_atr": abs(entry - float(f.ema_fast[i])) / atr, "rvol": float(f.rvol[i]) if math.isfinite(float(f.rvol[i])) else None,
                               "mtf_confirmed": setup.mtf_confirmed if setup is not None else None, "plan": None, "stop_note": stop_note, "room_r": None,
                               "blocked": t.blocked, "zone_level": t.zone_level, "swing_level": swing if math.isfinite(swing) else None, "entry": entry, "atr": atr}
        distance = abs(entry - stop) if stop is not None else None
        if stop is None or stop <= 0 or distance is None or not p.min_stop_atr <= distance / atr <= p.max_stop_atr or (long and stop >= entry) or (not long and stop <= entry):
            row.update(decision="REJECTED", reason_code="invalid_stop", reason=f"No valid stop: {stop_note}")
            out.append(row)
            continue
        row["plan"] = trade_plan(t.direction, entry, stop, p)
        if any(x["price"] <= 0 for x in row["plan"]["targets"]):
            row.update(plan=None, decision="REJECTED", reason_code="invalid_target", reason="A target would not be a valid price.")
            out.append(row)
            continue
        row["room_r"] = abs(boundary - entry) / distance if boundary is not None else None
        if t.blocked:
            row.update(decision="REJECTED", reason_code="volatility", reason=t.blocked)
        elif row["room_r"] is not None and row["room_r"] < p.min_room_r:
            row.update(decision="REJECTED", reason_code="insufficient_reward",
                       reason=f"The nearest {side} leaves {row['room_r']:.2f}R, below the {p.min_room_r:g}R minimum.")
        elif rule_passes(rule, t.direction, states, conf["score"], row["mtf_confirmed"], p):
            row.update(decision="QUALIFIED", reason_code="ok", reason=f"15m {ENTRY_LABEL[t.entry_type]} confirmed; alignment score {conf['score']:.0f} of {conf['out_of']:.0f}.")
        elif conf["score"] >= p.watch_score and biases.get("4h") != ("BEARISH" if long else "BULLISH"):
            row.update(decision="WATCHLIST", reason_code="weak_alignment",
                       reason=f"Alignment score {conf['score']:.0f} is below the {p.qualify_score:g} needed. Waiting for a new candle to qualify.")
        else:
            row.update(decision="REJECTED", reason_code="higher_timeframes", reason=f"Higher timeframes don't support this: {'; '.join(conflicts) or 'score too low'}.")
        out.append(row)
    return out
