"""Pure builders turning domain records into events. All numbers come from the persisted signal
or paper-trade record; nothing is recomputed, and paper results are always labelled as paper."""

from __future__ import annotations

import hashlib
import html
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.notifications.events import EventIn

PAPER_LABEL = "PAPER TRADING"
PAPER_FOOTER = "PAPER TRADING: simulated result. No real order was placed and no real money was at risk."
PAPER_PORTFOLIO_NOTE = "Paper portfolio only: no real trades are affected."
SHADOW_VERBS = {"QUALIFIED": "Would take", "WATCHLIST": "Would watch", "REJECTED": "Would reject",
                "NO_TRADE": "No trade", "ERROR": "Engine error"}
VARIANT_NAMES = {"A": "Baseline", "B": "2.5 ATR stop", "C": "2.5 ATR + HTF"}
SEVERITY_EMOJI = {"INFO": "ℹ️", "SUCCESS": "✅", "WARNING": "⚠️", "CRITICAL": "🚨"}


def num(x: float | None) -> str:
    if x is None:
        return "n/a"
    a = abs(x)
    return f"{x:,.2f}" if a >= 1000 else f"{x:,.4f}" if a >= 1 else f"{x:.6g}"


def usd(x: float | None) -> str:
    return "n/a" if x is None else f"{'-' if x < 0 else ''}${abs(x):,.2f}"


