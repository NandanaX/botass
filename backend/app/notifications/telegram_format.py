"""The one place Telegram messages are written. Presentation only: it reads an event's stored fields and
never decides anything, so changing a template can't change a signal, a paper trade or a risk check.

Compact mode has a short template per event type. Detailed mode is the older long text. A line is left
out when its value is missing, and no value is ever invented. Every dynamic value is escaped.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.notifications.events import EventIn
from app.notifications.messages import SEVERITY_EMOJI, telegram_message
from app.notifications.settings import NotificationSettings, public_url

MAX_LENGTH = 4000  # Telegram's own limit is 4096 characters
MAX_WATCH_LINES = 10
MAX_REASON = 120
RESEARCH_FOOTER = "🧪 Research only · No order placed"
UNCONFIRMED = "⚠️ No entry confirmed yet."
WATCH_BRIEF = {"awaiting_breakout_close": "Awaiting breakout close", "awaiting_retest": "Awaiting retest",
               "rejection_not_confirmed": "Awaiting rejection"}
WATCH_FALLBACK = "Setup developing"
SHADOW_HEADLINE = {"QUALIFIED": "✅ <b>SHADOW: WOULD TAKE</b>", "WATCHLIST": "👀 <b>SHADOW: WOULD WATCH</b>", "REJECTED": "⚠️ <b>SHADOW: WOULD REJECT</b>",
                   "NO_TRADE": "⚠️ <b>SHADOW: NO TRADE</b>", "ERROR": "⚠️ <b>SHADOW: ENGINE ERROR</b>"}
_TF_ORDER = ("5m", "15m", "30m", "1h", "4h", "1d")


def esc(value: Any) -> str:
    return html.escape(str(value))


def price(x: float) -> str:
    return f"${x:,.2f}" if abs(x) >= 1000 else f"${x:,.4f}" if abs(x) >= 1 else f"${x:.6g}"


def money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def quantity(x: float) -> str:
    return f"{x:,.8f}".rstrip("0").rstrip(".") if x else "0"


def coin(symbol: str | None) -> str:
    return esc((symbol or "").removesuffix("USDT"))


def short_reason(text: Any) -> str:
    """The first sentence, cut to a readable length, not yet escaped."""
    t = " ".join(str(text or "").split())
    first = re.split(r"(?<=[.!?])\s", t, maxsplit=1)[0]
    return first if len(first) <= MAX_REASON else first[:MAX_REASON - 1].rstrip() + "…"


def _link(prefs: NotificationSettings, path: str, label: str = "Details") -> list[str]:
    base = public_url(prefs.dashboard_url)
    return [f'🔗 <a href="{esc(base + path)}">{label}</a>'] if base else []


def _when(at: datetime | None, tz: str) -> list[str]:
    if at is None:
        return []
    return [f"🕒 {at.astimezone(ZoneInfo(tz)).strftime('%d %b %H:%M')} {esc(tz)}"]


def fit(text: str) -> str:
    """Within Telegram's limit, without cutting through a tag or an escaped character."""
    if len(text) <= MAX_LENGTH:
        return text
    cut = text.rfind("\n", 0, MAX_LENGTH - 2)
    if cut > 0:
        return text[:cut] + "\n…"
    plain = esc(html.unescape(re.sub(r"<[^>]+>", "", text)))[:MAX_LENGTH - 2]
    amp = plain.rfind("&")
    if amp > plain.rfind(";"):
        plain = plain[:amp]
    return plain + "\n…"


def _dir(direction: str | None) -> str:
    return "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪"


def _pair(direction: str | None) -> str:
    return "BUY / LONG" if direction == "LONG" else "SELL / SHORT" if direction == "SHORT" else esc(direction or "")


def _shadow(decision: dict[str, Any] | None) -> list[str]:
    if not decision or not decision.get("outcome"):
        return []
    outcome = decision["outcome"]
    if decision.get("mode") == "enforce":
        head = "✅ <b>ACCEPTED</b>" if outcome == "QUALIFIED" else f"⛔ <b>BLOCKED: {esc(outcome)}</b>"
    else:
        head = SHADOW_HEADLINE.get(outcome, f"⚠️ <b>SHADOW: {esc(outcome)}</b>")
    reason = short_reason(decision.get("reason"))
    return [head] + ([f"Reason: {esc(reason)}"] if reason and outcome != "QUALIFIED" else [])


