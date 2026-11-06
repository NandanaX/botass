"""Compact Telegram templates, built from the same events the app stores. Nothing here reaches Telegram."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.alerts.price import format_price_alert
from app.db.models import NotificationDelivery, NotificationEvent
from app.notifications.events import EventIn, route
from app.notifications.messages import (
    breaker_event,
    paper_exit_event,
    paper_level_event,
    paper_record_events,
    risk_entry_event,
    signal_qualified_event,
    watchlist_digest_event,
)
from app.notifications.service import NotificationService
from app.notifications.settings import NotificationSettings
from app.notifications.telegram_format import MAX_LENGTH, MAX_WATCH_LINES, fit, render
from app.settings_store import RuntimeSettings
from app.workers.scanner import Scanner

NOW = datetime(2026, 11, 6, 4, 15, tzinfo=UTC)
PREFS = NotificationSettings()


class Balance(HTMLParser):
    """Telegram refuses a message whose tags don't balance or aren't on its list."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ("b", "i", "a"):
            self.bad.append(tag)
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.bad.append(f"/{tag}")


def valid_html(text):
    b = Balance()
    b.feed(text)
    return not b.bad and not b.stack and not re.search(r"&(?!amp;|lt;|gt;|quot;|#x27;)", text)


def signal_row(direction="SHORT", **plan_extra):
    long = direction == "LONG"
    plan = {"targets": [{"label": f"TP{i}", "price": p, "r_multiple": i} for i, p in enumerate((0.8542, 0.8477, 0.8412), 1)], "primary_target_index": 1, "net_risk_reward": 1.51,
            "sizing": {"quantity": 12820.0, "estimated_loss": 100.2, "rules": {"base_coin": "SUI"}}, "decision": {"outcome": "REJECTED", "mode": "shadow", "reason": "Correlated DOGE SHORT already open. More detail that stays in the app."}, **plan_extra}
    return {"id": "sig1", "symbol": "SUIUSDT", "timeframe": "15m", "direction": direction, "strategy_type": "breakout", "candle_open_time": NOW - timedelta(minutes=15), "candle_close_time": NOW,
            "entry_reference": 0.8606, "stop_loss": 0.8671, "take_profit": 0.8477, "setup_status": "QUALIFIED", "explanation": "EMA20 above EMA50, RSI 41.2, MACD histogram negative",
            "score": 70, "strategy_version": "2.0.0+52ede346", "plan": plan, "mtf_details": [{"timeframe": "1h", "trend": "BULLISH" if long else "BEARISH"}, {"timeframe": "4h", "trend": "BULLISH" if long else "BEARISH"}]}


def signal_text(prefs=PREFS, **kw):
    return render(signal_qualified_event(signal_row(**kw), "<b>LONG DETAILED TEXT</b>", NOW + timedelta(hours=1)), prefs)


def trade(direction="LONG", **kw):
    return {"id": "t1", "variant_key": "C", "variant_id": "C@2.0.0+52ede346~c376938a93", "symbol": "BTCUSDT", "timeframe": "15m", "direction": direction, "status": "OPEN", "reference_entry": 68000.0, "fill_price": 68050.0,
            "quantity": 0.015, "stop_loss": 67400.0, "take_profit": 69350.0, "portfolio_taken": True, "detected_at": NOW, "fill_time": NOW, "data": {}, **kw}


# ----------------------------------------------------------------------- confirmed signals

def test_short_signal_matches_the_compact_layout():
    text = signal_text()
    assert text.startswith("🔴 <b>SUIUSDT · SELL / SHORT</b>\n📊 15M · Research Signal")
    for line in ("<b>ENTRY (reference):</b> $0.8606", "🛑 <b>STOP:</b> $0.8671", "🎯 <b>TP1:</b> $0.8542", "🎯 <b>TP2:</b> $0.8477 ⭐ Primary", "🎯 <b>TP3:</b> $0.8412", "💰 <b>Size:</b> 12,820 SUI",
                 "💵 <b>Risk:</b> ~$100.20", "⚖️ <b>Net R:R:</b> 1:1.51", "📈 <b>1H/4H:</b> Bearish alignment", "⚠️ <b>SHADOW: WOULD REJECT</b>", "Reason: Correlated DOGE SHORT already open.", "🧪 Research only · No order placed"):
        assert line in text
    assert valid_html(text)


