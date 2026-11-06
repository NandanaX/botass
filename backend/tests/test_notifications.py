"""Phase 7 notification center: creation, idempotency, signal and paper-trade lifecycle events,
partial take-profits, grouping, shadow vs enforcement labels, read state, Telegram delivery,
reconnect recovery, risk and system incidents, preferences and access boundaries."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.alerts.telegram import SendResult
from app.db.models import Notification, NotificationDelivery, NotificationEvent, NotificationIncident
from app.notifications.events import EventIn, route
from app.notifications.messages import decision_view, paper_exit_event, paper_level_event, telegram_message
from app.notifications.settings import CategoryPrefs, NotificationSettings
from app.settings_store import RuntimeSettings
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from app.timeutil import utcnow
from tests.conftest import make_candles, phase1_params

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)


class Store:
    def __init__(self, s=None):
        self.current = s or RuntimeSettings()


class Hub:
    def __init__(self):
        self.sent = []

    async def broadcast(self, event, data):
        self.sent.append((event, data))


class Telegram:
    def __init__(self, ok=True):
        self.ok = ok
        self.messages = []

    async def send_message(self, text):
        self.messages.append(text)
        return SendResult(self.ok, 1, None if self.ok else "HTTP 500: boom")


def center(telegram=None, s=None):
    from app.db.session import session_factory
    from app.notifications.service import NotificationService
    return NotificationService(session_factory, Hub(), Store(s), telegram)


def ev(event_id="e1", event_type="SIGNAL_REJECTED", **kw):
    return EventIn(event_id=event_id, event_type=event_type, title=kw.pop("title", "A title"), **kw)


async def rows(model=NotificationEvent):
    from app.db.session import session_factory
    async with session_factory()() as session:
        return (await session.execute(select(model))).scalars().all()


# ----------------------------------------------------------------------------- creation and idempotency

async def test_creation_and_duplicate_prevention_ignore_the_title(db):
    c = center()
    first = await c.emit(ev("same", title="First title"))
    assert first is not None
    assert await c.emit(ev("same", title="A different title")) is None  # same event: not stored again
    assert await c.emit(ev("other", title="First title")) is not None  # same title, different event: both kept
    stored = await rows()
    assert len(stored) == 2 and {r.title for r in stored} == {"First title"}
    assert stored[0].created_at.tzinfo is not None  # stored and returned in UTC


async def test_malformed_events_are_rejected_without_raising(db):
    c = center()
    assert await c.emit({"event_id": "x", "event_type": "NOT_A_TYPE", "title": "t"}) is None
    assert await c.emit({"event_id": "x", "event_type": "SIGNAL_REJECTED"}) is None  # no title
    assert await c.emit({"event_id": "x", "event_type": "PAPER_TRADE_CLOSED", "title": "t", "strategy_id": "Z"}) is None
    assert c.stats["malformed"] == 3 and await rows() == []


async def test_structured_payload_is_scrubbed_of_secrets():
    e = ev(payload={"ok": 1.5, "nan": float("nan"), "telegram_bot_token": "abc", "nested": [{"api_secret": "x", "keep": 2}]})
    assert e.payload == {"ok": 1.5, "nan": None, "nested": [{"keep": 2}]}


async def test_store_failure_is_buffered_and_retried(db):
    c = center()

    class Broken:
        def __call__(self):
            raise RuntimeError("database down")

    real = c._session_factory
    c._session_factory = lambda: Broken()()
    assert await c.emit(ev("buffered")) is None  # never raises into the caller
    assert c.stats["buffered"] == 1
    c._session_factory = real
    await c.flush_buffer()
    assert [r.event_id for r in await rows()] == ["buffered"] and c.stats["buffered"] == 0


# ----------------------------------------------------------------------------- shadow vs enforcement

def test_shadow_rejection_is_never_presented_as_enforced():
    v = decision_view({"outcome": "REJECTED", "mode": "shadow", "reason": "weak"})
    assert v["label"] == "Shadow decision: Would reject — signal was not blocked."
    assert v["enforced"] is False
    enforced = decision_view({"outcome": "REJECTED", "mode": "enforce", "reason": "weak"})
    assert enforced["enforced"] is True and "blocked" in enforced["label"] and "Shadow" not in enforced["label"]
    assert decision_view({"outcome": "QUALIFIED", "mode": "enforce"})["enforced"] is False
    assert decision_view(None) is None


# ----------------------------------------------------------------------------- signal lifecycle

def _signal_frame():
    p = phase1_params(mtf={"enabled": False})
    df = make_candles(1200, seed=3, drift=0.0008)
    f = compute_features(df, "15m", p)
    for i in range(p.warmup_bars, len(f) - 40):
        e = evaluate_at(f, i, p, {}, context=False)
        if e.setup is not None:
            f2 = compute_features(df.iloc[: i + 1], "15m", p)
            return p, df, f2, i, evaluate_at(f2, i, p, {}, context=False)
    raise AssertionError("no setup")


def signal_service(c, telegram):
    from app.config import Settings
    from app.db.session import session_factory
    from app.signals.service import SignalService
    svc = SignalService(session_factory, c.hub, Settings(_env_file=None), telegram)
    svc.center, svc.settings = c, c.settings
    return svc


async def test_qualified_signal_notifies_once_with_shadow_label_and_one_telegram(db):
    p, _, f, i, e = _signal_frame()
    tg = Telegram()
    s = RuntimeSettings(strategy=p)
    c = center(tg, s)
    svc = signal_service(c, tg)
    now = datetime.fromisoformat(e.close_time) + timedelta(seconds=5)
    decision = {"outcome": "REJECTED", "mode": "shadow", "reason": "low quality", "checks": []}
    created = await svc.handle_setup("bybit", "TESTUSDT", e, s, now, decision)
    assert created["notification_status"] == "SENT"
    assert await svc.handle_setup("bybit", "TESTUSDT", e, s, now, decision) is None
    stored = await rows()
    assert [r.event_type for r in stored] == ["SIGNAL_QUALIFIED"] and len(tg.messages) == 1
    n = stored[0]
    p_ = n.payload
    assert p_["direction"] == e.setup.direction and p_["baseline_status"] == "QUALIFIED"
    assert p_["entry_reference"] == e.setup.entry_reference and p_["stop_loss"] == e.setup.stop_loss
    assert p_["take_profit"] == e.setup.take_profit and p_["reasoning"]
    assert p_["decision"]["enforced"] is False
    assert "Shadow decision: Would reject — signal was not blocked." in n.message
    assert "signal was not blocked" in tg.messages[0]
    assert n.delivery_status == "TELEGRAM_SENT"
    # the outbox worker finds nothing further to send
    assert await c.deliver_pending(utcnow() + timedelta(hours=1)) == 0 and len(tg.messages) == 1
    # the existing delivery log keeps working
    assert [(x.kind, x.status) for x in await rows(Notification)] == [("signal", "SENT")]
    assert [x[1]["event"]["id"] for x in c.hub.sent if x[0] == "notification"] == [n.id]  # announced once


async def test_outbox_sends_a_signal_alert_if_the_process_died_before_sending(db):
    _, _, _, _, e = _signal_frame()
    tg = Telegram()
    c = center(tg)
    svc = signal_service(c, tg)
    from app.signals.service import build_signal_row
    row = build_signal_row("bybit", "TESTUSDT", e)
    await svc.persist(row)  # the crash happens here: committed, but never sent
    assert tg.messages == []
    # not yet: the signal service gets the first chance, so the two can never both send
    assert await c.deliver_pending(utcnow()) == 0
    d = (await rows(NotificationDelivery))[0]
    from app.db.session import session_factory
    async with session_factory()() as session:
        (await session.get(NotificationDelivery, d.id)).not_after = None
        await session.commit()
    await c.deliver_pending(utcnow() + timedelta(minutes=5))
    assert len(tg.messages) == 1


async def test_enforce_mode_is_labelled_and_blocks_only_the_alert(db):
    p, _, _, _, e = _signal_frame()
    tg = Telegram()
    s = RuntimeSettings(strategy=p)
    c = center(tg, s)
    svc = signal_service(c, tg)
    now = datetime.fromisoformat(e.close_time)
    created = await svc.handle_setup("bybit", "TESTUSDT", e, s, now, {"outcome": "REJECTED", "mode": "enforce", "reason": "x"})
    assert created["notification_status"] == "BLOCKED_BY_DECISION" and tg.messages == []
    n = (await rows())[0]
    assert "Enforced decision" in n.message and n.delivery_status.startswith("TELEGRAM_BLOCKED")
    assert (await rows(NotificationDelivery))[0].status == "SUPPRESSED"
    assert await c.deliver_pending(utcnow() + timedelta(hours=1)) == 0


async def test_expired_and_invalidated_signals_notify(db):
    from app.notifications.messages import signal_outcome_event

    class Sig:
        id = "s" * 64
        symbol, timeframe, direction, strategy_type = "BTCUSDT", "1h", "LONG", "breakout"
        candle_open_time, candle_close_time = T0, T0 + timedelta(hours=1)
        entry_reference, stop_loss, take_profit, score = 100.0, 98.0, 104.0, 70
        explanation, strategy_version = "because", "2.0.0"
        plan = {"decision": {"outcome": "REJECTED", "mode": "shadow", "reason": "x"}}

    c = center()
    await c.emit(signal_outcome_event(Sig, "EXPIRED", None))
    await c.emit(signal_outcome_event(Sig, "INVALIDATED", "next candle opened beyond the stop"))
    types = {r.event_type: r for r in await rows()}
    assert set(types) == {"SIGNAL_EXPIRED", "SIGNAL_INVALIDATED"}
    assert "next candle opened beyond the stop" in types["SIGNAL_INVALIDATED"].message
    assert "not blocked" in types["SIGNAL_EXPIRED"].message


# ----------------------------------------------------------------------------- paper trades

class _State:
    view = {}

    def ticker_view(self, symbol):
        return self.view


class _Market:
    exchange = "bybit"

    def __init__(self):
        self.state = _State()


async def _forward(tg=None):
    from app.db.session import session_factory
    from app.forward.service import ForwardService
    p, _, f, i, e = _signal_frame()
    s = RuntimeSettings(strategy=p)
    c = center(tg, s)
    m = _Market()
    px = float(f.close[i])
    now = utcnow()
    m.state.view = {"available": True, "bid_price": px * 0.9999, "ask_price": px * 1.0001, "last_price": px,
                    "quote_at": now.isoformat(), "funding_rate": 0.0001,
                    "next_funding_time": (now + timedelta(hours=4)).isoformat()}
    svc = ForwardService(m, Store(s), session_factory)
    svc.center = c
    close = datetime.fromtimestamp(int(f.close_time[i]) / 1e9, UTC) + timedelta(seconds=5)
    await svc.on_evaluation("TESTUSDT", "15m", f, e, True, close, {}, None, None)
    return svc, c, f, e, m, close


async def test_paper_entries_identify_each_strategy_and_group_one_summary(db):
    svc, c, f, e, _, close = await _forward()
    stored = await rows()
    by = {(r.event_type, r.strategy_id) for r in stored}
    assert ("PAPER_ENTRY_FILLED", "A") in by and ("PAPER_ENTRY_FILLED", "B") in by
    assert ("SIGNAL_REJECTED", "C") in by  # no higher timeframe in this test: C's own rule rejects
    summary = next(r for r in stored if r.event_type == "STRATEGY_EVALUATION")
    assert "Strategy A: Accepted" in summary.message and "Strategy C: Rejected" in summary.message
    assert summary.payload["label"] == "PAPER TRADING" and "pnl" not in json.dumps(summary.payload).lower()
    a = next(r for r in stored if r.event_type == "PAPER_ENTRY_FILLED" and r.strategy_id == "A")
    assert a.title.startswith("PAPER TRADING") and a.payload["fill_price"] and a.payload["quantity"]
    assert a.payload["reference_entry"] == e.setup.entry_reference
    assert a.signal_id and a.paper_trade_id
    # re-evaluating the candle creates nothing new
    n = len(stored)
    await svc.on_evaluation("TESTUSDT", "15m", f, e, True, close, {}, None, None)
    assert len(await rows()) == n


async def test_stop_loss_creates_one_result_per_strategy_and_no_duplicate_close(db):
    tg = Telegram()
    svc, c, _, e, m, close = await _forward(tg)
    long = e.setup.direction == "LONG"
    for tid, t in list(svc.open.items()):
        hit = t["stop_loss"] * (0.999 if long else 1.001)
        await svc._close(tid, "STOP", hit, close + timedelta(hours=2), "ticker_quote")
    stored = await rows()
    stops = [r for r in stored if r.event_type == "PAPER_STOP_LOSS_HIT"]
    assert {r.strategy_id for r in stops} == {"A", "B"}
    assert not [r for r in stored if r.event_type == "PAPER_TRADE_CLOSED"]
    for s in stops:
        p = s.payload
        assert p["label"] == "PAPER TRADING" and p["net_r"] < 0 and p["exit_price"] and p["net_pnl"] < 0
        assert p["entry_fee"] > 0 and p["exit_fee"] > 0 and p["slippage_cost"] > 0
        assert p["duration"] == "2h 0m" and p["remaining_quantity"] == 0.0
        assert "Realized simulated P&L" in s.message and "Funding" in s.message and "Net -" in s.message
    assert stops[0].payload["net_pnl"] != stops[1].payload["net_pnl"]  # results are not merged
    await c.deliver_pending()
    assert len(tg.messages) == 2 and all("PAPER TRADING" in x and "no real money" in x.lower() for x in tg.messages)


async def test_target_exit_maps_to_the_take_profit_event(db):
    svc, _, _, _, _, close = await _forward()
    tid, t = next((k, v) for k, v in svc.open.items() if v["variant_key"] == "B")
    await svc._close(tid, "TARGET", t["take_profit"], close + timedelta(hours=1), "ticker_quote")
    hit = next(r for r in await rows() if r.paper_trade_id == tid and r.event_type.startswith("PAPER_TP"))
    assert hit.event_type == "PAPER_TP1_HIT" and hit.payload["net_r"] > 0 and hit.severity == "SUCCESS"
    assert hit.payload["remaining_quantity"] == 0.0


def _trade(**kw):
    return {"id": "t" * 40, "variant_key": "A", "symbol": "BTCUSDT", "timeframe": "1h", "direction": "LONG", "status": "OPEN",
            "reference_entry": 100.0, "fill_price": 100.1, "fill_time": T0, "stop_loss": 98.0, "take_profit": 106.0,
            "quantity": 2.0, "entry_fee": 0.11, "exit_fee": 0.04, "slippage_cost": 0.05, "spread_cost": 0.04,
            "funding_cost": -0.02, "portfolio_taken": True, "cost_flags": {"funding": "exchange_funding_history"}, "data": {}, **kw}


def test_partial_take_profit_reports_realized_and_remaining_exposure():
    level = {"index": 1, "price": 102.0, "closes_trade": False}
    none = paper_level_event(_trade(), level, 102.1, T0)
    assert none.event_type == "PAPER_TP1_HIT"
    assert none.payload["realized_pnl"] == 0.0 and none.payload["remaining_quantity"] == 2.0
    assert "stays open at full size" in none.message and "Remaining position 2.0000" in none.message
    partial = paper_level_event(_trade(), level, 102.1, T0, realized_pnl=1.9, remaining_quantity=1.0)
    assert "Partial exit taken" in partial.message
    assert "Realized simulated P&L $1.90" in partial.message and "Remaining position 1.0000" in partial.message


async def test_level_below_the_exit_target_notifies_once_and_keeps_the_trade_open(db):
    svc, c, _, e, m, close = await _forward()
    tid, t = next((k, v) for k, v in svc.open.items() if v["variant_key"] == "A")
    long = e.setup.direction == "LONG"
    entry = t["fill_price"]
    level = entry + (t["take_profit"] - entry) / 2
    t["data"]["tp_levels"] = [{"index": 1, "price": level, "closes_trade": False},
                              {"index": 2, "price": t["take_profit"], "closes_trade": True}]
    q = level * (1.0002 if long else 0.9998)
    view = {**m.state.view, "bid_price": q, "ask_price": q, "last_price": q, "quote_at": utcnow().isoformat()}
    t["fill_time"] = utcnow() - timedelta(minutes=5)
    await svc.on_ticker("TESTUSDT", view)
    await svc.on_ticker("TESTUSDT", view)
    tp = [r for r in await rows() if r.event_type == "PAPER_TP1_HIT" and r.strategy_id == "A"]
    assert len(tp) == 1 and tp[0].payload["closes_trade"] is False and tid in svc.open
    # the target closes it, as TP2
    await svc._close(tid, "TARGET", t["take_profit"], close + timedelta(hours=3), "ticker_quote")
    assert [r for r in await rows() if r.event_type == "PAPER_TP2_HIT" and r.paper_trade_id == tid]


async def test_trade_not_taken_by_the_paper_portfolio_stays_in_app_only(db):
    tg = Telegram()
    c = center(tg)
    closed = _trade(portfolio_taken=False, portfolio_reason="correlated exposure", exit_reason="STOP", exit_time=T0 + timedelta(hours=1),
                    exit_price=97.9, net_pnl=-4.5, net_r=-1.1, status="CLOSED")
    await c.emit(paper_exit_event(closed))
    assert await rows(NotificationDelivery) == []
    assert "not taken" in (await rows())[0].message and "Signal-level record only" in (await rows())[0].message


# ----------------------------------------------------------------------------- grouping and read state

async def test_group_is_one_row_but_individual_records_stay_available(db):
    _, c, _, _, _, _ = await _forward()
    collapsed = await c.list_events("local")
    assert [x["event_type"] for x in collapsed["items"]] == ["STRATEGY_EVALUATION"]
    assert collapsed["unread"]["total"] == 1
    summary = collapsed["items"][0]
    detail = await c.get_event("local", summary["id"])
    assert {m["strategy_id"] for m in detail["members"]} == {"A", "B", "C"}
    # filtering by strategy returns that strategy's own records, never the merged view
    only_b = await c.list_events("local", strategy="B")
    assert {x["strategy_id"] for x in only_b["items"]} == {"B"} and only_b["total"] >= 1
    # reading the group covers its members
    await c.mark_read("local", summary["id"])
    assert (await c.unread_count())["total"] == 0
    assert (await c.list_events("local", strategy="B", read="unread"))["total"] == 0
    # with grouping off, the same records appear individually
    c.settings.current.notifications.grouping.enabled = False
    flat = await c.list_events("local")
    assert "STRATEGY_EVALUATION" not in {x["event_type"] for x in flat["items"]} and flat["total"] >= 3


def _qualified(i, symbol, direction="SHORT"):
    return EventIn(event_id=f"q{i}", event_type="SIGNAL_QUALIFIED", title=f"{symbol} qualified", symbol=symbol,
                   timeframe="1h", payload={"direction": direction}, telegram=False)


async def test_correlated_burst_is_summarised_but_unrelated_signals_are_not(db):
    c = center()
    for k, s in enumerate(["DOGEUSDT", "AVAXUSDT", "SUIUSDT", "LINKUSDT", "SOLUSDT"]):
        await c.emit(_qualified(k, s))
    await c.emit(_qualified(9, "XMRUSDT"))  # same direction and time, but not correlated
    await c.emit(_qualified(10, "ADAUSDT", "LONG"))  # correlated coin, opposite direction

    def corr(a, b):
        return 0.1 if "XMRUSDT" in (a, b) else float("nan") if "ADAUSDT" in (a, b) else 0.9

    await c.group_correlated(corr, 0.7)
    stored = await rows()
    summary = [r for r in stored if r.event_type == "MARKET_SIGNAL_GROUP"]
    assert len(summary) == 1
    assert summary[0].title == "5 correlated SHORT candidates detected across AVAX, DOGE, LINK and other assets"
    grouped = {r.symbol for r in stored if r.group_key == summary[0].group_key and not r.is_group_summary}
    assert grouped == {"DOGEUSDT", "AVAXUSDT", "SUIUSDT", "LINKUSDT", "SOLUSDT"}
    assert not any(r.group_key for r in stored if r.symbol in ("XMRUSDT", "ADAUSDT"))
    # individual records stay reachable, and the group is not rebuilt on later cycles
    assert (await c.list_events("local", category="signal"))["total"] == 7
    await c.group_correlated(corr, 0.7)
    assert len([r for r in await rows() if r.event_type == "MARKET_SIGNAL_GROUP"]) == 1
    assert len(await rows()) == 8


async def test_two_correlated_signals_stay_below_the_group_minimum(db):
    c = center()
    await c.emit(_qualified(1, "DOGEUSDT"))
    await c.emit(_qualified(2, "AVAXUSDT"))
    await c.group_correlated(lambda a, b: 0.95, 0.7)
    assert not [r for r in await rows() if r.event_type == "MARKET_SIGNAL_GROUP"]


async def test_read_state_updates_counts_and_is_broadcast_for_other_tabs(db):
    c = center()
    a = await c.emit(ev("a"))
    await c.emit(ev("b"))
    assert (await c.unread_count())["total"] == 2
    done = await c.mark_read("local", a)
    assert done["unread"]["total"] == 1
    assert ("notification_read", {"ids": [a], "read": True, "unread": done["unread"]}) in c.hub.sent
    await c.mark_read("local", a, read=False)
    assert (await c.unread_count())["total"] == 2
    res = await c.mark_all_read("local")
    assert res["unread"]["total"] == 0 and c.hub.sent[-1][0] == "notification_read"


# ----------------------------------------------------------------------------- Telegram delivery

async def test_telegram_failure_retries_without_new_events_and_tracks_one_incident(db):
    tg = Telegram(ok=False)
    c = center(tg)
    await c.emit(ev("sl", "PAPER_STOP_LOSS_HIT", paper_trade_id="t1", payload={"portfolio_taken": True}))
    t = utcnow()
    await c.deliver_pending(t)
    d = (await rows(NotificationDelivery))[0]
    assert d.status == "RETRY" and d.attempts == 1 and "boom" in d.last_error
    assert await c.deliver_pending(t + timedelta(seconds=10)) == 0  # backoff respected
    for mins in (2, 8, 30):
        await c.deliver_pending(t + timedelta(minutes=mins))
    d = (await rows(NotificationDelivery))[0]
    assert d.status == "RETRY" and d.attempts == 4 and len(tg.messages) == 4  # still trying, not given up
    assert await c.deliver_pending(t + timedelta(minutes=31)) == 0
    # one event for the trade, one incident however many retries failed
    stored = await rows()
    assert len([r for r in stored if r.event_type == "PAPER_STOP_LOSS_HIT"]) == 1
    assert [r.event_type for r in stored if r.category == "system"] == ["INCIDENT_OPENED"]
    assert len(await rows(NotificationDelivery)) == 1  # the failure alert itself is not sent to Telegram
    inc = (await rows(NotificationIncident))[0]
    assert inc.status == "OPEN" and inc.occurrences == 4
    # the next successful delivery closes the incident
    tg.ok = True
    await c.emit(ev("sl2", "PAPER_STOP_LOSS_HIT", paper_trade_id="t2", payload={"portfolio_taken": True}))
    await c.deliver_pending()
    assert (await rows(NotificationIncident))[0].status == "RESOLVED"
    assert "INCIDENT_RESOLVED" in {r.event_type for r in await rows()}


async def test_a_delivery_is_never_claimed_twice_and_expires(db):
    tg = Telegram()
    c = center(tg)
    await c.emit(ev("x", "PAPER_TP1_HIT", paper_trade_id="t", payload={"portfolio_taken": True}))
    did = (await rows(NotificationDelivery))[0].id
    await c._deliver(did)
    assert await c._deliver(did) is None and len(tg.messages) == 1
    await c.emit(ev("old", "PAPER_TP1_HIT", paper_trade_id="t2", payload={"portfolio_taken": True},
                    telegram_not_after=utcnow() - timedelta(minutes=1)))
    await c.deliver_pending()
    late = [d for d in await rows(NotificationDelivery) if d.id != did][0]
    assert late.status == "EXPIRED" and len(tg.messages) == 1


async def test_telegram_exception_does_not_escape(db):
    class Exploding:
        async def send_message(self, text):
            raise RuntimeError("network")

    c = center(Exploding())
    await c.emit(ev("x", "PAPER_TP1_HIT", paper_trade_id="t", payload={"portfolio_taken": True}))
    await c.deliver_pending()  # must not raise
    assert (await rows(NotificationDelivery))[0].status == "RETRY"


def test_telegram_text_is_escaped_and_labelled():
    e = ev("x", "PAPER_STOP_LOSS_HIT", title="<b>LINK</b>", message="a & b", paper_trade_id="t")
    text = telegram_message(e, "Asia/Colombo")
    assert "&lt;b&gt;LINK" in text and "a &amp; b" in text and "PAPER TRADING" in text and "Asia/Colombo" in text


# ----------------------------------------------------------------------------- risk events

async def test_paper_breaker_fires_once_per_trip_and_names_the_strategy(db):
    svc, c, _, _, _, close = await _forward()
    from app.db.models import PaperTrade
    from app.db.session import session_factory
    tid, t = next((k, v) for k, v in svc.open.items() if v["variant_key"] == "B")
    async with session_factory()() as session:  # a real loss big enough to trip both breakers
        row = await session.get(PaperTrade, tid)
        row.status, row.exit_time, row.net_pnl, row.portfolio_taken = "CLOSED", close, -1500.0, True
        await session.commit()
    svc.open.pop(tid)
    await svc._notify_breakers("B", close)
    await svc._notify_breakers("B", close)
    risk = [r for r in await rows() if r.category == "risk"]
    assert sorted(r.event_type for r in risk) == ["RISK_DAILY_LOSS_LIMIT", "RISK_DRAWDOWN_LIMIT"]
    for r in risk:
        assert r.severity == "CRITICAL" and r.strategy_id == "B" and "Strategy B" in r.title and "paper" in r.title.lower()
        assert "no real trades are affected" in r.message and r.payload["threshold"]["limit_pct"]
        assert isinstance(r.payload["affected_positions"], list)
    # a manual reset is announced, and a new trip after it is a new event
    await svc.reset_drawdown("B", "reviewed the losses", "tester")
    assert "CIRCUIT_BREAKER_RESET" in {r.event_type for r in await rows()}
    await svc._notify_breakers("B", close)  # the peak restarted at the reset, so it is not tripped again
    assert len([r for r in await rows() if r.event_type == "RISK_DRAWDOWN_LIMIT"]) == 1


async def test_portfolio_rejection_events_are_scoped_to_the_paper_account(db):
    from app.notifications.messages import risk_entry_event
    t = _trade(portfolio_taken=False, portfolio_reason="correlated exposure: already SHORT in ETHUSDT (0.91)", detected_at=T0)
    corr = risk_entry_event(t, "correlated", None)
    assert corr.event_type == "RISK_CORRELATED_REJECTED" and "no real trades" in corr.message
    assert risk_entry_event(t, "open_risk", None).event_type == "RISK_MAX_OPEN_RISK"
    assert risk_entry_event(t, "data_integrity", None).event_type == "RISK_STALE_DATA"
    assert risk_entry_event(t, "symbol_open", None) is None  # routine, not an alert


# ----------------------------------------------------------------------------- system incidents

class _HealthState:
    def __init__(self, watch):
        self.watch = set(watch)
        self.ws_connected = True

        self.ws_last_message_at = None
        self.ws_reconnects = 0

    def ticker_view(self, s):
        return {"available": True}

    def is_stale(self, s, now=None):
        return s in self.watch


def monitor(c, stale=(), scanner=None, forward=None):
    from app.notifications.health import HealthMonitor

    class M:
        state = _HealthState(stale)

    class S:
        interval = 15.0
        status = scanner or {"is_leader": False}

    class E:
        market_stream_enabled = True
        scanner_enabled = True

    return HealthMonitor(c, M(), S(), forward, c.settings, E())


async def test_outage_is_one_incident_with_a_recovery_and_no_retry_spam(db):
    s = RuntimeSettings(watchlist=["BTCUSDT", "ETHUSDT"])
    c = center(s=s)
    h = monitor(c, stale=["BTCUSDT", "ETHUSDT"])
    t = T0
    await h.check(t)
    await h.check(t + timedelta(minutes=2))
    assert await rows() == []  # within the grace period: no noise
    for m in (3, 4, 5, 6):
        await h.check(t + timedelta(minutes=m))
    opened = [r for r in await rows() if r.event_type == "INCIDENT_OPENED"]
    assert len(opened) == 1 and opened[0].title == "Bybit market data unavailable"
    assert "unavailable for 3 minutes" in opened[0].message and opened[0].severity == "CRITICAL"
    assert not [r for r in await rows() if "stale" in r.title]  # one incident, not one per coin
    h.market.state.watch = set()
    await h.check(t + timedelta(minutes=7))
    resolved = [r for r in await rows() if r.event_type == "INCIDENT_RESOLVED"]
    assert len(resolved) == 1 and resolved[0].title == "Bybit market data connection restored"
    assert "restored after 7 minutes" in resolved[0].message
    history = await c.incidents()
    assert history[0]["status"] == "RESOLVED" and history[0]["occurrences"] == 4
    # a second outage is a new incident in the history
    h.market.state.watch = {"BTCUSDT", "ETHUSDT"}
    for m in (8, 9, 10, 11):
        await h.check(t + timedelta(minutes=m))
    assert len(await c.incidents()) == 2


async def test_critical_incident_reaches_telegram_on_open_and_recovery(db):
    tg = Telegram()
    s = RuntimeSettings(watchlist=["BTCUSDT"])
    c = center(tg, s)
    h = monitor(c, stale=["BTCUSDT"])
    for m in (0, 2, 4):
        await h.check(T0 + timedelta(minutes=m))
    h.market.state.watch = set()
    await h.check(T0 + timedelta(minutes=5))
    await c.deliver_pending()
    assert len(tg.messages) == 2 and "unavailable" in tg.messages[0] and "restored" in tg.messages[1]


async def test_scanner_and_paper_worker_failures_open_and_close_incidents(db):
    c = center(s=RuntimeSettings(watchlist=["BTCUSDT"]))

    class Forward:
        status = {"last_error": "disk full"}

    status = {"is_leader": True, "running": False, "last_cycle_at": T0.isoformat()}
    h = monitor(c, scanner=status, forward=Forward())
    await h.check(T0 + timedelta(minutes=1))
    titles = {r.title for r in await rows()}
    assert {"Background scanner stopped", "Paper-trading worker failure"} <= titles
    status.update(running=True, last_cycle_at=(T0 + timedelta(minutes=2)).isoformat())
    Forward.status = {"last_error": None}
    await h.check(T0 + timedelta(minutes=2))
    assert {"Background scanner running again", "Paper-trading worker recovered"} <= {r.title for r in await rows()}


async def test_database_failure_is_recorded_after_recovery(db):
    c = center(s=RuntimeSettings(watchlist=["BTCUSDT"]))
    h = monitor(c)
    real = c._session_factory

    def broken():
        raise RuntimeError("database locked")

    c._session_factory = lambda: broken
    await h.check(T0)  # must not raise
    c._session_factory = real
    await h.check(T0 + timedelta(minutes=2))
    inc = (await c.incidents())[0]
    assert inc["key"] == "database" and inc["status"] == "RESOLVED"
    assert inc["started_at"].startswith("2026-11-04T08:00")


async def test_open_incident_survives_a_restart_and_still_recovers(db):
    c = center(s=RuntimeSettings(watchlist=["BTCUSDT"]))
    await c.incident_open("bybit_websocket", "Bybit WebSocket disconnected", "down", "WARNING", T0)
    h = monitor(c)  # new process: conditions are healthy
    await h._restore()
    await h.check(T0 + timedelta(minutes=10))
    assert (await c.incidents())[0]["status"] == "RESOLVED"


# ----------------------------------------------------------------------------- preferences

def test_routing_defaults_match_the_requested_channels():
    p = NotificationSettings()
    tg = {t: route(ev("x", t, severity=sev), p, True)[1] for t, sev in [
        ("SIGNAL_QUALIFIED", None), ("PAPER_ENTRY_FILLED", None), ("PAPER_TP1_HIT", None), ("PAPER_STOP_LOSS_HIT", None),
        ("SIGNAL_REJECTED", None), ("RISK_DRAWDOWN_LIMIT", None), ("RISK_CORRELATED_REJECTED", None),
        ("INCIDENT_OPENED", "WARNING"), ("INCIDENT_ESCALATED", "CRITICAL")]}
    assert tg == {"SIGNAL_QUALIFIED": True, "PAPER_ENTRY_FILLED": False, "PAPER_TP1_HIT": True, "PAPER_STOP_LOSS_HIT": True,
                  "SIGNAL_REJECTED": False, "RISK_DRAWDOWN_LIMIT": True, "RISK_CORRELATED_REJECTED": False,
                  "INCIDENT_OPENED": False, "INCIDENT_ESCALATED": True}
    # optional Telegram for entries; nothing is ever sent while Telegram is off
    p2 = NotificationSettings(telegram_events={"PAPER_ENTRY_FILLED": True})
    assert route(ev("x", "PAPER_ENTRY_FILLED"), p2, True)[1] is True
    assert route(ev("x", "SIGNAL_QUALIFIED"), p, False)[1] is False


async def test_muted_categories_still_record_critical_incidents(db):
    prefs = NotificationSettings(in_app_enabled=False, categories={"system": CategoryPrefs(in_app=False, telegram=False)})
    s = RuntimeSettings(notifications=prefs)
    tg = Telegram()
    c = center(tg, s)
    await c.incident_open("bybit_market_data", "Bybit down", "down", "CRITICAL", T0)
    stored = await rows()
    assert len(stored) == 1 and stored[0].muted is True and stored[0].severity == "CRITICAL"
    assert (await c.unread_count())["total"] == 0 and (await c.list_events("local"))["total"] == 0
    assert (await c.list_events("local", include_muted=True))["total"] == 1  # still in the history
    assert await rows(NotificationDelivery) == []


async def test_filters_for_strategy_coin_timeframe_and_severity(db):
    prefs = NotificationSettings(strategies=["C"], symbols=["BTCUSDT"], timeframes=["1h"], min_severity="WARNING")
    tg = Telegram()
    c = center(tg, RuntimeSettings(notifications=prefs))
    base = {"paper_trade_id": "t", "payload": {"portfolio_taken": True}, "symbol": "BTCUSDT", "timeframe": "1h"}
    await c.emit(ev("shown", "PAPER_STOP_LOSS_HIT", strategy_id="C", **base))
    await c.emit(ev("other-strategy", "PAPER_STOP_LOSS_HIT", strategy_id="A", **base))
    await c.emit(ev("too-low", "PAPER_TP1_HIT", strategy_id="C", **base))  # SUCCESS < WARNING
    await c.emit(ev("other-coin", "PAPER_STOP_LOSS_HIT", strategy_id="C", **{**base, "symbol": "ETHUSDT"}))
    await c.emit(ev("other-tf", "PAPER_STOP_LOSS_HIT", strategy_id="C", **{**base, "timeframe": "4h"}))
    visible = (await c.list_events("local"))["items"]
    assert [x["event_id"] for x in visible] == ["shown"]
    assert len(await rows()) == 5  # all recorded
    await c.deliver_pending()
    assert len(tg.messages) == 1


def test_preferences_validate_their_values():
    with pytest.raises(ValueError):
        NotificationSettings(timezone="Mars/Olympus")
    with pytest.raises(ValueError):
        NotificationSettings(strategies=["E"])
    with pytest.raises(ValueError):
        NotificationSettings(retention_days=1)
    assert NotificationSettings().timezone == "Asia/Colombo"


def test_adding_notification_settings_does_not_change_locked_strategy_versions():
    from app.forward.variants import variant_config, variant_version
    base = RuntimeSettings()
    other = RuntimeSettings(notifications=NotificationSettings(sound=True, retention_days=30))
    for key in "ABC":
        a = variant_version(variant_config(key, base.strategy.version, base.validation))
        b = variant_version(variant_config(key, other.strategy.version, other.validation))
        assert a == b


async def test_retention_keeps_critical_events(db):
    c = center()
    await c.emit(ev("old-info"))
    await c.emit(ev("old-critical", "RISK_DRAWDOWN_LIMIT"))
    await c.emit(EventIn(event_id="tg", event_type="PAPER_TP1_HIT", title="t"))
    removed = await c.prune(utcnow() + timedelta(days=400))
    assert removed == 2 and [r.event_id for r in await rows()] == ["old-critical"]


# ----------------------------------------------------------------------------- API, recovery and access

@pytest.fixture
def api(tmp_path, monkeypatch):
    from app.config import Settings
    from app.container import build_container
    from app.db.models import Base
    from app.db.session import dispose_engine, get_engine, init_engine
    from app.main import create_app
    url = f"sqlite+aiosqlite:///{tmp_path / 'api.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    env = Settings(_env_file=None, database_url=url, scanner_enabled=False, market_stream_enabled=False)

    class Bybit:
        exchange = "bybit"

        async def close(self):
            pass

    init_engine(url)

    async def create():
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await dispose_engine()

    asyncio.run(create())

    def factory(env_, store, hub, sf):
        return build_container(env_, store, hub, sf, client=Bybit())  # type: ignore[arg-type]

    app = create_app(env, factory, run_background=False)
    with TestClient(app) as client:
        yield client


def _emit(client, *events):
    center_ = client.app.state.container.notifications
    return [client.portal.call(center_.emit, e) for e in events]


def test_api_filters_pagination_read_flow_and_preferences(api):
    ids = _emit(api, ev("a", "SIGNAL_REJECTED", symbol="BTCUSDT", timeframe="1h"),
                ev("b", "PAPER_STOP_LOSS_HIT", symbol="ETHUSDT", timeframe="4h", strategy_id="B"),
                ev("c", "RISK_DRAWDOWN_LIMIT", strategy_id="C"))
    body = api.get("/api/notification-center/events").json()
    assert body["total"] == 3 and body["unread"] == {"total": 3, "critical": 1}
    assert api.get("/api/notification-center/events", params={"category": "risk"}).json()["items"][0]["event_id"] == "c"
    assert api.get("/api/notification-center/events", params={"symbol": "ETHUSDT"}).json()["total"] == 1
    assert api.get("/api/notification-center/events", params={"strategy": "B"}).json()["total"] == 1
    assert api.get("/api/notification-center/events", params={"timeframe": "1h"}).json()["total"] == 1
    assert api.get("/api/notification-center/events", params={"severity": "CRITICAL"}).json()["total"] == 1
    page = api.get("/api/notification-center/events", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert api.get("/api/notification-center/events", params={"date_to": "2000-01-01T00:00:00Z"}).json()["total"] == 0
    assert api.get("/api/notification-center/events", params={"severity": "BAD"}).status_code == 422
    assert api.get("/api/notification-center/events", params={"symbol": "nope"}).status_code == 422
    # read flow
    r = api.post(f"/api/notification-center/events/{ids[0]}/read").json()
    assert r["unread"]["total"] == 2
    assert api.get("/api/notification-center/events", params={"read": "unread"}).json()["total"] == 2
    assert api.post("/api/notification-center/read-all").json()["unread"]["total"] == 0
    # details include links only to known pages
    d = api.get(f"/api/notification-center/events/{ids[1]}").json()
    assert d["links"] == [{"label": "Open chart", "href": "/market/ETHUSDT?tf=4h"},
                          {"label": "Validation Monitor", "href": "/validation"}]
    # preferences persist and reject bad values
    p = api.get("/api/notification-center/preferences").json()["preferences"]
    p["sound"], p["retention_days"] = True, 30
    assert api.put("/api/notification-center/preferences", json=p).json()["preferences"]["sound"] is True
    assert api.get("/api/settings").json()["settings"]["notifications"]["retention_days"] == 30
    assert api.put("/api/notification-center/preferences", json={**p, "timezone": "Nope/Nope"}).status_code == 422
    assert "token" not in json.dumps(api.get("/api/notification-center/preferences").json())


def test_access_is_scoped_to_the_recipient(api):
    mine, theirs = _emit(api, ev("mine"), ev("theirs", recipient="someone-else"))
    assert api.get(f"/api/notification-center/events/{mine}").status_code == 200
    assert api.get(f"/api/notification-center/events/{theirs}").status_code == 404
    assert api.post(f"/api/notification-center/events/{theirs}/read").status_code == 404
    body = api.get("/api/notification-center/events").json()
    assert [x["event_id"] for x in body["items"]] == ["mine"] and body["unread"]["total"] == 1
    # "mark all read" does not touch another recipient's events
    api.post("/api/notification-center/read-all")
    center_ = api.app.state.container.notifications
    assert api.portal.call(center_.unread_count, "someone-else")["total"] == 1


def test_websocket_announces_and_a_reconnect_recovers_missed_events(api):
    first = _emit(api, ev("before"))[0]
    with api.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["event"] == "hello" and hello["data"]["notifications"]["total"] == 1
        new = _emit(api, ev("live"))[0]
        msg = ws.receive_json()
        assert msg["event"] == "notification" and msg["data"]["event"]["id"] == new
        assert msg["data"]["unread"]["total"] == 2 and msg["ts"]
        api.post(f"/api/notification-center/events/{new}/read")
        read = ws.receive_json()
        assert read["event"] == "notification_read" and read["data"]["ids"] == [new]
    # disconnected: this event is not delivered live, but it is in the database
    missed = _emit(api, ev("while-away"))[0]
    with api.websocket_connect("/ws") as ws:
        assert ws.receive_json()["data"]["notifications"]["total"] == 2
    recovered = api.get("/api/notification-center/events", params={"after_id": new}).json()
    assert [x["id"] for x in recovered["items"]] == [missed] and first < new < missed


def test_notifications_survive_a_restart(tmp_path, monkeypatch):
    from app.config import Settings
    from app.container import build_container
    from app.db.models import Base
    from app.db.session import dispose_engine, get_engine, init_engine
    from app.main import create_app
    url = f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    env = Settings(_env_file=None, database_url=url, scanner_enabled=False, market_stream_enabled=False)

    class Bybit:
        exchange = "bybit"

        async def close(self):
            pass

    init_engine(url)

    async def create():
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await dispose_engine()

    asyncio.run(create())

    def factory(env_, store, hub, sf):
        return build_container(env_, store, hub, sf, client=Bybit())  # type: ignore[arg-type]

    with TestClient(create_app(env, factory, run_background=False)) as first:
        _emit(first, ev("kept"))
    with TestClient(create_app(env, factory, run_background=False)) as second:
        body = second.get("/api/notification-center/events").json()
        assert [x["event_id"] for x in body["items"]] == ["kept"]
        assert _emit(second, ev("kept")) == [None]  # still idempotent after the restart
