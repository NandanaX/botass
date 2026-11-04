from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.main import create_app


class FakeBybit:
    """Offline stand-in for the Bybit client: deterministic synthetic candles (test data only)."""

    exchange = "bybit"

    async def get_instrument(self, symbol):
        if symbol == "FAKEUSDT":
            return None
        return {"symbol": symbol, "status": "Trading", "baseCoin": symbol.removesuffix("USDT"),
                "priceFilter": {"tickSize": "0.01"},
                "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "1000",
                                  "maxMktOrderQty": "100", "minNotionalValue": "5"},
                "leverageFilter": {"maxLeverage": "100"}}

    async def get_open_interest(self, symbol, interval, limit=50):
        from datetime import timedelta

        from app.timeutil import utcnow
        now = utcnow()
        return [{"time": now - timedelta(hours=limit - k), "open_interest": 1000.0 + 10 * k} for k in range(limit)]

    async def get_account_ratio(self, symbol, period, limit=50):
        from app.timeutil import utcnow
        return [{"time": utcnow(), "buy_ratio": 0.7, "sell_ratio": 0.3}]

    async def get_funding_history(self, symbol, start, end):
        return [{"funding_time": end, "rate": 0.0001}]

    async def get_risk_tiers(self, symbol):
        return [{"maintenanceMargin": "0.005", "isLowestRisk": 1}]

    async def server_time(self):
        from app.timeutil import utcnow
        return utcnow()

    async def get_klines_range(self, symbol, timeframe, start, end):
        import math

        from app.timeutil import tf_delta
        step = tf_delta(timeframe)
        t = start
        rows = []
        while t <= end:
            x = t.timestamp()
            c = 100 + 3 * math.sin(x / 1.2e5) + 1.5 * math.sin(x / 3.1e4) + 0.4 * math.sin(x / 7e3)
            px = (t - step).timestamp()
            o = 100 + 3 * math.sin(px / 1.2e5) + 1.5 * math.sin(px / 3.1e4) + 0.4 * math.sin(px / 7e3)
            rows.append({"open_time": t, "open": o, "high": max(o, c) + 0.2, "low": min(o, c) - 0.2, "close": c,
                         "volume": 100 + 50 * math.sin(x / 5e3) ** 2, "turnover": 1e4})
            t += step
        return rows

    async def close(self):
        return None


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'api.db'}"
    env = Settings(database_url=url, scanner_enabled=False, market_stream_enabled=False, log_json=False,
                   telegram_bot_token=None, telegram_chat_id=None)

    def factory(env_, store, hub, sf):
        return build_container(env_, store, hub, sf, client=FakeBybit())  # type: ignore[arg-type]

    # Create schema through the real Alembic migration.
    from alembic import command
    from alembic.config import Config
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    app = create_app(env, container_factory=factory, run_background=False)
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/api/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    assert r.json()["checks"]["telegram"] == "not configured"


def test_risk_endpoint_validation(client):
    ok = client.post("/api/risk/calculate", json={"account_balance": 1000, "risk_pct": 1, "direction": "LONG",
                                                  "entry_price": 100, "stop_loss": 95, "take_profit": 110})
    assert ok.status_code == 200 and ok.json()["max_loss"] == 10
    bad = client.post("/api/risk/calculate", json={"account_balance": 1000, "direction": "LONG",
                                                   "entry_price": 100, "stop_loss": 105})
    assert bad.status_code == 422
    assert client.post("/api/risk/calculate", json={"account_balance": -5, "direction": "LONG",
                                                    "entry_price": 100, "stop_loss": 95}).status_code == 422