def _alignment(direction: str | None, htf: list[dict[str, Any]] | None) -> list[str]:
    """Higher-timeframe agreement in one line."""
    rows = [h for h in htf or [] if h.get("timeframe") and h.get("trend")]
    if not rows:
        return []
    names = "/".join(esc(str(h["timeframe"]).upper()) for h in rows)
    want = "BULLISH" if direction == "LONG" else "BEARISH"
    if all(h["trend"] == want for h in rows):
        return [f"📈 <b>{names}:</b> {want.title()} alignment"]
    return [f"📈 <b>{names}:</b> " + " · ".join(esc(str(h["trend"]).replace("_", " ").title()) for h in rows)]


def _targets(targets: list[dict[str, Any]] | None, primary: int | None) -> list[str]:
    out = []
    for k, t in enumerate(targets or []):
        if t.get("price") is None:
            continue
        tags = " ⭐ Primary" if k == primary and len(targets or []) > 1 else ""
        tags += " ⚠️ Beyond zone" if t.get("blocked") else ""
        out.append(f"🎯 <b>{esc(t.get('label') or f'TP{k + 1}')}:</b> {price(t['price'])}{tags}")
    return out


def _signal_size(symbol: str | None, size: dict[str, Any] | None) -> list[str]:
    s = size or {}
    out = []
    if s.get("quantity") is not None:
        out.append(f"💰 <b>Size:</b> {quantity(s['quantity'])} {esc(s.get('base') or coin(symbol))}")
    if s.get("risk") is not None:
        out.append(f"💵 <b>Risk:</b> ~${s['risk']:,.2f}")
    return out


def signal_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    p = ev.payload
    d = p.get("direction")
    lines = [f"{_dir(d)} <b>{esc(ev.symbol)} · {_pair(d)}</b>", f"📊 {esc((ev.timeframe or '').upper())} · Research Signal", ""]
    if p.get("entry_reference") is not None:
        lines.append(f"<b>ENTRY (reference):</b> {price(p['entry_reference'])}")
    if p.get("stop_loss") is not None:
        lines.append(f"🛑 <b>STOP:</b> {price(p['stop_loss'])}")
    targets = p.get("targets") or ([{"label": "Target", "price": p["take_profit"]}] if p.get("take_profit") is not None else [])
    lines += ["", *_targets(targets, p.get("primary_target_index")), ""]
    lines += _signal_size(ev.symbol, p.get("size"))
    if p.get("net_risk_reward"):
        lines.append(f"⚖️ <b>Net R:R:</b> 1:{p['net_risk_reward']:.2f}")
    lines += ["", *_alignment(d, p.get("htf")), "", *_shadow(p.get("decision")), "", *_when(ev.occurred_at, prefs.timezone), RESEARCH_FOOTER]
    return [*lines, *_link(prefs, f"/signals?id={quote(str(ev.signal_id), safe='')}")] if ev.signal_id else lines


def strategy_d_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    p = ev.payload
    d = p.get("direction")
    plan = p.get("plan") or {}
    lines = [f"{_dir(d)} <b>{esc(ev.symbol)} · {_pair(d)}</b>", "📊 15M · Strategy D Research", ""]
    if plan.get("entry_reference") is not None:
        lines.append(f"<b>ENTRY (reference):</b> {price(plan['entry_reference'])}")
    if plan.get("stop_loss") is not None:
        lines.append(f"🛑 <b>STOP:</b> {price(plan['stop_loss'])}")
    lines += ["", *_targets(plan.get("targets"), None), "", *_signal_size(ev.symbol, {"quantity": p.get("quantity"), "risk": p.get("risk_amount")}),
              "", *_alignment(d, p.get("htf")), "", *_when(ev.occurred_at, prefs.timezone), "🧪 Research shadow · No order placed"]
    return lines


def _tf_rank(tf: str, first_15m: bool) -> int:
    if first_15m and tf == "15m":
        return -1
    i = _TF_ORDER.index(tf) if tf in _TF_ORDER else len(_TF_ORDER)
    return i if first_15m else -i


def watchlist_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    """Never carries a price: a watchlist candidate has no confirmed entry, stop or target."""
    items = {(x.get("symbol"), x.get("timeframe"), x.get("direction")): x for x in ev.payload.get("items") or []}
    rows = sorted(items.values(), key=lambda x: (_tf_rank(x.get("timeframe") or "", prefs.watchlist_15m_first), x.get("symbol") or ""))
    both = len({x.get("direction") for x in rows}) > 1
    if both:  # stable, so each side keeps the timeframe order
        rows.sort(key=lambda x: x.get("direction") != "LONG")
    shown = rows[:MAX_WATCH_LINES]
    n = len(rows)
    lines = [f"👀 <b>MARKET WATCH · {n} SETUP{'' if n == 1 else 'S'}</b>", "⏳ <b>Not confirmed signals</b>", ""]
    previous = None
    for x in shown:
        if both and previous is not None and x.get("direction") != previous:
            lines.append("")
        previous = x.get("direction")
        lines.append(f"{_dir(x.get('direction'))} {coin(x.get('symbol'))} {esc((x.get('timeframe') or '').upper())} — {esc(x.get('brief') or WATCH_FALLBACK)}")
    if n > len(shown):
        lines.append(f"…and {n - len(shown)} more")
    return [*lines, "", UNCONFIRMED, *_link(prefs, "/scanner")]