def test_long_signal_is_green_and_says_long():
    text = signal_text(direction="LONG")
    assert text.startswith("🟢 <b>SUIUSDT · BUY / LONG</b>") and "Bullish alignment" in text and "🔴" not in text


def test_signal_leaves_the_technical_explanation_in_the_app():
    text = signal_text()
    for word in ("EMA", "MACD", "RSI", "More detail", "Signal Score", "CRYPTO TRADING SETUP"):
        assert word not in text
    assert len(text) < 700


def test_signal_says_no_order_and_keeps_entry_as_a_reference():
    text = signal_text()
    assert "No order placed" in text and "ENTRY (reference)" in text and "fill" not in text.lower() and "opened" not in text.lower()


@pytest.mark.parametrize(("outcome", "mode", "expected", "shows_reason"), [
    ("QUALIFIED", "shadow", "SHADOW: WOULD TAKE", False), ("REJECTED", "shadow", "SHADOW: WOULD REJECT", True), ("WATCHLIST", "shadow", "SHADOW: WOULD WATCH", True),
    ("QUALIFIED", "enforce", "ACCEPTED", False)])
def test_shadow_wording(outcome, mode, expected, shows_reason):
    text = signal_text(decision={"outcome": outcome, "mode": mode, "reason": "the reason"})
    assert expected in text and ("Reason: the reason" in text) is shows_reason
    if mode == "shadow":
        assert "BLOCKED" not in text and "ACCEPTED" not in text


def test_missing_optional_values_are_omitted_and_never_invented():
    text = signal_text(sizing=None, net_risk_reward=None, decision=None)
    for gone in ("Size", "Risk", "Net R:R", "SHADOW", "n/a", "None"):
        assert gone not in text
    bare = signal_row()
    bare["plan"] = {}
    bare["mtf_details"] = []
    bare["stop_loss"] = None
    text = render(signal_qualified_event(bare, "x", NOW), PREFS)
    assert "🎯 <b>Target:</b> $0.8477" in text and "STOP" not in text and "1H" not in text and "\n\n\n" not in text and "None" not in text


def test_timestamp_uses_the_configured_time_zone():
    assert "🕒 06 Nov 09:45 Asia/Colombo" in signal_text()
    assert "🕒 06 Nov 04:15 UTC" in signal_text(prefs=NotificationSettings(timezone="UTC"))


def test_detailed_mode_keeps_the_full_message():
    prefs = NotificationSettings(telegram_format="detailed")
    assert signal_text(prefs=prefs) == "<b>LONG DETAILED TEXT</b>"
    assert PREFS.telegram_format == "compact"


# ----------------------------------------------------------------------- watchlist

def items(n=9, direction="SHORT", tf="15m"):
    coins = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "LINK", "AVAX", "SUI", "ADA", "DOT", "LTC"]
    return [{"symbol": f"{coins[i]}USDT", "timeframe": tf, "direction": direction, "why": "a long explanation " * 10, "brief": "Awaiting rejection"} for i in range(n)]


def digest(rows, prefs=PREFS):
    return render(watchlist_digest_event(rows, NOW, NOW + timedelta(minutes=10)), prefs)


def test_watchlist_is_short_and_marked_unconfirmed():
    text = digest(items(3))
    assert text.startswith("👀 <b>MARKET WATCH · 3 SETUPS</b>\n⏳ <b>Not confirmed signals</b>") and text.endswith("⚠️ No entry confirmed yet.")
    assert "🔴 BTC 15M — Awaiting rejection" in text and "a long explanation" not in text and valid_html(text)


def test_watchlist_never_shows_prices_or_trade_language():
    text = digest(items(9))
    assert "$" not in text and "ENTRY" not in text and "STOP" not in text and "TP1" not in text


def test_watchlist_puts_15m_first_then_higher_timeframes():
    rows = items(1, tf="4h") + [{**items(2)[1], "timeframe": "1h"}] + [{**items(3)[2], "timeframe": "15m"}]
    text = digest(rows)
    assert text.index("15M") < text.index("1H") < text.index("4H")
    off = digest(rows, NotificationSettings(watchlist_15m_first=False))
    assert off.index("4H") < off.index("1H") < off.index("15M")


