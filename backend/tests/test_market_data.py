from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.api.hub import EventHub
from app.db.session import session_factory
from app.exchanges.bybit import BybitClient, ExchangeError, parse_ticker
from app.market.service import MarketDataService
from app.market.state import MarketState
from app.market.validation import filter_closed, find_gaps, validate_candles
from app.timeutil import dt_to_ms, last_closed_open_time

BASE = "https://api.bybit.com"


def kline_item(t: datetime, price: float = 100.0) -> list[str]:
    return [str(dt_to_ms(t)), str(price), str(price + 1), str(price - 1), str(price + 0.5), "10", "1000"]


def bybit_ok(items):
    return httpx.Response(200, json={"retCode": 0, "retMsg": "OK", "result": {"list": items}})


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    import app.exchanges.bybit as bb
    monkeypatch.setattr(bb, "backoff_delay", lambda *a, **k: 0)


def test_closed_candle_filter_and_last_closed():
    now = datetime(2026, 11, 3, 10, 7, 30, tzinfo=UTC)
    assert last_closed_open_time(now, "15m") == datetime(2026, 11, 3, 9, 45, tzinfo=UTC)
    rows = [{"open_time": datetime(2026, 11, 3, 9, 45, tzinfo=UTC)}, {"open_time": datetime(2026, 11, 3, 10, 0, tzinfo=UTC)}]
    closed = filter_closed(rows, "15m", now)
    assert [r["open_time"].minute for r in closed] == [45]  # the 10:00 candle is still forming
    # Exactly at the close boundary the candle counts as closed.
    assert len(filter_closed(rows, "15m", datetime(2026, 11, 3, 10, 15, tzinfo=UTC))) == 2


def test_validation_drops_bad_candles_and_sorts():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    good = {"open_time": t + timedelta(minutes=15), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1}
    bad_hl = {**good, "open_time": t, "high": 1.2}  # high below close
    misaligned = {**good, "open_time": t + timedelta(minutes=7)}
    negative = {**good, "open_time": t + timedelta(minutes=30), "volume": -1}
    earlier = {**good, "open_time": t - timedelta(minutes=15)}
    out = validate_candles([good, bad_hl, misaligned, negative, earlier], "15m")
    assert [r["open_time"] for r in out] == [earlier["open_time"], good["open_time"]]


def test_gap_detection():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    times = [t, t + timedelta(minutes=15), t + timedelta(minutes=60)]
    assert find_gaps(times, "15m") == [(t + timedelta(minutes=30), t + timedelta(minutes=45))]


@respx.mock
async def test_klines_are_returned_ascending_and_retry_on_rate_limit():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    route = respx.get(f"{BASE}/v5/market/kline").mock(side_effect=[
        httpx.Response(200, json={"retCode": 10006, "retMsg": "Too many visits!"}),
        httpx.Response(403),
        bybit_ok([kline_item(t + timedelta(minutes=15)), kline_item(t)]),  # Bybit: newest first
    ])
    client = BybitClient(BASE)
    rows = await client.get_klines("BTCUSDT", "15m")
    assert route.call_count == 3
    assert [r["open_time"] for r in rows] == [t, t + timedelta(minutes=15)]
    assert route.calls[-1].request.url.params["category"] == "linear"
    await client.close()


@respx.mock
async def test_non_retryable_error_raises_immediately():
    route = respx.get(f"{BASE}/v5/market/kline").mock(
        return_value=httpx.Response(200, json={"retCode": 10001, "retMsg": "params error"}))
    client = BybitClient(BASE)
    with pytest.raises(ExchangeError):
        await client.get_klines("BTCUSDT", "15m")
    assert route.call_count == 1
    await client.close()


@respx.mock
async def test_kline_range_paginates_backwards():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    all_times = [start + timedelta(minutes=5 * k) for k in range(1500)]

    def handler(request: httpx.Request):
        end_ms = int(request.url.params["end"])
        start_ms = int(request.url.params["start"])
        sel = [t for t in all_times if start_ms <= dt_to_ms(t) <= end_ms][-1000:]
        return bybit_ok([kline_item(t) for t in reversed(sel)])

    route = respx.get(f"{BASE}/v5/market/kline").mock(side_effect=handler)
    client = BybitClient(BASE, rate_per_second=1000)
    rows = await client.get_klines_range("BTCUSDT", "5m", start, all_times[-1])
    assert len(rows) == 1500 and rows[0]["open_time"] == start and route.call_count == 2
    assert not find_gaps([r["open_time"] for r in rows], "5m")
    await client.close()


def test_ticker_parsing_and_stale_state():
    t = parse_ticker({"symbol": "BTCUSDT", "lastPrice": "76938.10", "price24hPcnt": "-0.01253",
                      "turnover24h": "3395018808.6", "fundingRate": "0.00007446", "openInterest": "67944.3",
                      "nextFundingTime": "1793664000000"})
    assert t["last_price"] == 76938.10 and t["change_24h_pct"] == pytest.approx(-1.253)
    state = MarketState(stale_after_seconds=30)
    assert state.ticker_view("BTCUSDT")["available"] is False
    state.update_ticker("BTCUSDT", t, "rest")
    assert not state.is_stale("BTCUSDT")
    # A delta containing only lastPrice keeps the other fields.
    state.update_ticker("BTCUSDT", {"last_price": 77000.0, "funding_rate": None}, "websocket")
    v = state.ticker_view("BTCUSDT")
    assert v["last_price"] == 77000.0 and v["funding_rate"] == pytest.approx(0.00007446)
    assert state.is_stale("BTCUSDT", now=datetime.now(UTC) + timedelta(seconds=31))


@respx.mock
async def test_market_service_excludes_forming_candle_and_persists(db):
    now = datetime(2026, 11, 3, 10, 7, tzinfo=UTC)
    last_closed = last_closed_open_time(now, "15m")
    times = [last_closed - timedelta(minutes=15 * k) for k in range(300)] + [last_closed + timedelta(minutes=15)]
    respx.get(f"{BASE}/v5/market/kline").mock(return_value=bybit_ok([kline_item(t) for t in sorted(times, reverse=True)]))
    client = BybitClient(BASE, rate_per_second=1000)
    svc = MarketDataService(client, session_factory, EventHub(), history_limit=300)
    series = await svc.get_closed_candles("BTCUSDT", "15m", now)
    assert series.last_open_time == last_closed  # the forming 10:00 candle is excluded
    assert series.is_current and not series.gaps and len(series.df) == 300
    # A fresh service instance loads the persisted history from the database.
    svc2 = MarketDataService(client, session_factory, EventHub(), history_limit=300)
    respx.get(f"{BASE}/v5/market/kline").mock(side_effect=AssertionError("should not call exchange"))
    s2 = await svc2.get_closed_candles("BTCUSDT", "15m", now)
    assert len(s2.df) == 300 and s2.last_open_time == last_closed
    await client.close()
