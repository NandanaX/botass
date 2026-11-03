from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest
import respx
from sqlalchemy import func, select

from app.alerts.telegram import TelegramClient, format_signal_message
from app.api.hub import EventHub
from app.config import Settings
from app.db.models import Notification, Signal
from app.db.session import session_factory
from app.logging_setup import JsonFormatter
from app.settings_store import RuntimeSettings
from app.signals.evaluation import evaluate_signal_outcome
from app.signals.identity import signal_id
from app.signals.service import SignalService
from app.strategy.engine import evaluate_at
from tests.conftest import phase1_params
from tests.test_strategy import forced

TOKEN = "123456:ABC-secret_token"
TG = "https://api.telegram.org"


def sample_signal(**kw):
    base = {"symbol": "BTCUSDT", "direction": "LONG", "timeframe": "15m", "entry_reference": 68_500.0,
            "stop_loss": 67_800.0, "take_profit": 69_900.0, "risk_reward": 2.0, "score": 82,
            "indicators": {"rsi": 58.2, "macd_hist": 12.5}, "explanation": "Bullish EMA alignment <b>&</b> breakout",
            "mtf_confirmed": True, "candle_close_time": "2026-11-03 10:15"}
    return {**base, **kw}


def test_telegram_message_format_and_escaping():
    msg = format_signal_message(sample_signal())
    for part in ("CRYPTO SIGNAL DETECTED", "Symbol: <b>BTCUSDT</b>", "Direction: <b>LONG</b>", "Timeframe: 15m",
                 "Entry Reference: $68,500.00", "Stop Loss: $67,800.00", "Take Profit: $69,900.00",
                 "Risk/Reward: 1:2", "Signal Score: 82/100", "Trend: Bullish", "RSI: 58.2", "MACD: Positive",
                 "no order has been placed"):
        assert part in msg, part
    assert "&lt;b&gt;&amp;&lt;/b&gt;" in msg  # user-facing text is HTML-escaped
    assert "win probability" in msg  # score is explicitly not a probability
    short = format_signal_message(sample_signal(direction="SHORT", indicators={"rsi": 41.0, "macd_hist": -3}))
    assert "Trend: Bearish" in short and "MACD: Negative" in short


def test_signal_id_is_deterministic():
    t = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    a = signal_id("bybit", "BTCUSDT", "15m", "LONG", t, "1.0.0+abc")
    assert a == signal_id("bybit", "BTCUSDT", "15m", "LONG", t, "1.0.0+abc")
    assert a != signal_id("bybit", "BTCUSDT", "15m", "SHORT", t, "1.0.0+abc")
    assert a != signal_id("bybit", "BTCUSDT", "15m", "LONG", t + timedelta(minutes=15), "1.0.0+abc")
    assert a != signal_id("bybit", "BTCUSDT", "15m", "LONG", t, "1.0.0+def")


@respx.mock
async def test_telegram_retries_on_429_and_5xx_then_succeeds(monkeypatch):
    route = respx.post(f"{TG}/bot{TOKEN}/sendMessage").mock(side_effect=[
        httpx.Response(429, json={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 0}}),
        httpx.Response(502, text="bad gateway"),
        httpx.Response(200, json={"ok": True, "result": {}}),
    ])
    client = TelegramClient(TOKEN, "42", TG)
    import app.alerts.telegram as tg

    async def no_sleep(_):
        return None

    monkeypatch.setattr(tg.asyncio, "sleep", no_sleep)
    res = await client.send_message("hi")
    assert res.ok and res.attempts == 3 and route.call_count == 3
    assert route.calls[0].request.content and b'"chat_id":"42"' in route.calls[0].request.content