def test_watchlist_groups_long_and_short():
    text = digest(items(2, "SHORT") + [{**x, "symbol": "ADAUSDT"} for x in items(1, "LONG")])
    assert text.index("🟢") < text.index("🔴")


def test_watchlist_caps_lines_and_counts_the_rest():
    text = digest(items(12))
    assert "12 SETUPS" in text and text.count("Awaiting rejection") == MAX_WATCH_LINES and "…and 2 more" in text


def test_watchlist_removes_duplicates_and_singular_is_right():
    text = digest(items(1) + items(1))
    assert text.count("BTC 15M") == 1 and "1 SETUP</b>" in text


def test_watchlist_link_needs_a_public_address():
    assert "href" not in digest(items(1))
    assert 'href="https://app.example.com/scanner"' in digest(items(1), NotificationSettings(dashboard_url="https://app.example.com/"))


@pytest.mark.parametrize("url", ["http://localhost:3000", "http://127.0.0.1:3000", "http://192.168.1.5:3000", "http://10.0.0.2", "http://mybox.local", "ftp://example.com", "not a url"])
def test_local_dashboard_addresses_are_refused(url):
    with pytest.raises(ValidationError):
        NotificationSettings(dashboard_url=url)


def test_a_localhost_address_already_stored_never_reaches_a_message():
    prefs = NotificationSettings.model_construct(**{**PREFS.model_dump(), "dashboard_url": "http://localhost:3000"})
    assert "localhost" not in digest(items(1), prefs) and "localhost" not in signal_text(prefs=prefs)


# ----------------------------------------------------------------------- paper trades

def test_paper_entry_shows_the_simulated_fill_not_the_reference():
    text = render(paper_record_events(trade(), None)[0], PREFS)
    assert text.startswith("🟢 <b>BTCUSDT · PAPER LONG OPENED</b>") and "<b>Simulated fill:</b> $68,050.00" in text and "$68,000" not in text
    assert "<b>Size:</b> 0.015 BTC" in text and "<b>Stop:</b> $67,400.00" in text and "<b>Target:</b> $69,350.00" in text
    assert "🧪 Strategy C · v2.0.0+52ede346 · 15M LONG · Paper Trading" in text and valid_html(text)


def test_short_paper_entry_is_red():
    assert render(paper_record_events(trade("SHORT"), None)[0], PREFS).startswith("🔴 <b>BTCUSDT · PAPER SHORT OPENED</b>")


def closed(reason, net_pnl, net_r, **kw):
    return trade(status="CLOSED", exit_reason=reason, exit_price=69000.0, exit_time=NOW, net_pnl=net_pnl, net_r=net_r, **kw)


def test_take_profit_shows_net_result():
    text = render(paper_exit_event(closed("TARGET", 18.4, 1.84), 2), PREFS)
    assert text.startswith("✅ <b>BTCUSDT · TP2 HIT</b>") and "💚 <b>Net P&amp;L:</b> +$18.40" in text and "📈 <b>Result:</b> +1.84R" in text and "Paper Trading" in text and valid_html(text)


def test_stop_shows_the_actual_exit_after_costs_not_exactly_minus_one_r():
    text = render(paper_exit_event(closed("STOP", -10.8, -1.08)), PREFS)
    assert text.startswith("🛑 <b>BTCUSDT · STOP HIT</b>") and "🔻 <b>Net P&amp;L:</b> -$10.80" in text and "📉 <b>Result:</b> -1.08R" in text


def test_level_reached_without_a_partial_exit_shows_no_final_result():
    text = render(paper_level_event(trade(), {"index": 1, "price": 68700.0}, 68710.0, NOW), PREFS)
    assert "TP1 REACHED" in text and "Still open:</b> 0.015 BTC" in text and "Realized so far:</b> +$0.00" in text and "Result" not in text.replace("Final result", "") and "Net P&amp;L" not in text


def test_partial_exit_shows_what_is_realized_and_what_remains():
    text = render(paper_level_event(trade(), {"index": 2, "price": 68700.0}, 68710.0, NOW, realized_pnl=9.5, remaining_quantity=0.005), PREFS)
    assert "TP2 PARTIAL EXIT" in text and "+$9.50" in text and "Still open:</b> 0.005 BTC" in text and "Final result comes when the trade closes" in text


