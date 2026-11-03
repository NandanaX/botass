"""Bybit V5 public WebSocket stream (linear) with reconnect and heartbeat.

Docs: https://bybit-exchange.github.io/docs/v5/ws/connect
Topics used: ``tickers.{symbol}`` (snapshot + deltas) and ``kline.{interval}.{symbol}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from app.exchanges.bybit import INTERVAL_TO_TF, INTERVALS, parse_ticker
from app.exchanges.ratelimit import backoff_delay
from app.timeutil import ms_to_dt

log = logging.getLogger(__name__)

TickerHandler = Callable[[str, dict[str, Any]], Awaitable[None]]
KlineHandler = Callable[[str, str, dict[str, Any]], Awaitable[None]]
StatusHandler = Callable[[bool, str | None], Awaitable[None]]

PING_INTERVAL = 20.0
RECV_TIMEOUT = 45.0
MAX_ARGS_PER_SUBSCRIBE = 10


def parse_ws_kline(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "open_time": ms_to_dt(item["start"]),
        "open": float(item["open"]),
        "high": float(item["high"]),
        "low": float(item["low"]),
        "close": float(item["close"]),
        "volume": float(item["volume"]),
        "turnover": float(item["turnover"]),
        "confirmed": bool(item.get("confirm", False)),
    }


class BybitStream:
    def __init__(
        self,
        url: str,
        on_ticker: TickerHandler,
        on_kline: KlineHandler,
        on_status: StatusHandler,
    ) -> None:
        self.url = url
        self.on_ticker = on_ticker
        self.on_kline = on_kline
        self.on_status = on_status
        self._symbols: list[str] = []
        self._timeframes: list[str] = []
        self._resubscribe = asyncio.Event()
        self._stopping = False

    def set_subscriptions(self, symbols: list[str], timeframes: list[str]) -> None:
        if symbols != self._symbols or timeframes != self._timeframes:
            self._symbols, self._timeframes = list(symbols), list(timeframes)
            self._resubscribe.set()

    def topics(self) -> list[str]:
        topics = [f"tickers.{s}" for s in self._symbols]
        topics += [f"kline.{INTERVALS[tf]}.{s}" for s in self._symbols for tf in self._timeframes]
        return topics

    def stop(self) -> None:
        self._stopping = True
        self._resubscribe.set()

    async def run(self) -> None:
        attempt = 0
        while not self._stopping:
            try:
                await self._session()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any failure
                await self.on_status(False, f"websocket error: {exc}")
                delay = max(1.0, backoff_delay(attempt, base=1.0, cap=60.0))
                attempt += 1
                log.warning("bybit websocket disconnected; reconnecting",
                            extra={"error": str(exc), "delay": round(delay, 1), "attempt": attempt})
                await asyncio.sleep(delay)

    async def _session(self) -> None:
        async with websockets.connect(self.url, ping_interval=None, open_timeout=15, close_timeout=5) as ws:
            self._resubscribe.clear()
            for i in range(0, len(self.topics()), MAX_ARGS_PER_SUBSCRIBE):
                await ws.send(json.dumps({"op": "subscribe", "args": self.topics()[i: i + MAX_ARGS_PER_SUBSCRIBE]}))
            await self.on_status(True, None)
            log.info("bybit websocket connected", extra={"topics": len(self.topics())})
            pinger = asyncio.create_task(self._ping(ws))
            try:
                while not self._stopping and not self._resubscribe.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                    except TimeoutError as exc:
                        raise ConnectionError("no data received within timeout") from exc
                    await self._handle(json.loads(raw))
            finally:
                pinger.cancel()
            await self.on_status(False, "resubscribing" if not self._stopping else "stopped")

    async def _ping(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await ws.send(json.dumps({"op": "ping"}))

    async def _handle(self, msg: dict[str, Any]) -> None:
        topic = msg.get("topic")
        if not topic:
            if msg.get("op") == "subscribe" and not msg.get("success", True):
                log.error("bybit subscription rejected", extra={"ret_msg": msg.get("ret_msg")})
            return
        data = msg.get("data")
        if topic.startswith("tickers.") and isinstance(data, dict):
            symbol = topic.split(".", 1)[1]
            # Deltas contain only changed fields; parse_ticker maps missing fields to None,
            # which MarketState.update_ticker ignores when merging.
            await self.on_ticker(symbol, parse_ticker({**data, "symbol": symbol}))
        elif topic.startswith("kline.") and isinstance(data, list):
            _, interval, symbol = topic.split(".", 2)
            tf = INTERVAL_TO_TF.get(interval)
            if tf is None:
                return
            for item in data:
                await self.on_kline(symbol, tf, parse_ws_kline(item))