def _paper_footer(ev: EventIn) -> str:
    p = ev.payload
    version = (ev.strategy_version or "").split("~")[0]
    parts = [f"🧪 Strategy {esc(ev.strategy_id)}"] + ([f"v{esc(version)}"] if version else [])
    parts.append(f"{esc((ev.timeframe or '').upper())} {esc(p.get('direction') or '')}".strip())
    return " · ".join([*parts, "Paper Trading"])


def paper_entry_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    p = ev.payload
    d = p.get("direction")
    lines = [f"{_dir(d)} <b>{esc(ev.symbol)} · PAPER {esc(d)} OPENED</b>", ""]
    if p.get("fill_price") is not None:
        lines.append(f"💰 <b>Simulated fill:</b> {price(p['fill_price'])}")
    if p.get("quantity") is not None:
        lines.append(f"📦 <b>Size:</b> {quantity(p['quantity'])} {coin(ev.symbol)}")
    if p.get("stop_loss") is not None:
        lines.append(f"🛑 <b>Stop:</b> {price(p['stop_loss'])}")
    if p.get("take_profit") is not None:
        lines.append(f"🎯 <b>Target:</b> {price(p['take_profit'])}")
    if p.get("portfolio_taken") is False:
        lines += ["", f"⚠️ Not in paper portfolio: {esc(short_reason(p.get('portfolio_reason')))}"]
    return [*lines, "", _paper_footer(ev)]


def _result_lines(p: dict[str, Any]) -> list[str]:
    net, r = p.get("net_pnl"), p.get("net_r")
    lines = []
    if net is not None:
        lines.append(f"{'💚' if net >= 0 else '🔻'} <b>Net P&amp;L:</b> {money(net)}")
    if r is not None:
        lines.append(f"{'📈' if r >= 0 else '📉'} <b>Result:</b> {r:+.2f}R")
    return lines


def paper_tp_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    p = ev.payload
    n = p.get("tp_index") or ev.event_type[8]
    if p.get("closes_trade") is False:  # the position is still open, so its total result isn't known yet
        realized, remaining = p.get("realized_pnl") or 0.0, p.get("remaining_quantity")
        partial = realized != 0.0 or remaining != p.get("quantity")
        lines = [f"✅ <b>{esc(ev.symbol)} · TP{esc(n)} {'PARTIAL EXIT' if partial else 'REACHED'}</b>", ""]
        lines.append(f"💵 <b>Realized so far:</b> {money(realized)}")
        if remaining is not None:
            lines.append(f"📦 <b>Still open:</b> {quantity(remaining)} {coin(ev.symbol)}")
        return [*lines, "⏳ Final result comes when the trade closes.", "", _paper_footer(ev)]
    return [f"✅ <b>{esc(ev.symbol)} · TP{esc(n)} HIT</b>", "", *_result_lines(p), "", _paper_footer(ev)]


def paper_stop_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    return [f"🛑 <b>{esc(ev.symbol)} · STOP HIT</b>", "", *_result_lines(ev.payload), "", _paper_footer(ev)]


def paper_closed_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    word = "TIME LIMIT" if ev.payload.get("exit_reason") == "TIME_EXIT" else "CLOSED"
    return [f"⏱ <b>{esc(ev.symbol)} · {word}</b>", "", *_result_lines(ev.payload), "", _paper_footer(ev)]


def paper_not_entered_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    word = "ENTRY MISSED" if ev.event_type == "PAPER_ENTRY_MISSED" else "NOT FILLED"
    return [f"⏭ <b>{esc(ev.symbol)} · {word}</b>", esc(short_reason(ev.payload.get("decision_reason"))), "", _paper_footer(ev)]


def breaker_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    p = ev.payload
    state = p.get("threshold") or {}
    daily = p.get("breaker") == "daily_loss"
    head = "DAILY LOSS LIMIT HIT" if daily else "DRAWDOWN LIMIT HIT"
    detail = (f"Today: {money(state['value'])} (limit {state.get('limit_pct')}%)" if daily and state.get("value") is not None
              else f"Drawdown: {state['value_pct']}% (limit {state.get('limit_pct')}%)" if state.get("value_pct") is not None else "")
    until = "the next UTC day" if daily else "a manual reset"
    return [f"🚨 <b>{head}</b>", "", f"Strategy {esc(p.get('strategy') or ev.strategy_id)}", *([esc(detail)] if detail else []), "",
            f"⛔ New paper entries blocked until {until}.", "🧪 Paper Trading"]