def test_time_exit_and_missing_result_values():
    assert "TIME LIMIT" in render(paper_exit_event(closed("TIME_EXIT", -2.0, -0.2)), PREFS)
    text = render(paper_exit_event(closed("STOP", None, None)), PREFS)
    assert "Net P&amp;L" not in text and "Result" not in text and "n/a" not in text


# ----------------------------------------------------------------------- risk

def test_drawdown_breaker_is_short_and_clear():
    ev = breaker_event("C", "C@v", "2.0", "max_drawdown", {"value_pct": 10.2, "limit_pct": 10.0, "tripped": True}, NOW, "initial", [])
    text = render(ev, PREFS)
    assert text.startswith("🚨 <b>DRAWDOWN LIMIT HIT</b>") and "Strategy C" in text and "Drawdown: 10.2% (limit 10.0%)" in text and "⛔ New paper entries blocked until a manual reset." in text
    assert ev.severity == "CRITICAL" and len(text) < 200


def test_daily_loss_breaker():
    text = render(breaker_event("B", "B@v", "2.0", "daily_loss", {"value": -210.5, "limit_pct": 2.0}, NOW, "2026-11-06", []), PREFS)
    assert "DAILY LOSS LIMIT HIT" in text and "-$210.50" in text and "until the next UTC day" in text


def test_open_risk_block_names_only_the_trade_it_blocked():
    ev = risk_entry_event(trade(portfolio_reason="open risk would be 2.30% of equity (limit 2%)"), "open_risk", None)
    text = render(ev, PREFS)
    assert "PORTFOLIO RISK WARNING" in text and "Strategy C" in text and "2.30%" in text and "⛔ BTC 15M LONG not entered on paper." in text


def test_incident_without_a_trade_uses_a_short_template():
    ev = EventIn(event_id="x", event_type="RISK_STALE_DATA", title="Market data stale", message="BTCUSDT candles stopped. Second sentence stays in the app.", severity="CRITICAL")
    text = render(ev, PREFS)
    assert text.startswith("🚨 <b>Market data stale</b>") and "Second sentence" not in text


# ----------------------------------------------------------------------- safety of the text itself

def test_dynamic_content_is_escaped():
    row = signal_row(decision={"outcome": "REJECTED", "mode": "shadow", "reason": "<script>&</script>"})
    row["symbol"] = "A<B&USDT"
    text = render(signal_qualified_event(row, "x", NOW), PREFS)
    assert "<script>" not in text and "&lt;script&gt;" in text and "A&lt;B&amp;USDT" in text and valid_html(text)
    note = render(watchlist_digest_event([{"symbol": "X<USDT", "timeframe": "15m", "direction": "LONG", "why": "", "brief": "<b>"}], NOW, NOW), PREFS)
    assert "&lt;b&gt;" in note and valid_html(note)


def test_a_price_alert_is_compact_and_escaped():
    text = format_price_alert({"symbol": "BTCUSDT", "condition": "below", "price": 60000.0, "note": "<b>x</b>"}, 59990.0, NOW)
    assert "PRICE ALERT" in text and "at or below <b>$60,000.00</b>" in text and "&lt;b&gt;" in text and "not a trade signal" in text and valid_html(text)


@pytest.mark.parametrize("text", ["<b>head</b>\n" + "line &amp; more\n" * 900, "x &amp; " * 2000, "<b>" + "y" * 5000 + "</b>"])
def test_over_long_text_is_cut_without_breaking_tags_or_entities(text):
    out = fit(text)
    assert len(out) <= MAX_LENGTH and valid_html(out)


def test_every_compact_message_is_within_the_limit_even_with_a_huge_reason():
    huge = "word " * 3000
    assert len(signal_text(decision={"outcome": "REJECTED", "mode": "shadow", "reason": huge})) < 800
    ev = EventIn(event_id="x", event_type="VALIDATION_DECISION", title="T", message=huge[:3900])
    assert len(render(ev, PREFS)) < 300


# ----------------------------------------------------------------------- preferences and routing

def test_old_stored_preferences_keep_their_values_and_gain_the_new_defaults():
    old = {"min_severity": "WARNING", "telegram_events": {"PAPER_ENTRY_FILLED": True}, "symbols": ["BTCUSDT"], "strategies": ["C"], "timezone": "UTC", "muted_events": []}
    p = NotificationSettings.model_validate(old)
    assert p.min_severity == "WARNING" and p.telegram_events == {"PAPER_ENTRY_FILLED": True} and p.symbols == ["BTCUSDT"] and p.strategies == ["C"] and p.timezone == "UTC" and p.muted_events == []
    assert p.telegram_format == "compact" and p.watchlist_15m_first is True and p.watchlist_interval_minutes == 15 and p.dashboard_url is None


