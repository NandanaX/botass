from __future__ import annotations

import pytest
from sqlalchemy import select

from app.alerts.price import PriceAlertService, format_price_alert
from app.alerts.telegram import SendResult
from app.api.hub import EventHub
from app.db.models import Notification, PriceAlert
from app.db.session import session_factory
from app.timeutil import utcnow
from tests.test_api import client  # noqa: F401 - fixture


class FakeTelegram:
    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, text: str) -> SendResult:
        self.sent.append(text)
        return SendResult(True, 1)


async def test_alert_fires_once_when_price_crosses(db):
    tg = FakeTelegram()
    svc = PriceAlertService(session_factory, EventHub(), tg)  # type: ignore[arg-type]
    below = await svc.create("BTCUSDT", 76_650, current=76_800, note="15m support")
    above = await svc.create("BTCUSDT", 77_000, current=76_800)
    assert below["condition"] == "below" and above["condition"] == "above"  # chosen from the current price
    await svc.on_ticker("BTCUSDT", {"last_price": 76_700})
    assert tg.sent == []
    await svc.on_ticker("BTCUSDT", {"last_price": 76_640})
    await svc.on_ticker("BTCUSDT", {"last_price": 76_600})  # already fired: no second message
    assert len(tg.sent) == 1 and "76,650.00" in tg.sent[0] and "15m support" in tg.sent[0]
    await svc.on_ticker("ETHUSDT", {"last_price": 1e9})  # other symbols are unaffected
    await svc.on_ticker("BTCUSDT", {"last_price": 77_000})
    assert len(tg.sent) == 2
    async with session_factory()() as s:
        rows = {r.id: r for r in (await s.execute(select(PriceAlert))).scalars()}
        assert rows[below["id"]].status == "TRIGGERED" and rows[below["id"]].trigger_price == 76_640
        assert rows[below["id"]].notification_status == "SENT"
        kinds = [n.kind for n in (await s.execute(select(Notification))).scalars()]
        assert kinds == ["price", "price"]


async def test_cancel_and_validation(db):
    tg = FakeTelegram()
    svc = PriceAlertService(session_factory, EventHub(), tg)  # type: ignore[arg-type]
    a = await svc.create("BTCUSDT", 70_000, current=76_800)
    assert (await svc.cancel(a["id"]))["status"] == "CANCELLED"
    await svc.on_ticker("BTCUSDT", {"last_price": 60_000})
    assert tg.sent == []
    with pytest.raises(ValueError, match="already"):
        await svc.create("BTCUSDT", 80_000, current=76_800, condition="below")
    assert await svc.cancel(999) is None


async def test_alert_without_telegram_is_still_recorded(db):
    svc = PriceAlertService(session_factory, EventHub(), None)
    a = await svc.create("SOLUSDT", 95, current=91.8)
    await svc.on_ticker("SOLUSDT", {"last_price": 95.2})
    rows = await svc.list("TRIGGERED")
    assert rows[0]["id"] == a["id"] and rows[0]["notification_status"] == "NOT_CONFIGURED"


def test_price_alert_message_is_escaped_and_not_a_signal():
    msg = format_price_alert({"symbol": "BTCUSDT", "condition": "above", "price": 77_000.0, "note": "<b>x</b>"},
                             77_010.5, utcnow())
    assert "PRICE ALERT" in msg and "at or above <b>$77,000.00</b>" in msg and "&lt;b&gt;" in msg
    assert "not a trade signal" in msg


def test_price_alert_api(client):  # noqa: F811
    c = client.app.state.container
    assert client.post("/api/price-alerts", json={"symbol": "BTCUSDT", "price": 76650}).status_code == 503  # no tick
    c.market.state.update_ticker("BTCUSDT", {"last_price": 76_800.0}, "websocket")
    r = client.post("/api/price-alerts", json={"symbol": "BTCUSDT", "price": 76650, "note": "breakdown"})
    assert r.status_code == 201 and r.json()["condition"] == "below"
    assert client.post("/api/price-alerts", json={"symbol": "BTCUSDT", "price": 90000,
                                                  "condition": "below"}).status_code == 422
    assert client.post("/api/price-alerts", json={"symbol": "XRPUSDT", "price": 1}).status_code == 422  # not watched
    listed = client.get("/api/price-alerts?status=ACTIVE").json()
    assert [x["price"] for x in listed] == [76650]
    assert client.delete(f"/api/price-alerts/{listed[0]['id']}").json()["status"] == "CANCELLED"
    assert client.delete("/api/price-alerts/12345").status_code == 404