def test_settings_roundtrip_and_validation(client):
    s = client.get("/api/settings").json()
    assert s["settings"]["watchlist"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert "telegram_bot_token" not in str(s) and s["telegram"]["configured"] is False
    new = s["settings"] | {"watchlist": ["BTCUSDT", "XRPUSDT"], "timeframes": ["1h", "15m"]}
    r = client.put("/api/settings", json=new)
    assert r.status_code == 200
    assert r.json()["settings"]["watchlist"] == ["BTCUSDT", "XRPUSDT"]
    assert r.json()["settings"]["timeframes"] == ["15m", "1h"]
    assert client.put("/api/settings", json=new | {"watchlist": ["FAKEUSDT"]}).status_code == 422
    assert client.put("/api/settings", json=new | {"watchlist": ["not a symbol"]}).status_code == 422
    assert client.put("/api/settings", json=new | {"timeframes": ["7m"]}).status_code == 422
    bad_strategy = new | {"strategy": new["strategy"] | {"ema_fast": 300}}
    assert client.put("/api/settings", json=bad_strategy).status_code == 422


def test_signal_list_query_validation(client):
    assert client.get("/api/signals").json() == {"total": 0, "items": []}
    assert client.get("/api/signals?timeframe=7m").status_code == 422
    assert client.get("/api/signals?sort=drop_table").status_code == 422
    assert client.get("/api/signals?limit=0").status_code == 422
    assert client.get("/api/signals/doesnotexist").status_code == 404
    assert client.get("/api/signals/stats").json()["total"] == 0


def test_backtest_validation(client):
    r = client.post("/api/backtests", json={"symbol": "BTCUSDT", "timeframe": "15m",
                                            "start": "2026-02-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"})
    assert r.status_code == 422
    r = client.post("/api/backtests", json={"symbol": "BTCUSDT", "timeframe": "15m",
                                            "start": "2026-01-01T00:00:00", "end": "2026-02-01T00:00:00"})
    assert r.status_code == 422  # naive timestamps rejected
    assert client.get("/api/backtests/unknown").status_code == 404


def test_market_routes_validate_symbol_and_timeframe(client):
    assert client.get("/api/market/btcusdt/candles").status_code == 422
    assert client.get("/api/market/BTCUSDT/candles?timeframe=2h").status_code == 422
    assert client.get("/api/market/BTCUSDT/analysis?timeframe=15m").status_code == 404


def test_telegram_test_without_configuration(client):
    r = client.post("/api/alerts/telegram/test")
    assert r.status_code == 400 and "not configured" in r.json()["detail"]


def test_setup_endpoint_returns_zones_setups_and_sizing(client):
    r = client.get("/api/market/BTCUSDT/setup?timeframe=15m&account_balance=5000&risk_pct=0.5&leverage=3")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["decision"] in ("QUALIFIED", "WATCHLIST", "REJECTED", "NO_SETUP")
    assert d["market"]["ready"] and d["market"]["trend"] in ("BULLISH", "BEARISH", "NEUTRAL")
    assert d["sizing_inputs"]["account_balance"] == 5000 and d["sizing_inputs"]["risk_pct"] == 0.5
    assert d["instrument"]["qty_step"] == 0.001 and d["instrument"]["maintenance_margin_rate"] == 0.005
    assert set(d["structure"]["htf_zones"]) == {"1h", "4h"}
    zones = d["structure"]["zones"] + [z for zs in d["structure"]["htf_zones"].values() for z in zs]
    assert zones, "synthetic oscillating data should produce zones"
    for z in zones:
        assert z["lower"] <= z["upper"] and z["zone_type"] in ("SUPPORT", "RESISTANCE")
        assert z["status"] in ("ACTIVE", "BROKEN", "INVALIDATED") and z["tests"] >= 2 and z["explanation"]
    for s in d["setups"]:
        assert s["status"] in ("QUALIFIED", "WATCHLIST", "REJECTED")
        if s["status"] == "REJECTED":
            assert s["reasons"] and all("code" in x and "message" in x for x in s["reasons"])
    if not d["setups"]:
        assert "does not force a trade" in d["decision_note"]
    assert client.get("/api/market/BTCUSDT/setup?leverage=50").status_code == 422  # above max_leverage 20


def test_candles_endpoint_includes_zones_and_swings(client):
    d = client.get("/api/market/BTCUSDT/candles?timeframe=15m&limit=300").json()
    assert "zones" in d and "htf_zones" in d and d["swings"]
    first = d["candles"][0]["time"]
    assert all(s["time"] >= first and s["confirmed_time"] >= s["time"] for s in d["swings"])
    assert d["levels"] == [] or {"price", "kind", "lower", "upper"} <= set(d["levels"][0])


def test_risk_endpoint_applies_exchange_rules(client):
    r = client.post("/api/risk/calculate", json={"account_balance": 10000, "risk_pct": 1, "direction": "LONG",
                                                 "entry_price": 100.005, "stop_loss": 97, "symbol": "BTCUSDT",
                                                 "take_profits": [103, 106, 109], "leverage": 5,
                                                 "slippage_pct": 0.02})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["instrument"]["tick_size"] == 0.01
    assert round(d["quantity"] / 0.001, 6) == round(d["quantity"] / 0.001)
    assert d["estimated_loss_at_stop_net"] <= d["max_loss"] + 1e-9
    assert len(d["targets"]) == 3 and any("tick size" in w for w in d["warnings"])
    bad = client.post("/api/risk/calculate", json={"account_balance": 1000, "direction": "LONG", "entry_price": 100,
                                                   "stop_loss": 95, "symbol": "FAKEUSDT"})
    assert bad.status_code == 422


def test_analysis_endpoints(client):
    a = client.get("/api/analysis/BTCUSDT?timeframe=1h")
    assert a.status_code == 200, a.text
    d = a.json()
    for key in ("trend", "structure", "momentum", "volatility", "bias", "alignment_score", "scenarios", "evidence",
                "chart_patterns", "candlestick_patterns", "data_source", "futures"):
        assert key in d
    assert [s["name"][0] for s in d["scenarios"]] == ["A", "B", "C"]
    assert d["futures"]["sources"]["liquidations"]["status"] == "not_supported"
    assert any(i["key"] == "long_short" for i in d["futures"]["items"])  # 70% long accounts -> crowded context
    m = client.get("/api/analysis/BTCUSDT/mtf").json()
    assert [r["timeframe"] for r in m["timeframes"]] == ["1w", "1d", "4h", "1h", "15m", "5m"]
    o = client.get("/api/analysis/BTCUSDT/overlays?timeframe=1h&limit=300").json()
    for key in ("structure_events", "swings", "chart_patterns", "candlestick_patterns", "trendlines", "fair_value_gaps",
                "moving_averages"):
        assert key in o
    r = client.get("/api/analysis/BTCUSDT/report?timeframe=1h").json()
    assert len(r["sections"]) == 12 and r["method"].startswith("deterministic")
    assert "Bybit" in r["data_source"]["exchange"] and r["generated_from_candle"]
    v = client.get("/api/analysis/BTCUSDT/validation?timeframe=1h&days=30").json()
    assert "baseline" in v["event_study"] and "events" in v["event_study"]
    assert client.get("/api/analysis/BTCUSDT?timeframe=2h").status_code == 422
    assert client.get("/api/analysis/detections?limit=5").status_code == 200