@pytest.mark.parametrize(("event", "etype"), [("watchlist", "WATCHLIST_DIGEST"), ("qualified", "SIGNAL_QUALIFIED"), ("entry", "PAPER_ENTRY_FILLED"), ("tp", "PAPER_TP2_HIT"), ("sl", "PAPER_STOP_LOSS_HIT")])
def test_each_alert_type_can_be_switched_off(event, etype):
    ev = EventIn(event_id="x", event_type=etype, title="t", symbol="BTCUSDT")
    assert route(ev, NotificationSettings(telegram_events={etype: True}), True)[1] is True
    assert route(ev, NotificationSettings(telegram_events={etype: False}), True)[1] is False


def test_risk_alerts_off_stops_telegram_but_the_event_is_still_recorded():
    ev = breaker_event("C", "C@v", "2", "max_drawdown", {"value_pct": 11, "limit_pct": 10}, NOW, "g", [])
    prefs = NotificationSettings()
    assert route(ev, prefs, True)[1] is True
    prefs.categories["risk"].telegram = False
    muted, send = route(ev, prefs, True)
    assert send is False and muted is False


def test_strategy_and_symbol_selection_filter_telegram():
    ev = EventIn(event_id="x", event_type="PAPER_STOP_LOSS_HIT", title="t", symbol="BTCUSDT", strategy_id="B")
    assert route(ev, NotificationSettings(strategies=["C"]), True)[1] is False
    assert route(ev, NotificationSettings(symbols=["ETHUSDT"]), True)[1] is False
    assert route(ev, NotificationSettings(strategies=["B"], symbols=["BTCUSDT"]), True)[1] is True


def test_interval_has_sensible_limits():
    for bad in (1, 1000):
        with pytest.raises(ValidationError):
            NotificationSettings(watchlist_interval_minutes=bad)


# ----------------------------------------------------------------------- delivered from stored events

class Telegram:
    def __init__(self):
        self.messages = []

    async def send_message(self, text):
        from app.alerts.telegram import SendResult
        self.messages.append(text)
        return SendResult(True, 1)


def center(tg, **prefs):
    from app.db.session import session_factory
    s = RuntimeSettings()
    s.notifications = NotificationSettings(**prefs)
    return NotificationService(session_factory, SimpleNamespace(broadcast=_noop), SimpleNamespace(current=s), tg)


async def _noop(*_):
    return None


async def stored(model):
    from sqlalchemy import select

    from app.db.session import session_factory
    async with session_factory()() as s:
        return (await s.execute(select(model))).scalars().all()


async def test_the_message_comes_from_the_stored_event(db):
    tg = Telegram()
    c = center(tg)
    await c.emit(paper_exit_event(closed("STOP", -10.8, -1.08)))
    await c.deliver_pending()
    e = (await stored(NotificationEvent))[0]
    assert e.payload["net_pnl"] == -10.8 and e.payload["net_r"] == -1.08
    assert len(tg.messages) == 1 and "-$10.80" in tg.messages[0] and "-1.08R" in tg.messages[0]
    assert (await stored(NotificationDelivery))[0].message == tg.messages[0]


async def test_the_same_event_is_delivered_once(db):
    tg = Telegram()
    c = center(tg)
    for _ in range(3):
        await c.emit(paper_exit_event(closed("STOP", -10.8, -1.08)))
    await c.deliver_pending()
    assert len(tg.messages) == 1 and len(await stored(NotificationEvent)) == 1


async def test_distinct_outcomes_and_strategies_are_never_merged(db):
    tg = Telegram()
    c = center(tg)
    await c.emit(paper_level_event(trade(), {"index": 1, "price": 1.0}, 1.0, NOW))
    await c.emit(paper_level_event(trade(), {"index": 2, "price": 2.0}, 2.0, NOW))
    await c.emit(paper_exit_event(closed("TARGET", 5.0, 0.5), 3))
    await c.emit(paper_exit_event(closed("STOP", -5.0, -0.5, id="t2", variant_key="B", variant_id="B@2.0.0+x~y")))
    await c.deliver_pending()
    assert len(tg.messages) == 4 and "Strategy B" in tg.messages[3] and all("Strategy C" in m for m in tg.messages[:3])