def duration(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "n/a"
    mins = max(0, round((end - start).total_seconds() / 60))
    days, hours, rest = mins // 1440, (mins % 1440) // 60, mins % 60
    return f"{days}d {hours}h {rest}m" if days else f"{hours}h {rest}m" if hours else f"{rest}m"


def decision_view(decision: dict[str, Any] | None) -> dict[str, Any] | None:
    """The decision engine's verdict, kept apart from the baseline signal status. A shadow
    rejection is a recommendation only and must never read as an enforced rejection."""
    if not decision or not decision.get("outcome"):
        return None
    outcome, mode = decision["outcome"], decision.get("mode", "shadow")
    if mode == "enforce":
        label = ("Enforced decision: Qualified — alert allowed." if outcome == "QUALIFIED"
                 else f"Enforced decision: {outcome} — Telegram alert blocked.")
        enforced = outcome != "QUALIFIED"
    else:
        verb = SHADOW_VERBS.get(outcome, outcome)
        label = (f"Shadow decision: {verb} — signal was not changed." if outcome == "QUALIFIED"
                 else f"Shadow decision: {verb} — signal was not blocked.")
        enforced = False
    return {"outcome": outcome, "mode": mode, "enforced": enforced, "label": label,
            "reason": decision.get("reason"), "checks": decision.get("checks") or []}


# ---------------------------------------------------------------------------- signals

def signal_payload(symbol: str, tf: str, direction: str, strategy: str | None, candle_open: Any, candle_close: Any,
                   entry: float | None, stop: float | None, target: float | None, status: str, reasoning: str | None,
                   decision: dict[str, Any] | None = None, score: int | None = None) -> dict[str, Any]:
    return {"symbol": symbol, "timeframe": tf, "direction": direction, "strategy": strategy,
            "candle_open_time": candle_open, "signal_time": candle_close, "entry_reference": entry, "stop_loss": stop,
            "take_profit": target, "baseline_status": status, "reasoning": reasoning, "score": score,
            "decision": decision_view(decision)}


def _signal_lines(p: dict[str, Any]) -> list[str]:
    lines = [f"Entry {num(p['entry_reference'])} · Stop {num(p['stop_loss'])} · Target {num(p['take_profit'])}",
             f"Baseline signal status: {p['baseline_status']}"]
    if p.get("decision"):
        lines.append(p["decision"]["label"])
    if p.get("reasoning"):
        lines.append(f"Reason: {p['reasoning']}")
    return lines


def _plan_summary(row: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """The stored plan values an alert shows, kept with the event. Nothing is recomputed."""
    sizing = plan.get("sizing") or {}
    return {"targets": [{k: t.get(k) for k in ("label", "price", "r_multiple", "blocked")} for t in plan.get("targets") or []],
            "primary_target_index": plan.get("primary_target_index"), "net_risk_reward": plan.get("net_risk_reward"),
            "size": {"quantity": sizing.get("quantity"), "base": (sizing.get("rules") or {}).get("base_coin"), "risk": sizing.get("estimated_loss")} if sizing else None,
            "htf": [{"timeframe": h.get("timeframe"), "trend": h.get("trend")} for h in row.get("mtf_details") or []]}


def signal_qualified_event(row: dict[str, Any], telegram_html: str, not_after: datetime) -> EventIn:
    plan = row.get("plan") or {}
    p = signal_payload(row["symbol"], row["timeframe"], row["direction"], row.get("strategy_type"),
                       row["candle_open_time"], row["candle_close_time"], row["entry_reference"], row["stop_loss"],
                       row["take_profit"], row.get("setup_status", "QUALIFIED"), row["explanation"], plan.get("decision"),
                       row.get("score"))
    return EventIn(event_id=f"signal:qualified:{row['id']}", event_type="SIGNAL_QUALIFIED",
                   title=f"{row['direction']} {row['symbol']} {row['timeframe']}: qualified signal",
                   message="\n".join(_signal_lines(p)), payload={**p, **_plan_summary(row, plan)}, symbol=row["symbol"], timeframe=row["timeframe"],
                   strategy_version=row.get("strategy_version"), signal_id=row["id"], occurred_at=row["candle_close_time"],
                   telegram_html=telegram_html, telegram_not_after=not_after)


def candidate_event(symbol: str, tf: str, st: Any, open_time: str, close_time: str, status: str | None = None,
                    reason: str | None = None) -> EventIn:
    """A non-qualified candidate: SIGNAL_DETECTED (entered the watchlist, no plan yet) or SIGNAL_REJECTED."""
    status = status or st.status
    etype = "SIGNAL_DETECTED" if status == "WATCHLIST" else "SIGNAL_REJECTED"
    why = reason or "; ".join(r.get("message", "") for r in (st.reasons or []) if r.get("message")) or st.explanation
    p = signal_payload(symbol, tf, st.direction, st.strategy, open_time, close_time, st.entry_reference, st.stop_loss,
                       st.take_profit, status, why, score=st.score)
    title = (f"{st.direction} {symbol} {tf}: candidate on watchlist" if etype == "SIGNAL_DETECTED"
             else f"{st.direction} {symbol} {tf}: candidate rejected")
    return EventIn(event_id=f"signal:{etype.lower()}:{symbol}:{tf}:{st.direction}:{st.strategy}:{open_time}", event_type=etype,
                   title=title, message="\n".join(_signal_lines(p)), payload=p, symbol=symbol, timeframe=tf,
                   strategy_version=st.strategy_version, occurred_at=datetime.fromisoformat(close_time))


MAX_DIGEST_LINES = 15


def watchlist_digest_event(items: list[dict[str, Any]], now: datetime, valid_until: datetime) -> EventIn:
    """One Telegram message per candle close listing candidates that have just got close. These have no
    entry, stop or target, and the message says plainly that they are not signals."""
    order = {"4h": 0, "1h": 1, "30m": 2, "15m": 3, "5m": 4}
    ordered = sorted(items, key=lambda x: (order.get(x["timeframe"], 9), x["symbol"]))
    lines = [f"{'🟢' if x['direction'] == 'LONG' else '🔴'} {x['symbol']} {x['timeframe']} {x['direction']}: {x['why']}" for x in ordered]
    extra = len(lines) - MAX_DIGEST_LINES
    if extra > 0:
        lines = lines[:MAX_DIGEST_LINES] + [f"…and {extra} more. See the Market Scanner."]
    intro = "NOT signals. No entry, stop or target yet, so don't trade these. A separate alert follows only if one qualifies."
    text = "\n".join(lines)
    key = "|".join(f"{x['symbol']}:{x['timeframe']}:{x['direction']}" for x in ordered)
    body = f"👀 <b>Getting close: {len(items)} to watch</b>\n<b>{html.escape(intro)}</b>\n\n{html.escape(text)}"
    return EventIn(event_id=f"watchlist:digest:{now.isoformat(timespec='minutes')}:{hashlib.sha256(key.encode()).hexdigest()[:12]}",
                   event_type="WATCHLIST_DIGEST", title=f"Getting close: {len(items)} to watch (not signals)",
                   message=f"{intro}\n{text}"[:4000], payload={"items": [{"symbol": x["symbol"], "timeframe": x["timeframe"], "direction": x["direction"], "brief": x.get("brief")} for x in items]},
                   occurred_at=now, telegram_html=body[:4000], telegram_not_after=valid_until)


def signal_outcome_event(sig: Any, status: str, note: str | None) -> EventIn:
    """A stored signal that expired (no decision within the time limit) or was invalidated."""
    etype = "SIGNAL_INVALIDATED" if status == "INVALIDATED" else "SIGNAL_EXPIRED"
    word = "invalidated" if status == "INVALIDATED" else "expired"
    decision = (sig.plan or {}).get("decision")
    p = signal_payload(sig.symbol, sig.timeframe, sig.direction, sig.strategy_type, sig.candle_open_time,
                       sig.candle_close_time, sig.entry_reference, sig.stop_loss, sig.take_profit, status,
                       note or sig.explanation, decision, sig.score)
    return EventIn(event_id=f"signal:{word}:{sig.id}", event_type=etype,
                   title=f"{sig.direction} {sig.symbol} {sig.timeframe}: signal {word}", message="\n".join(_signal_lines(p)),
                   payload=p, symbol=sig.symbol, timeframe=sig.timeframe, strategy_version=sig.strategy_version,
                   signal_id=sig.id)


# ---------------------------------------------------------------------------- paper trades

def strategy_group_key(symbol: str, tf: str, direction: str, candle_open: datetime) -> str:
    return f"strategy-eval:{symbol}:{tf}:{direction}:{candle_open.isoformat()}"


def _variant(key: str) -> str:
    return f"Strategy {key} ({VARIANT_NAMES.get(key, key)})"


def _portfolio_line(t: dict[str, Any]) -> str:
    if t.get("portfolio_taken"):
        return "Paper portfolio: taken."
    return f"Paper portfolio: not taken ({t.get('portfolio_reason') or 'no reason recorded'}). Signal-level record only."


def trade_payload(t: dict[str, Any]) -> dict[str, Any]:
    keys = ("variant_key", "symbol", "timeframe", "direction", "strategy", "decision", "decision_reason", "status",
            "reference_entry", "fill_price", "fill_time", "stop_loss", "take_profit", "quantity", "risk_amount", "entry_fee",
            "exit_fee", "slippage_cost", "spread_cost", "funding_cost", "exit_time", "exit_price", "exit_reason", "gross_pnl",
            "net_pnl", "net_r", "portfolio_taken", "portfolio_reason", "cost_flags")
    return {"label": PAPER_LABEL, **{k: t.get(k) for k in keys}}


def _base(t: dict[str, Any], etype: str, suffix: str, title: str, message: list[str], **kw: Any) -> EventIn:
    data = t.get("data") or {}
    return EventIn(event_id=f"paper:{t['id']}:{suffix}", event_type=etype, title=f"{PAPER_LABEL}: {title}",
                   message="\n".join(message), payload={**trade_payload(t), **kw.pop("extra", {})}, symbol=t["symbol"],
                   timeframe=t["timeframe"], strategy_id=t["variant_key"], signal_id=data.get("signal_id"),
                   strategy_version=str(t.get("variant_id") or "").partition("@")[2] or None,
                   paper_trade_id=t["id"], **kw)


def paper_record_events(t: dict[str, Any], group_key: str | None) -> list[EventIn]:
    """Events for a new paper-trade record, chosen by what actually happened to it."""
    key, head = t["variant_key"], f"{t['direction']} {t['symbol']} {t['timeframe']}"
    who = _variant(key)
    why = t.get("decision_reason") or ""
    occurred = t["detected_at"]
    if t["status"] == "OPEN":
        return [_base(t, "PAPER_ENTRY_FILLED", "entry", f"{key} entered {head}", [
            who,
            f"Entry reference {num(t.get('reference_entry'))} · Simulated fill {num(t.get('fill_price'))} · Size {num(t.get('quantity'))}",
            f"Stop {num(t.get('stop_loss'))} · Take-profit {num(t.get('take_profit'))}",
            f"Entry fee {usd(t.get('entry_fee'))} · Slippage {usd(t.get('slippage_cost'))} · Spread {usd(t.get('spread_cost'))}",
            _portfolio_line(t)], group_key=group_key, occurred_at=occurred)]
    if t["status"] == "MISSED":
        return [_base(t, "PAPER_ENTRY_MISSED", "missed", f"{key} missed {head}", [who, why],
                      group_key=group_key, occurred_at=occurred)]
    if t["status"] == "UNFILLED":
        return [_base(t, "PAPER_ORDER_UNFILLED", "unfilled", f"{key} not filled on {head}",
                      [who, f"No position was opened: {why}"], group_key=group_key, occurred_at=occurred)]
    # REJECTED by this strategy's own rule. The baseline signal itself is unaffected.
    return [_base(t, "SIGNAL_REJECTED", "rejected", f"{key} rejected {head}", [who, why],
                  group_key=group_key, occurred_at=occurred, category="signal")]


def strategy_summary_event(symbol: str, tf: str, direction: str, candle_open: datetime, group_key: str,
                           rows: list[dict[str, Any]], signal_id: str | None, occurred: datetime) -> EventIn:
    """One grouped view of how A, B and C treated the same signal. Their results stay separate."""
    outcomes = {r["variant_key"]: paper_outcome(r) for r in rows}
    lines = [f"Strategy {k}: {outcomes[k]}" for k in sorted(outcomes)]
    return EventIn(event_id=group_key, event_type="STRATEGY_EVALUATION", is_group_summary=True, group_key=group_key,
                   title=f"{PAPER_LABEL}: {direction} {symbol} {tf} evaluated by A, B and C",
                   message="\n".join(lines), payload={"label": PAPER_LABEL, "strategies": outcomes, "direction": direction,
                                                      "candle_open_time": candle_open}, symbol=symbol, timeframe=tf,
                   signal_id=signal_id, occurred_at=occurred)


def paper_outcome(t: dict[str, Any]) -> str:
    if t["status"] == "OPEN":
        return "Accepted" + ("" if t.get("portfolio_taken") else " (signal level; portfolio blocked)")
    return {"REJECTED": "Rejected", "MISSED": "Missed", "UNFILLED": "Unfilled"}.get(t["status"], t["status"])


def paper_exit_event(t: dict[str, Any], tp_index: int = 1) -> EventIn:
    """The single notification for a finished trade. Stop and target exits already carry the
    full result, so no separate PAPER_TRADE_CLOSED is sent for them."""
    reason = t.get("exit_reason")
    etype, what = {"STOP": ("PAPER_STOP_LOSS_HIT", "stop-loss hit"),
                   "TARGET": (f"PAPER_TP{min(max(tp_index, 1), 3)}_HIT", f"TP{tp_index} hit, trade closed"),
                   }.get(reason, ("PAPER_TRADE_CLOSED", "closed at the time limit" if reason == "TIME_EXIT" else "closed"))
    net_r = t.get("net_r")
    flags = t.get("cost_flags") or {}
    lines = [
        _variant(t["variant_key"]),
        f"Simulated fill {num(t.get('fill_price'))} · Exit {num(t.get('exit_price'))} · Size {num(t.get('quantity'))}",
        f"Stop {num(t.get('stop_loss'))} · Take-profit {num(t.get('take_profit'))}",
        f"Realized simulated P&L {usd(t.get('net_pnl'))} · Net {'n/a' if net_r is None else f'{net_r:+.2f}R'}",
        (f"Fees {usd((t.get('entry_fee') or 0) + (t.get('exit_fee') or 0))} · Slippage {usd(t.get('slippage_cost'))} · "
         f"Spread {usd(t.get('spread_cost'))} · Funding {usd(t.get('funding_cost'))} ({flags.get('funding', 'n/a')})"),
        f"Duration {duration(t.get('fill_time'), t.get('exit_time'))}",
        _portfolio_line(t),
    ]
    return _base(t, etype, "exit", f"{t['variant_key']} {t['direction']} {t['symbol']} {t['timeframe']}: {what}", lines,
                 occurred_at=t["exit_time"],
                 extra={"tp_index": tp_index if reason == "TARGET" else None, "remaining_quantity": 0.0,
                        "duration": duration(t.get("fill_time"), t.get("exit_time"))})


def paper_level_event(t: dict[str, Any], level: dict[str, Any], price: float, when: datetime,
                      realized_pnl: float = 0.0, remaining_quantity: float | None = None) -> EventIn:
    """A take-profit level reached before the trade's exit target. The paper model exits in full
    at one target (changing that would change the locked strategy), so nothing is realized yet;
    realized P&L and the remaining position are always reported explicitly."""
    remaining = t.get("quantity") if remaining_quantity is None else remaining_quantity
    n = int(level["index"])
    partial = realized_pnl != 0.0 or remaining != t.get("quantity")
    state = ("Partial exit taken." if partial
             else "No partial exit in this paper model: the position stays open at full size.")
    lines = [
        _variant(t["variant_key"]),
        f"TP{n} {num(level['price'])} reached at {num(price)}. {state}",
        f"Realized simulated P&L {usd(realized_pnl)} · Remaining position {num(remaining)}",
        f"Stop {num(t.get('stop_loss'))} · Exit target {num(t.get('take_profit'))}",
    ]
    return _base(t, f"PAPER_TP{min(max(n, 1), 3)}_HIT", f"tp{n}",
                 f"{t['variant_key']} {t['direction']} {t['symbol']} {t['timeframe']}: TP{n} reached", lines, occurred_at=when,
                 extra={"tp_index": n, "closes_trade": False, "realized_pnl": realized_pnl, "remaining_quantity": remaining})


# ---------------------------------------------------------------------------- risk

RISK_EVENT_BY_CODE = {"open_risk": "RISK_MAX_OPEN_RISK", "correlated": "RISK_CORRELATED_REJECTED",
                      "daily_loss": "RISK_DAILY_LOSS_LIMIT", "max_drawdown": "RISK_DRAWDOWN_LIMIT",
                      "data_integrity": "RISK_STALE_DATA"}


def risk_entry_event(t: dict[str, Any], code: str, group_key: str | None) -> EventIn | None:
    """A paper entry the risk engine kept out of the paper portfolio. Breakers have their own
    once-per-trip events (see ``breaker_event``); ``symbol_open`` is routine and not notified."""
    etype = RISK_EVENT_BY_CODE.get(code)
    if etype is None or code in ("daily_loss", "max_drawdown"):
        return None
    key = t["variant_key"]
    return _base(t, etype, f"risk:{code}", f"{key} paper portfolio blocked {t['direction']} {t['symbol']} {t['timeframe']}",
                 [_variant(key), t.get("portfolio_reason") or code, PAPER_PORTFOLIO_NOTE], group_key=group_key,
                 occurred_at=t["detected_at"], extra={"risk_code": code})


def missing_price_event(t: dict[str, Any], group_key: str | None) -> EventIn:
    key = t["variant_key"]
    return _base(t, "RISK_MISSING_PRICE", "risk:missing_price", f"{key}: no exchange price for {t['symbol']}",
                 [_variant(key), "No live Bybit price was available when the signal was detected, so no paper entry was made.",
                  PAPER_PORTFOLIO_NOTE], group_key=group_key, occurred_at=t["detected_at"])


def breaker_event(key: str, variant_id: str, version: str | None, breaker: str, state: dict[str, Any], when: datetime,
                  generation: str, positions: list[dict[str, Any]]) -> EventIn:
    daily = breaker == "daily_loss"
    name = "Daily loss limit" if daily else "Portfolio drawdown limit"
    limit = state.get("limit_pct")
    value = f"{state.get('value')} USDT today" if daily else f"{state.get('value_pct')}% below peak"
    until = "until the next UTC day" if daily else "until a manual reset"
    return EventIn(
        event_id=f"breaker:{breaker}:{variant_id}:{generation}",
        event_type="RISK_DAILY_LOSS_LIMIT" if daily else "RISK_DRAWDOWN_LIMIT",
        title=f"{_variant(key)}: paper circuit breaker activated ({name})",
        message="\n".join([f"{name} reached on the paper portfolio of {_variant(key)}: {value} (limit {limit}%).",
                           f"This paper portfolio takes no new positions {until}.", PAPER_PORTFOLIO_NOTE]),
        payload={"label": PAPER_LABEL, "breaker": breaker, "circuit_breaker": True, "threshold": state,
                 "affected_positions": positions, "strategy": key},
        strategy_id=key, strategy_version=version, occurred_at=when)


def breaker_reset_event(key: str, variant_id: str, version: str | None, note: str, reviewer: str, when: datetime) -> EventIn:
    return EventIn(
        event_id=f"breaker-reset:{variant_id}:{when.isoformat()}", event_type="CIRCUIT_BREAKER_RESET",
        title=f"{_variant(key)}: paper circuit breaker manually reset",
        message="\n".join([f"Reset by {reviewer}: {note}", PAPER_PORTFOLIO_NOTE]),
        payload={"label": PAPER_LABEL, "breaker": "max_drawdown", "reviewer": reviewer, "note": note, "strategy": key},
        strategy_id=key, strategy_version=version, occurred_at=when)


# ---------------------------------------------------------------------------- market & validation

def correlated_summary(direction: str, group_key: str, symbols: list[str], threshold: float, when: datetime) -> EventIn:
    coins = [s.removesuffix("USDT") for s in symbols]
    shown = ", ".join(coins[:3]) + (" and other assets" if len(coins) > 3 else "")
    return EventIn(event_id=group_key, event_type="MARKET_SIGNAL_GROUP", is_group_summary=True, group_key=group_key,
                   title=f"{len(coins)} correlated {direction} candidates detected across {shown}",
                   message=(f"Same-direction qualified signals on coins correlated at {threshold:g} or more: {', '.join(coins)}. "
                            "They behave more like one market move than independent opportunities. "
                            "Each signal keeps its own record."),
                   payload={"direction": direction, "symbols": symbols, "correlation_threshold": threshold},
                   occurred_at=when, telegram=False)


def price_alert_event(a: dict[str, Any], last: float, when: datetime, telegram_status: str) -> EventIn:
    cond = "at or above" if a["condition"] == "above" else "at or below"
    note = f"\nNote: {a['note']}" if a.get("note") else ""
    return EventIn(event_id=f"price-alert:{a['id']}", event_type="MARKET_PRICE_ALERT",
                   title=f"{a['symbol']} price alert: {cond} {num(a['price'])}",
                   message=f"Last price {num(last)}.{note}",
                   payload={"alert_id": a["id"], "level": a["price"], "condition": a["condition"], "last": last,
                            "telegram": telegram_status}, symbol=a["symbol"], occurred_at=when, telegram=False)


def validation_decision_event(key: str, variant_id: str, version: str | None, decision: str, readiness: str, note: str,
                              reviewer: str, when: datetime) -> EventIn:
    return EventIn(event_id=f"validation:decision:{variant_id}:{when.isoformat()}", event_type="VALIDATION_DECISION",
                   title=f"{_variant(key)}: review recorded ({decision})",
                   message=f"Readiness {readiness}. Reviewer {reviewer}: {note}\nRecorded only: production signals and enforcement are unchanged.",
                   payload={"decision": decision, "readiness": readiness, "strategy": key}, strategy_id=key,
                   strategy_version=version, occurred_at=when)


def validation_period_event(key: str, variant_id: str, version: str, when: datetime) -> EventIn:
    return EventIn(event_id=f"validation:period:{variant_id}", event_type="VALIDATION_PERIOD_STARTED", severity="WARNING",
                   title=f"{_variant(key)}: forward test restarted by a settings change",
                   message=(f"The strategy or paper configuration changed, so a new evaluation period started under version {version}. "
                            "Evidence restarts from zero; earlier results stay in their own period and are not lost."),
                   payload={"version": version, "strategy": key}, strategy_id=key, strategy_version=version, occurred_at=when)


def validation_ready_event(key: str, variant_id: str, version: str, when: datetime) -> EventIn:
    return EventIn(event_id=f"validation:ready:{variant_id}", event_type="VALIDATION_READY_FOR_REVIEW",
                   title=f"{_variant(key)}: ready for manual review",
                   message="The promotion gate passed on forward data. This only requests a human review: nothing changes in production.",
                   payload={"strategy": key}, strategy_id=key, strategy_version=version, occurred_at=when)


# ---------------------------------------------------------------------------- Telegram

def telegram_message(ev: EventIn, tz: str) -> str:
    """Generic HTML rendering. Only the event's own text is included, so no credentials or
    private account data can reach the chat."""
    if ev.telegram_html:
        return ev.telegram_html
    when = ev.occurred_at.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
    lines = [f"{SEVERITY_EMOJI.get(ev.severity or 'INFO', '')} <b>{html.escape(ev.title)}</b>", html.escape(ev.message), "",
             f"<i>{when} {html.escape(tz)}</i>"]
    if ev.category == "trade" or ev.paper_trade_id:
        lines.append(f"<i>{PAPER_FOOTER}</i>")
    return "\n".join(lines)  # the caller fits this to Telegram's limit without breaking a tag


def signal_not_after(candle_close: datetime, tf_len: timedelta, max_age_candles: int) -> datetime:
    return candle_close + tf_len * max_age_candles
