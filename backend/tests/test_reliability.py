"""Phase 8 regressions: failures must stay contained and visible. Covers scanner isolation, restart
recovery, duplicate prevention across restarts, failed closes, the origin guard, API errors that keep
CORS headers, the forward-test lock in settings, monitoring and the System Health API."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text

from app.alerts.telegram import SendResult
from app.db.models import NotificationDelivery, NotificationEvent, PaperTrade
from app.exchanges.bybit import ExchangeError
from app.notifications.health import HealthMonitor
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
    async def broadcast(self, event, data):
        pass


def center(s=None, telegram=None):
    from app.db.session import session_factory
    from app.notifications.service import NotificationService
    return NotificationService(session_factory, Hub(), Store(s), telegram)


async def rows(model=NotificationEvent):
    from app.db.session import session_factory
    async with session_factory()() as session:
        return (await session.execute(select(model))).scalars().all()


# ----------------------------------------------------------------------------- scanner isolation

class _Series:
    def __init__(self):
        self.df = make_candles(5)
        self.gaps = []
        self.is_current = True


def _scanner(watch):
    from app.workers.scanner import Scanner

    class Signals:
        async def evaluate_pending(self, *a, **k):
            return 0

    class Market:
        exchange = "bybit"

        def now(self):
            return T0

    s = RuntimeSettings(watchlist=watch, timeframes=["1h"], strategy=phase1_params(mtf={"enabled": False}, zones={"htf_timeframes": []}))
    sc = Scanner(Market(), Store(s), Signals(), Hub(), None, 15.0, 0)

    class F:
        def time_at(self, i):
            return T0

        def __len__(self):
            return 1

    async def features_for(symbol, tf, settings, now):
        return F(), _Series()

    sc._features_for = features_for
    return sc


async def test_a_failure_on_one_coin_does_not_stop_the_others():
    sc = _scanner(["BTCUSDT", "XRPUSDT", "SUIUSDT"])
    seen = []

    async def evaluate(symbol, tf, features, series, settings, now):
        seen.append(symbol)
        if symbol == "XRPUSDT":
            raise ValueError("computed stop is not a positive price")

    sc._evaluate = evaluate
    await sc.scan_once()  # must not raise
    assert seen == ["BTCUSDT", "XRPUSDT", "SUIUSDT"]
    assert sc.status["cycle_errors"] == 1 and "XRPUSDT 1h" in sc.status["last_error"]
    # the next clean cycle clears the error instead of leaving it for ever
    sc._evaluate = lambda *a: asyncio.sleep(0)
    await sc.scan_once()
    assert sc.status["cycle_errors"] == 0 and sc.status["last_error"] is None


async def test_market_data_failure_on_one_coin_is_counted_and_others_continue():
    sc = _scanner(["BTCUSDT", "XRPUSDT"])
    good = sc._features_for
    seen = []

    async def features_for(symbol, tf, settings, now):
        if symbol == "BTCUSDT":
            raise ExchangeError("HTTP 503 from Bybit")
        return await good(symbol, tf, settings, now)

    async def evaluate(symbol, *a):
        seen.append(symbol)

    sc._features_for, sc._evaluate = features_for, evaluate
    await sc.scan_once()
    assert seen == ["XRPUSDT"] and sc.status["market_errors"] == 1


async def test_scanner_says_so_when_it_is_working_from_old_candles():
    sc = _scanner(["BTCUSDT"])
    good = sc._features_for

    async def stale(symbol, tf, settings, now):
        feats, series = await good(symbol, tf, settings, now)
        series.is_current = False
        return feats, series

    sc._features_for = stale
    sc._evaluate = lambda *a: asyncio.sleep(0)
    await sc.scan_once()
    assert sc.status["series_behind"] >= 1


# ----------------------------------------------------------------------------- paper trading across restarts

class _State:
    view = {}

    def ticker_view(self, symbol):
        return self.view


class _Market:
    exchange = "bybit"

    def __init__(self):
        self.state = _State()


def _frame():
    p = phase1_params(mtf={"enabled": False})
    df = make_candles(1200, seed=3, drift=0.0008)
    f = compute_features(df, "15m", p)
    for i in range(p.warmup_bars, len(f) - 40):
        if evaluate_at(f, i, p, {}, context=False).setup is not None:
            f2 = compute_features(df.iloc[: i + 1], "15m", p)
            return p, f2, i, evaluate_at(f2, i, p, {}, context=False)
    raise AssertionError("no setup")


async def _opened():
    from app.db.session import session_factory
    from app.forward.service import ForwardService
    p, f, i, e = _frame()
    s = RuntimeSettings(strategy=p)
    m = _Market()
    px = float(f.close[i])
    m.state.view = {"available": True, "bid_price": px * 0.9999, "ask_price": px * 1.0001, "last_price": px,
                    "quote_at": utcnow().isoformat(), "funding_rate": 0.0001}
    svc = ForwardService(m, Store(s), session_factory)
    close = datetime.fromtimestamp(int(f.close_time[i]) / 1e9, UTC) + timedelta(seconds=5)
    await svc.on_evaluation("TESTUSDT", "15m", f, e, True, close, {}, None, None)
    return svc, s, m, f, e, close


async def test_restart_resumes_exit_monitoring_before_the_next_candle(db):
    from app.db.session import session_factory
    from app.forward.service import ForwardService
    svc, s, m, _, _, _ = await _opened()
    assert svc.open
    tid, t = next(iter(svc.open.items()))
    # a new process: nothing in memory
    fresh = ForwardService(m, Store(s), session_factory)
    await fresh.startup()
    assert set(fresh.open) == set(svc.open)
    hit = t["stop_loss"] * 0.999
    view = {**m.state.view, "bid_price": hit, "ask_price": hit, "last_price": hit, "quote_at": utcnow().isoformat()}
    fresh.open[tid]["fill_time"] = utcnow() - timedelta(minutes=5)
    await fresh.on_ticker("TESTUSDT", view)
    closed = next(r for r in await rows(PaperTrade) if r.id == tid)
    assert closed.status == "CLOSED" and closed.exit_reason == "STOP"


async def test_duplicate_signal_after_restart_cannot_open_a_second_position(db):
    from app.db.session import session_factory
    from app.forward.service import ForwardService
    svc, s, m, f, e, close = await _opened()
    before = sorted((r.id, r.status) for r in await rows(PaperTrade))
    fresh = ForwardService(m, Store(s), session_factory)
    await fresh.startup()
    n_open = len(fresh.open)
    for _ in range(3):  # duplicate callbacks for the same candle
        await fresh.on_evaluation("TESTUSDT", "15m", f, e, True, close, {}, None, None)
    assert sorted((r.id, r.status) for r in await rows(PaperTrade)) == before
    assert len(fresh.open) == n_open


async def test_closing_twice_cannot_double_count_a_result(db):
    svc, _, _, _, _, close = await _opened()
    tid, t = next(iter(svc.open.items()))
    await asyncio.gather(svc._close(tid, "STOP", t["stop_loss"], close + timedelta(hours=1), "ticker_quote"),
                         svc._close(tid, "TARGET", t["take_profit"], close + timedelta(hours=1), "candle"))
    row = next(r for r in await rows(PaperTrade) if r.id == tid)
    assert row.status == "CLOSED" and row.exit_reason == "STOP"
    acct = await svc.account(t["variant_key"])
    assert len([1 for when, _ in acct.realized if when == row.exit_time]) == 1


async def test_a_failed_close_keeps_the_trade_monitored_and_is_retried(db):
    svc, _, m, _, _, _ = await _opened()
    tid, t = next(iter(svc.open.items()))
    t["fill_time"] = utcnow() - timedelta(minutes=5)
    hit = t["stop_loss"] * 0.999
    view = {**m.state.view, "bid_price": hit, "ask_price": hit, "last_price": hit, "quote_at": utcnow().isoformat()}
    real = svc._settle

    async def broken(*a, **k):
        raise RuntimeError("database is locked")

    svc._settle = broken
    await svc.on_ticker("TESTUSDT", view)  # must not raise into the market stream
    assert tid in svc.open and "locked" in svc.status["last_error"]
    assert next(r for r in await rows(PaperTrade) if r.id == tid).status == "OPEN"
    svc._settle = real
    await svc.on_ticker("TESTUSDT", {**view, "quote_at": utcnow().isoformat()})
    assert tid not in svc.open
    assert next(r for r in await rows(PaperTrade) if r.id == tid).status == "CLOSED"


# ----------------------------------------------------------------------------- monitoring

class _Clock:
    def __init__(self, fail=False, offset=0.0):
        self.fail, self.offset = fail, offset

    async def server_time(self):
        if self.fail:
            raise ExchangeError("HTTP 503 from Bybit /v5/market/time")
        return utcnow() + timedelta(seconds=self.offset)


class _HealthState:
    def __init__(self):
        self.ws_connected = True
        self.ws_last_message_at = None
        self.ws_reconnects = 0
        self.rest_fallback_active = False
        self.last_error = None
        self.tickers = {}
        self.stale = False

    def ticker_view(self, s):
        return {"available": True}

    def is_stale(self, s, now=None):
        return self.stale


def monitor(c, client=None):
    class M:
        state = _HealthState()
        clock_offset_seconds = 0.0

    class S:
        interval = 15.0
        status = {"is_leader": False}

    class E:
        market_stream_enabled = True
        scanner_enabled = True

    m = M()
    m.client = client
    return HealthMonitor(c, m, S(), None, c.settings, E())


async def test_a_pc_sleep_is_reported_as_a_pause_not_as_a_bybit_outage(db):
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    h = monitor(c)
    h.scanner.status = {"is_leader": True, "running": True, "last_cycle_at": T0.isoformat()}
    await h.check(T0)
    wake = T0 + timedelta(hours=3)
    h.market.state.stale = True  # everything is out of date at the moment of waking
    await h.check(wake)
    await h.check(wake + timedelta(minutes=1))
    titles = [r.title for r in await rows()]
    assert titles == ["Monitoring was paused (PC asleep or app suspended)", "Monitoring resumed after a pause"]
    assert "asleep" in (await rows())[0].message and (await rows())[0].severity == "WARNING"
    assert h.probes.pauses[0]["duration"] == "180 minutes"
    # once it has had time to catch up, a real problem is reported normally
    for m in (2, 3, 4, 5, 6, 7, 8):
        await h.check(wake + timedelta(minutes=m))
    assert "Bybit market data unavailable" in [r.title for r in await rows()]


async def test_database_check_is_a_real_write(db):
    from app.db.session import session_factory
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    h = monitor(c)
    await h.check(T0)
    assert h.probes.db_ok is True and h.probes.db_latency_ms is not None
    async with session_factory()() as session:
        assert (await session.execute(text("select count(*) from app_settings where key='health_probe'"))).scalar_one() == 1

    class ReadOnly:
        def __call__(self):
            class S:
                async def __aenter__(self):
                    raise RuntimeError("attempt to write a readonly database")

                async def __aexit__(self, *a):
                    return False

            return S()

    real = c._session_factory
    c._session_factory = lambda: ReadOnly()
    await h.check(T0 + timedelta(seconds=30))
    assert h.probes.db_ok is False and "readonly" in h.probes.db_error
    c._session_factory = real
    await h.check(T0 + timedelta(seconds=60))
    assert h.probes.db_ok is True
    assert (await c.incidents())[0]["key"] == "database"


async def test_rest_failure_opens_one_incident_escalates_and_recovers(db):
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    clock = _Clock(fail=True)
    h = monitor(c, clock)
    for m in range(0, 15):
        h._last_probe = None
        await h.check(T0 + timedelta(minutes=m))
    inc = [i for i in await c.incidents() if i["key"] == "bybit_rest"]
    assert len(inc) == 1 and inc[0]["severity"] == "CRITICAL" and inc[0]["status"] == "OPEN"
    types = [r.event_type for r in await rows() if r.payload.get("incident_key") == "bybit_rest"]
    assert types == ["INCIDENT_OPENED", "INCIDENT_ESCALATED"]  # no alert per retry
    clock.fail = False
    h._last_probe = None
    await h.check(T0 + timedelta(minutes=15))
    assert [i for i in await c.incidents() if i["key"] == "bybit_rest"][0]["status"] == "RESOLVED"


async def test_a_network_outage_is_one_cause_not_a_stopped_scanner(db):
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    h = monitor(c, _Clock(fail=True))
    h.scanner.status = {"is_leader": True, "running": True, "last_cycle_at": T0.isoformat()}
    for m in range(0, 8):
        h._last_probe = None
        await h.check(T0 + timedelta(minutes=m))
    keys = [i["key"] for i in await c.incidents()]
    assert "bybit_rest" in keys and "scanner" not in keys
    # with Bybit answering, a scanner that makes no progress is a real problem
    h.probes.rest_ok = True
    h.market.client = None
    await h.check(T0 + timedelta(minutes=9))
    assert "scanner" in [i["key"] for i in await c.incidents()]


async def test_a_slow_check_is_not_mistaken_for_the_pc_sleeping(db):
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    h = monitor(c)
    real = h._probe_database
    await h.check()

    async def slow():
        h.heartbeat_at = None
        await real()
        h._since["__marker"] = T0

    h._probe_database = slow
    await h.check()
    start = h._last_check
    assert start is not None
    await h.check(start + timedelta(seconds=30))
    assert "app_paused" not in [i["key"] for i in await c.incidents()]


async def test_alerts_survive_a_long_telegram_outage_and_send_on_recovery(db):
    from app.notifications.events import EventIn

    class Flaky:
        ok = False
        messages: list[str] = []

        async def send_message(self, text):
            self.messages.append(text)
            return SendResult(self.ok, 1, None if self.ok else "ConnectError: no network")

    tg = Flaky()
    tg.messages = []
    c = center(telegram=tg)
    t = utcnow()
    for name in ("sl-1", "sl-2"):
        await c.emit(EventIn(event_id=name, event_type="PAPER_STOP_LOSS_HIT", title=name, paper_trade_id=name, payload={"portfolio_taken": True}))
    for mins in (0, 2, 8, 30, 50):  # well past where a fixed number of attempts would have given up
        await c.deliver_pending(t + timedelta(minutes=mins))
    assert {d.status for d in await rows(NotificationDelivery)} == {"RETRY"}
    tg.ok = True
    await c.deliver_pending(t + timedelta(minutes=70))
    await c.deliver_pending(t + timedelta(minutes=70))
    assert {d.status for d in await rows(NotificationDelivery)} == {"SENT"}  # no duplicates of the event, none lost
    assert [r.title for r in await rows() if r.event_type == "PAPER_STOP_LOSS_HIT"] == ["sl-1", "sl-2"]
    # an alert too old to be useful expires rather than arriving hours late
    tg.ok = False
    await c.emit(EventIn(event_id="old", event_type="PAPER_STOP_LOSS_HIT", title="old", paper_trade_id="old", payload={"portfolio_taken": True}))
    await c.deliver_pending(utcnow() + timedelta(hours=7))
    assert "EXPIRED" in {d.status for d in await rows(NotificationDelivery)}


async def test_clock_drift_updates_the_offset_and_alerts(db):
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    h = monitor(c, _Clock(offset=-30.0))
    await h.check(T0)
    assert h.market.clock_offset_seconds == pytest.approx(-30, abs=1)
    assert h.probes.clock_skew_s == pytest.approx(30, abs=1)
    assert [i["key"] for i in await c.incidents()] == ["clock_skew"]


async def test_a_slow_reply_is_not_mistaken_for_clock_drift(db):
    class Slow:
        async def server_time(self):
            when = utcnow()  # the exchange answered at the start, then the reply took a long time to arrive
            await asyncio.sleep(2.2)
            return when

    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    h = monitor(c, Slow())
    await h.check(T0)
    assert h.probes.clock_skew_s is None and h.market.clock_offset_seconds == 0.0
    assert h.probes.rest_ok is True  # slow, but it did answer
    assert "clock_skew" not in [i["key"] for i in await c.incidents()]


async def test_connected_but_silent_websocket_counts_as_down(db):
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    h = monitor(c)
    h.market.state.ws_last_message_at = T0 - timedelta(minutes=5)  # socket "connected", no data
    await h.check(T0)
    await h.check(T0 + timedelta(minutes=2))
    assert "bybit_websocket" in [i["key"] for i in await c.incidents()]


async def test_a_websocket_that_keeps_dropping_is_reported(db):
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    h = monitor(c)
    for k in range(7):  # connected at every check, but it has dropped again since the last one
        h.market.state.ws_reconnects = k * 2
        h.market.state.ws_last_message_at = T0 + timedelta(minutes=k)
        await h.check(T0 + timedelta(minutes=k))
    assert "bybit_websocket_unstable" in [i["key"] for i in await c.incidents()]


async def test_telegram_backlog_and_worker_stall_are_detected(db):
    from app.db.session import session_factory
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    async with session_factory()() as session:
        session.add(NotificationEvent(recipient="local", event_id="x", event_type="PAPER_TP1_HIT", category="trade", severity="SUCCESS", title="t", message="m", payload={},
                                      is_group_summary=False, muted=False, delivery_status="TELEGRAM_PENDING", occurred_at=T0, created_at=T0))
        await session.flush()
        nid = (await session.execute(select(NotificationEvent.id))).scalar_one()
        session.add(NotificationDelivery(notification_id=nid, channel="telegram", status="RETRY", attempts=1, message="m", next_attempt_at=T0, created_at=T0, updated_at=T0))
        await session.commit()
    h = monitor(c)
    c.heartbeat_at = T0 - timedelta(minutes=5)
    await h.check(T0 + timedelta(hours=1))
    keys = {i["key"] for i in await c.incidents()}
    assert {"telegram_backlog", "notification_worker"} <= keys
    backlog = next(r for r in await rows() if r.payload.get("incident_key") == "telegram_backlog")
    assert (await rows(NotificationDelivery))[0].notification_id == nid and backlog.delivery_status != "TELEGRAM_PENDING"


async def test_crashed_background_task_is_reported(db):
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))
    h = monitor(c)

    async def boom():
        raise RuntimeError("worker exploded")

    task = asyncio.create_task(boom(), name="notification-outbox")
    await asyncio.wait([task])
    h.tasks = [task]
    await h.check(T0)
    inc = (await c.incidents())[0]
    assert inc["key"] == "task:notification-outbox" and "worker exploded" in inc["message"] and inc["severity"] == "CRITICAL"


async def test_monitor_errors_never_escape(db):
    c = center(RuntimeSettings(watchlist=["BTCUSDT"]))

    class Bad:
        async def server_time(self):
            raise ZeroDivisionError("bug in a probe")

    h = monitor(c, Bad())
    await h.check(T0)  # a bug in one check neither raises nor stops the conditions
    assert h.heartbeat_at == T0


async def test_failed_telegram_never_interrupts_paper_trading(db):
    from app.db.session import session_factory
    from app.forward.service import ForwardService

    class Down:
        async def send_message(self, text):
            return SendResult(False, 4, "HTTP 500: down")

    svc, s, m, f, e, close = await _opened()
    c = center(s, Down())
    fresh = ForwardService(m, Store(s), session_factory)
    fresh.center = c
    await fresh.startup()
    tid, t = next(iter(fresh.open.items()))
    await fresh._close(tid, "STOP", t["stop_loss"], close + timedelta(hours=1), "ticker_quote")
    await c.deliver_pending()
    assert next(r for r in await rows(PaperTrade) if r.id == tid).status == "CLOSED"
    assert fresh.status["last_error"] is None
    assert (await rows(NotificationDelivery))[0].status == "RETRY"


# ----------------------------------------------------------------------------- API

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

        async def get_instrument(self, symbol):
            return {"status": "Trading"}

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

    @app.get("/api/__boom")
    async def boom():
        raise RuntimeError("secret detail must not leak")

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


GOOD, EVIL = {"Origin": "http://localhost:3000"}, {"Origin": "https://evil.example"}


def test_other_websites_cannot_trigger_actions(api):
    assert api.post("/api/alerts/telegram/test", headers=EVIL).status_code == 403
    assert api.post("/api/notification-center/read-all", headers=EVIL).status_code == 403
    assert api.put("/api/settings", headers=EVIL, json={}).status_code == 403
    assert api.post("/api/notification-center/read-all", headers={"Origin": "null"}).status_code == 403
    # the dashboard and non-browser clients are unaffected
    assert api.post("/api/notification-center/read-all", headers=GOOD).status_code == 200
    assert api.post("/api/notification-center/read-all").status_code == 200
    # reading is not blocked here; the browser refuses to expose it without a CORS header
    r = api.get("/api/settings", headers=EVIL)
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers


def test_other_websites_cannot_open_the_websocket(api):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect("/ws", headers=EVIL) as ws:
            ws.receive_json()
    with api.websocket_connect("/ws", headers=GOOD) as ws:
        assert ws.receive_json()["event"] == "hello"
    with api.websocket_connect("/ws") as ws:
        assert ws.receive_json()["event"] == "hello"


def test_server_errors_keep_cors_headers_and_hide_details(api):
    r = api.get("/api/__boom", headers=GOOD)
    assert r.status_code == 500 and r.json() == {"detail": "internal server error"}
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_settings_change_cannot_silently_restart_the_forward_test(api):
    s = api.get("/api/settings").json()["settings"]
    changed = {**s, "strategy": {**s["strategy"], "rsi_long_max": 72}}
    r = api.put("/api/settings", json=changed)
    assert r.status_code == 409 and "forward test" in r.json()["detail"] and "A, B, C" in r.json()["detail"]
    assert api.get("/api/settings").json()["settings"]["strategy"]["rsi_long_max"] == 70  # not saved
    paper = {**s, "validation": {**s["validation"], "paper": {**s["validation"]["paper"], "taker_fee_pct": 0.06}}}
    assert api.put("/api/settings", json=paper).status_code == 409
    # changes outside the locked configuration need no confirmation
    assert api.put("/api/settings", json={**s, "alerts": {**s["alerts"], "min_score": 10}}).status_code == 200
    assert api.put("/api/settings", json={**s, "timeframes": ["15m", "1h"]}).status_code == 200
    # an explicit confirmation is honoured
    done = api.put("/api/settings", params={"confirm_new_forward_period": True}, json=changed)
    assert done.status_code == 200 and done.json()["settings"]["strategy"]["rsi_long_max"] == 72


def test_system_health_does_not_report_healthy_without_evidence(api):
    d = api.get("/api/system/health").json()
    comp = {c["key"]: c for c in d["components"]}
    assert set(comp) == {"database", "bybit_rest", "bybit_ws", "market_data", "scanner", "signal_engine", "paper_worker", "telegram", "notifications", "monitor"}
    # nothing has run in this configuration, so nothing may claim to be healthy
    assert {c["status"] for c in comp.values()} == {"UNKNOWN"}
    assert d["overall"] == "UNKNOWN" and d["real_money_trading"] is False and d["decision_mode"] == "shadow"
    assert d["uptime_seconds"] is not None
    assert "token" not in json.dumps(d).lower()


def test_system_health_reflects_real_state(api):
    c = api.app.state.container
    now = utcnow()
    c.scanner.status.update(running=True, is_leader=True, last_cycle_at=(now - timedelta(minutes=30)).isoformat())
    c.env.scanner_enabled = True
    c.scanner.status.update(last_evaluated_at=(now - timedelta(seconds=30)).isoformat())
    c.health.heartbeat_at = now - timedelta(hours=1)
    c.health.probes.db_ok, c.health.probes.db_checked_at, c.health.probes.db_latency_ms = True, now, 3.0
    c.health.probes.rest_ok, c.health.probes.rest_checked_at, c.health.probes.rest_failures, c.health.probes.rest_error = False, now, 5, "HTTP 503"
    comp = {x["key"]: x for x in api.get("/api/system/health").json()["components"]}
    assert comp["scanner"]["status"] == "DOWN"  # the process is running, but its heartbeat is old
    assert comp["signal_engine"]["status"] == "HEALTHY"
    assert comp["monitor"]["status"] == "DOWN"
    assert comp["database"]["status"] == "HEALTHY"
    assert comp["bybit_rest"]["status"] == "DOWN"
    c.market.state.ws_connected = True
    c.market.state.ws_last_message_at = now - timedelta(minutes=10)
    c.env.market_stream_enabled = True
    comp = {x["key"]: x for x in api.get("/api/system/health").json()["components"]}
    assert comp["bybit_ws"]["status"] == "DEGRADED"  # connected is not the same as up to date
    assert api.get("/api/system/health").json()["overall"] == "DOWN"


def test_telegram_is_not_healthy_while_messages_are_failing(api):
    from app.db.session import session_factory
    c = api.app.state.container
    c.env.telegram_bot_token, c.env.telegram_chat_id = SecretStr("x"), "1"  # not a real token; nothing is sent here
    now = utcnow()

    async def seed():
        async with session_factory()() as session:
            session.add(NotificationEvent(recipient="local", event_id="x", event_type="PAPER_TP1_HIT", category="trade", severity="SUCCESS", title="t", message="m", payload={},
                                          is_group_summary=False, muted=False, delivery_status="TELEGRAM_RETRY", occurred_at=now, created_at=now))
            await session.flush()
            nid = (await session.execute(select(NotificationEvent.id))).scalar_one()
            session.add(NotificationDelivery(notification_id=nid, channel="telegram", status="SENT", attempts=1, message="m", next_attempt_at=now, created_at=now, updated_at=now, sent_at=now))
            await session.commit()
            return nid

    nid = api.portal.call(seed)
    tg = lambda: next(x for x in api.get("/api/system/health").json()["components"] if x["key"] == "telegram")  # noqa: E731
    assert tg()["status"] == "HEALTHY"

    async def retry():
        async with session_factory()() as session:
            (await session.execute(select(NotificationDelivery).where(NotificationDelivery.notification_id == nid))).scalar_one().status = "RETRY"
            await session.commit()

    api.portal.call(retry)
    assert tg()["status"] == "DEGRADED" and "retried" in tg()["detail"]


def test_system_health_still_answers_when_the_database_fails(api, monkeypatch):
    import app.api.routes_system as rs

    def broken():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(rs, "session_factory", broken)
    r = api.get("/api/system/health")
    assert r.status_code == 200 and "gone" in r.json()["database_error"]
    assert next(c for c in r.json()["components"] if c["key"] == "telegram")["status"] == "UNKNOWN"