async def test_critical_events_are_recorded_when_telegram_is_switched_off(db):
    tg = Telegram()
    c = center(tg)
    c.prefs.categories["risk"].telegram = False
    await c.emit(breaker_event("C", "C@v", "2", "max_drawdown", {"value_pct": 11, "limit_pct": 10}, NOW, "g", []))
    await c.deliver_pending()
    rows = await stored(NotificationEvent)
    assert tg.messages == [] and len(rows) == 1 and rows[0].severity == "CRITICAL" and rows[0].muted is False


# ----------------------------------------------------------------------- watchlist rate limit

class Center:
    def __init__(self, interval=15):
        self.prefs = NotificationSettings(watchlist_interval_minutes=interval)
        self.events = []

    async def emit(self, ev):
        self.events.append(ev)


def scanner(c=None):
    from app.db.session import session_factory
    s = Scanner.__new__(Scanner)
    s._session_factory = session_factory
    s.center = c or Center()
    s._watch_new, s._watch_sent, s._watching = [], None, {}
    return s


def watch(s, symbol="BTCUSDT", tf="15m", direction="LONG", still=True):
    s._watch_new.append({"symbol": symbol, "timeframe": tf, "direction": direction, "strategy": "breakout", "why": "x", "brief": "Awaiting retest"})
    if still:
        s._watching[(symbol, tf, direction, "breakout")] = "t"


async def test_candidates_within_the_interval_wait_and_arrive_together(db):
    s = scanner()
    watch(s)
    await s._send_watch_digest(NOW)
    assert len(s.center.events) == 1
    watch(s, "ETHUSDT")
    await s._send_watch_digest(NOW + timedelta(minutes=5))
    watch(s, "SOLUSDT")
    await s._send_watch_digest(NOW + timedelta(minutes=10))
    assert len(s.center.events) == 1
    await s._send_watch_digest(NOW + timedelta(minutes=15, seconds=-2))  # scans land just either side of the close
    assert len(s.center.events) == 2 and {x["symbol"] for x in s.center.events[1].payload["items"]} == {"ETHUSDT", "SOLUSDT"}


async def test_a_longer_interval_is_respected(db):
    s = scanner(Center(interval=60))
    watch(s)
    await s._send_watch_digest(NOW)
    watch(s, "ETHUSDT")
    await s._send_watch_digest(NOW + timedelta(minutes=30))
    assert len(s.center.events) == 1
    await s._send_watch_digest(NOW + timedelta(minutes=60))
    assert len(s.center.events) == 2


async def test_a_candidate_that_stopped_being_one_is_not_announced(db):
    s = scanner()
    watch(s)
    await s._send_watch_digest(NOW)
    watch(s, "ETHUSDT", still=False)
    await s._send_watch_digest(NOW + timedelta(minutes=15))
    assert len(s.center.events) == 1


async def test_the_same_candidate_isnt_repeated_within_its_cooldown(db):
    s = scanner()
    watch(s)
    await s._send_watch_digest(NOW)
    watch(s)
    await s._send_watch_digest(NOW + timedelta(minutes=15))
    assert len(s.center.events) == 1
    watch(s)
    await s._send_watch_digest(NOW + timedelta(hours=2))  # 8 of its 15m candles later
    assert len(s.center.events) == 2


async def test_no_message_when_nothing_is_new(db):
    s = scanner()
    await s._send_watch_digest(NOW)
    assert s.center.events == []


# ----------------------------------------------------------------------- nothing else moved

def test_the_formatter_decides_nothing():
    """Only reads events: it imports no strategy, risk, paper-trading or database module."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "notifications" / "telegram_format.py").read_text(encoding="utf-8"))
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not [m for m in modules if m and m.startswith(("app.strategy", "app.forward", "app.risk", "app.signals", "app.db", "app.planning"))]


def test_notification_preferences_cannot_change_strategy_versions():
    a, b = RuntimeSettings(), RuntimeSettings()
    b.notifications = NotificationSettings(telegram_format="detailed", watchlist_interval_minutes=60, dashboard_url="https://app.example.com")
    assert a.strategy.version == b.strategy.version