def risk_entry_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    p = ev.payload
    heads = {"RISK_MAX_OPEN_RISK": "🚨 <b>PORTFOLIO RISK WARNING</b>", "RISK_CORRELATED_REJECTED": "⚠️ <b>CORRELATED ENTRY BLOCKED</b>",
             "RISK_STALE_DATA": "🚨 <b>STALE MARKET DATA</b>", "RISK_MISSING_PRICE": "🚨 <b>NO EXCHANGE PRICE</b>"}
    message_lines = ev.message.split("\n")
    reason = short_reason(p.get("portfolio_reason") or (message_lines[1] if len(message_lines) > 1 else ev.message))
    trade = f"{coin(ev.symbol)} {esc((ev.timeframe or '').upper())} {esc(p.get('direction') or '')}".strip()
    return [heads[ev.event_type], "", f"Strategy {esc(ev.strategy_id)}", esc(reason), "", f"⛔ {trade} not entered on paper.", "🧪 Paper Trading"]


def incident_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    emoji = "✅" if ev.event_type == "INCIDENT_RESOLVED" else SEVERITY_EMOJI.get(ev.severity or "INFO", "⚠️")
    return [f"{emoji} <b>{esc(ev.title)}</b>", "", esc(short_reason(ev.message)), *_when(ev.occurred_at, prefs.timezone)]


def generic_text(ev: EventIn, prefs: NotificationSettings) -> list[str]:
    """Anything without its own template: the headline and one short line."""
    lines = [f"{SEVERITY_EMOJI.get(ev.severity or 'INFO', 'ℹ️')} <b>{esc(ev.title)}</b>"]
    if ev.message:
        lines.append(esc(short_reason(ev.message)))
    if ev.paper_trade_id:
        lines += ["", _paper_footer(ev)]
    return lines


TEMPLATES: dict[str, Callable[[EventIn, NotificationSettings], list[str]]] = {
    "SIGNAL_QUALIFIED": signal_text, "WATCHLIST_DIGEST": watchlist_text, "STRATEGY_D_QUALIFIED": strategy_d_text,
    "PAPER_ENTRY_FILLED": paper_entry_text, "PAPER_TP1_HIT": paper_tp_text, "PAPER_TP2_HIT": paper_tp_text, "PAPER_TP3_HIT": paper_tp_text,
    "PAPER_STOP_LOSS_HIT": paper_stop_text, "PAPER_TRADE_CLOSED": paper_closed_text, "PAPER_ENTRY_MISSED": paper_not_entered_text, "PAPER_ORDER_UNFILLED": paper_not_entered_text,
    "RISK_DRAWDOWN_LIMIT": breaker_text, "RISK_DAILY_LOSS_LIMIT": breaker_text,
    "RISK_MAX_OPEN_RISK": risk_entry_text, "RISK_CORRELATED_REJECTED": risk_entry_text, "RISK_STALE_DATA": risk_entry_text, "RISK_MISSING_PRICE": risk_entry_text,
    "INCIDENT_OPENED": incident_text, "INCIDENT_ESCALATED": incident_text, "INCIDENT_RESOLVED": incident_text,
}


def _tidy(lines: list[str]) -> str:
    """Joins the lines. Omitted values can leave several blank lines together, or one at the end."""
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def render(ev: EventIn, prefs: NotificationSettings) -> str:
    """The text to deliver for this event."""
    if prefs.telegram_format == "detailed":
        return fit(telegram_message(ev, prefs.timezone))
    # A paper risk event that came from a breaker has no trade; a stale-data incident may use the same type.
    template = TEMPLATES.get(ev.event_type, generic_text)
    if template is risk_entry_text and not ev.symbol:
        template = incident_text
    return fit(_tidy(template(ev, prefs)))


def price_alert_text(symbol: str, condition: str, level: float, last: float, note: str | None, at: datetime) -> str:
    """Sent by the price-alert service, which has no event at that point."""
    above = condition == "above"
    lines = [f"🔔 <b>{esc(symbol)} · PRICE ALERT</b> {'⬆️' if above else '⬇️'}", "", f"Price is {'at or above' if above else 'at or below'} <b>{price(level)}</b>", f"Last: {price(last)}"]
    if note:
        lines.append(f"Note: {esc(note)}")
    return fit("\n".join([*lines, "", f"🕒 {at.strftime('%d %b %H:%M')} UTC · Price only, not a trade signal."]))