@respx.mock
async def test_telegram_does_not_retry_client_errors_and_redacts_token(caplog):
    respx.post(f"{TG}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(400, json={"ok": False, "description": f"chat not found bot{TOKEN}"}))
    client = TelegramClient(TOKEN, "42", TG)
    with caplog.at_level(logging.WARNING):
        res = await client.send_message("hi")
    assert not res.ok and res.attempts == 1
    assert TOKEN not in (res.error or "")
    formatted = JsonFormatter().format(logging.LogRecord("x", logging.ERROR, "", 0, f"url /bot{TOKEN}/send", (), None))
    assert TOKEN not in formatted and "bot<redacted>" in formatted


class FakeTelegram:
    def __init__(self, ok: bool = True):
        self.sent: list[str] = []
        self.ok = ok

    async def send_message(self, text):
        from app.alerts.telegram import SendResult
        self.sent.append(text)
        return SendResult(self.ok, 1, None if self.ok else "boom")


async def test_signal_deduplication_and_single_alert(db, candles):
    params = phase1_params(mtf={"enabled": False})
    f, i = forced(candles, "LONG", params)
    ev = evaluate_at(f, i, params)
    tg = FakeTelegram()
    svc = SignalService(session_factory, EventHub(), Settings(), tg)  # type: ignore[arg-type]
    settings = RuntimeSettings(strategy=params)
    now = pd.Timestamp(ev.close_time).to_pydatetime() + timedelta(seconds=5)
    first = await svc.handle_setup("bybit", "BTCUSDT", ev, settings, now)
    second = await svc.handle_setup("bybit", "BTCUSDT", ev, settings, now)
    assert first is not None and first["notification_status"] == "SENT"
    assert second is None
    assert len(tg.sent) == 1
    async with session_factory()() as s:
        assert (await s.execute(select(func.count(Signal.id)))).scalar_one() == 1
        assert (await s.execute(select(func.count(Notification.id)))).scalar_one() == 1


@pytest.mark.parametrize("alerts,too_late,expected", [
    ({"telegram_enabled": False}, False, "DISABLED"),
    ({"min_score": 101 - 1}, False, "BELOW_THRESHOLD"),
    ({}, True, "SKIPPED_STALE"),
])
async def test_alert_gating(db, candles, alerts, too_late, expected):
    params = phase1_params(mtf={"enabled": False})
    f, i = forced(candles, "LONG", params)
    ev = evaluate_at(f, i, params)
    if expected == "BELOW_THRESHOLD":
        ev.setup.score = 50
    tg = FakeTelegram()
    svc = SignalService(session_factory, EventHub(), Settings(), tg)  # type: ignore[arg-type]
    settings = RuntimeSettings(strategy=params, alerts=alerts)
    now = pd.Timestamp(ev.close_time).to_pydatetime() + (timedelta(hours=3) if too_late else timedelta(seconds=5))
    out = await svc.handle_setup("bybit", "BTCUSDT", ev, settings, now)
    assert out["notification_status"] == expected
    assert tg.sent == []


def _bars(rows):
    t0 = pd.Timestamp("2026-01-01 00:00", tz="UTC")
    return pd.DataFrame([{"open_time": t0 + pd.Timedelta(minutes=15 * k), "open": o, "high": h, "low": lo, "close": c}
                         for k, (o, h, lo, c) in enumerate(rows)])


def test_signal_outcome_methodology():
    t_signal = pd.Timestamp("2026-01-01 00:00", tz="UTC")
    # Next open 100, then target 106 touched -> TARGET_HIT with R = 6/3 = 2
    c = _bars([(99, 100, 98, 99.5), (100, 101, 99, 100.5), (100.5, 106.5, 100, 106)])
    out = evaluate_signal_outcome("LONG", t_signal, 97, 106, c, 96)
    assert out["status"] == "TARGET_HIT" and out["r_multiple"] == pytest.approx(2)
    # Still undecided -> None (stays PENDING)
    assert evaluate_signal_outcome("LONG", t_signal, 97, 106, c.iloc[:2], 96) is None
    # Next candle opens below the stop -> INVALIDATED
    c2 = _bars([(99, 100, 98, 99.5), (96, 96.5, 95, 96)])
    assert evaluate_signal_outcome("LONG", t_signal, 97, 106, c2, 96)["status"] == "INVALIDATED"
    # Both touched in one candle -> conservative stop
    c3 = _bars([(99, 100, 98, 99.5), (100, 107, 96, 100)])
    assert evaluate_signal_outcome("LONG", t_signal, 97, 106, c3, 96)["status"] == "AMBIGUOUS_STOP"
    # Expiry after max_bars
    c4 = _bars([(99, 100, 98, 99.5), (100, 101, 99, 100), (100, 101, 99, 100.5)])
    exp = evaluate_signal_outcome("LONG", t_signal, 97, 106, c4, 2)
    assert exp["status"] == "EXPIRED" and exp["r_multiple"] == pytest.approx(0.5 / 3, abs=1e-4)


async def test_qualified_signal_persists_plan_and_alert_shows_computed_values(db, candles):
    params = phase1_params(mtf={"enabled": False})
    f, i = forced(candles, "LONG", params)
    ev = evaluate_at(f, i, params, symbol="BTCUSDT")
    tg = FakeTelegram()
    svc = SignalService(session_factory, EventHub(), Settings(), tg)  # type: ignore[arg-type]
    now = pd.Timestamp(ev.close_time).to_pydatetime() + timedelta(seconds=5)
    data = await svc.handle_setup("bybit", "BTCUSDT", ev, RuntimeSettings(strategy=params), now)
    assert data["setup_status"] == "QUALIFIED" and data["strategy_type"] == "breakout"
    plan = data["plan"]
    assert [t["label"] for t in plan["targets"]] == ["TP1", "TP2", "TP3"] and plan["sizing"]["quantity"] > 0
    msg = tg.sent[0]
    assert "CRYPTO TRADING SETUP" in msg
    for t in plan["targets"]:
        assert f"{t['label']}: ${t['price']:,.4f}" in msg  # prices ~100 use 4 decimals
    assert f"Position Notional: ${plan['sizing']['notional']:,.2f}" in msg


async def test_non_qualified_setups_are_never_persisted_or_alerted(db, candles):
    params = phase1_params(mtf={"enabled": False})
    f, i = forced(candles, "LONG", params, rsi=75.0)  # breakout outside the RSI band -> REJECTED
    ev = evaluate_at(f, i, params)
    assert ev.setup is None and any(s.status == "REJECTED" for s in ev.setups)
    tg = FakeTelegram()
    svc = SignalService(session_factory, EventHub(), Settings(), tg)  # type: ignore[arg-type]
    now = pd.Timestamp(ev.close_time).to_pydatetime()
    assert await svc.handle_setup("bybit", "BTCUSDT", ev, RuntimeSettings(strategy=params), now) is None
    assert tg.sent == []


def test_outcome_reports_targets_hit_and_weighted_r():
    times = pd.date_range("2026-01-01", periods=4, freq="15min", tz="UTC")
    candles = pd.DataFrame({"open_time": times, "open": [100, 100, 103, 106], "high": [100, 103.5, 106.5, 108],
                            "low": [99, 99.5, 102, 96], "close": [100, 103, 106, 99]})
    plan = {"partial_exits": True, "targets": [{"price": 103, "allocation_pct": 30}, {"price": 106, "allocation_pct": 40},
                                               {"price": 109, "allocation_pct": 30}]}
    r = evaluate_signal_outcome("LONG", times[0], 97, 106, candles, 50, plan)
    assert r["status"] == "TARGET_HIT"  # primary target (TP2)
    assert r["targets"]["targets_hit"] == ["TP1", "TP2"] and r["targets"]["final"] == "STOP"
    assert r["targets"]["weighted_r"] == pytest.approx(0.3 * 1 + 0.4 * 2 + 0.3 * -1)


def test_signal_id_distinguishes_strategies():
    t = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    assert signal_id("bybit", "BTCUSDT", "15m", "LONG", t, "2.0.0+a", "zone_breakout") != \
        signal_id("bybit", "BTCUSDT", "15m", "LONG", t, "2.0.0+a", "zone_pullback")
