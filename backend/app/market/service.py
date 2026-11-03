"""Market data ingestion: closed-candle history (REST + DB) and live state (WebSocket)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from app.api.hub import EventHub
from app.exchanges.bybit import BybitClient, ExchangeError
from app.exchanges.bybit_ws import BybitStream
from app.market import store
from app.market.state import MarketState
from app.market.validation import filter_closed, find_gaps, validate_candles
from app.risk.sizing import InstrumentRules
from app.strategy.features import CANDLE_COLUMNS, candles_to_frame
from app.timeutil import last_closed_open_time, tf_delta, utcnow

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CandleSeries:
    symbol: str
    timeframe: str
    df: pd.DataFrame  # closed candles only, ascending
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    refreshed_at: datetime | None = None
    expected_last_open: datetime | None = None

    @property
    def last_open_time(self) -> datetime | None:
        return None if self.df.empty else self.df["open_time"].iloc[-1].to_pydatetime()

    @property
    def is_current(self) -> bool:
        return self.last_open_time is not None and self.last_open_time >= (self.expected_last_open or self.last_open_time)


class MarketDataService:
    def __init__(self, client: BybitClient, session_factory, hub: EventHub, history_limit: int = 1000,
                 stale_after_seconds: float = 30.0) -> None:
        self.client = client
        self.exchange = client.exchange
        self._session_factory = session_factory
        self.hub = hub
        self.history_limit = history_limit
        self.state = MarketState(stale_after_seconds=stale_after_seconds)
        self._series: dict[tuple[str, str], CandleSeries] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._attempted_gaps: set[tuple[str, str, datetime]] = set()
        self._stream: BybitStream | None = None
        self._last_ticker_broadcast: dict[str, float] = {}
        self.clock_offset_seconds = 0.0
        self._rules: dict[str, tuple[float, InstrumentRules]] = {}
        # Called with (symbol, ticker view) on every ticker update (price alerts).
        self.ticker_listeners: list = []
        self.rules_ttl_seconds = 6 * 3600

    # ------------------------------------------------------------------ clock

    async def sync_clock(self) -> None:
        try:
            server = await self.client.server_time()
            self.clock_offset_seconds = (server - utcnow()).total_seconds()
            log.info("exchange clock offset measured", extra={"offset_seconds": round(self.clock_offset_seconds, 3)})
        except ExchangeError as exc:
            log.warning("could not read exchange server time", extra={"error": str(exc)})

    def now(self) -> datetime:
        return utcnow() + pd.Timedelta(seconds=self.clock_offset_seconds).to_pytimedelta()

    # ------------------------------------------------------------------ closed candles

    async def get_closed_candles(self, symbol: str, timeframe: str, now: datetime | None = None) -> CandleSeries:
        """Most recent ``history_limit`` closed candles, refreshed incrementally from Bybit.

        Raises ExchangeError if there is no data at all and the exchange is unreachable.
        If the exchange is unreachable but cached data exists, the cached series is
        returned with ``is_current == False`` so callers can flag stale data.
        """
        key = (symbol, timeframe)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = now or self.now()
            expected_last = last_closed_open_time(now, timeframe)
            series = self._series.get(key)
            if series is None:
                async with self._session_factory()() as session:
                    df = await store.load_candles(session, self.exchange, symbol, timeframe, limit=self.history_limit)
                series = CandleSeries(symbol, timeframe, df)
            series.expected_last_open = expected_last
            if series.last_open_time is not None and series.last_open_time >= expected_last:
                self._series[key] = series
                return series

            step = tf_delta(timeframe)
            oldest_wanted = expected_last - step * (self.history_limit - 1)
            start = max(series.last_open_time, oldest_wanted) if series.last_open_time else oldest_wanted
            try:
                rows = await self.client.get_klines_range(symbol, timeframe, start, expected_last)
            except ExchangeError as exc:
                self.state.last_error = str(exc)
                if series.df.empty:
                    raise
                log.warning("using cached candles; refresh failed",
                            extra={"symbol": symbol, "timeframe": timeframe, "error": str(exc)})
                self._series[key] = series
                return series
            rows = filter_closed(rows, timeframe, now)
            series.df = self._merge(series.df, rows)
            new_rows = rows
            gaps = find_gaps(series.df["open_time"].tolist(), timeframe)
            fillable = [g for g in gaps if (symbol, timeframe, g[0]) not in self._attempted_gaps]
            for g_start, g_end in fillable:
                self._attempted_gaps.add((symbol, timeframe, g_start))
                log.warning("missing candles detected; backfilling",
                            extra={"symbol": symbol, "timeframe": timeframe, "from": g_start.isoformat(),
                                   "to": g_end.isoformat()})
                try:
                    fill = filter_closed(await self.client.get_klines_range(symbol, timeframe, g_start, g_end),
                                         timeframe, now)
                except ExchangeError as exc:
                    log.warning("gap backfill failed", extra={"error": str(exc)})
                    continue
                series.df = self._merge(series.df, fill)
                new_rows = new_rows + fill
            series.gaps = find_gaps(series.df["open_time"].tolist(), timeframe)
            series.refreshed_at = utcnow()
            if new_rows:
                async with self._session_factory()() as session:
                    await store.upsert_candles(session, self.exchange, symbol, timeframe, new_rows)
            self._series[key] = series
            return series

    def _merge(self, df: pd.DataFrame, rows: list[dict]) -> pd.DataFrame:
        if rows:
            df = candles_to_frame(pd.concat([df, pd.DataFrame(rows, columns=CANDLE_COLUMNS)], ignore_index=True)
                                  if not df.empty else pd.DataFrame(rows, columns=CANDLE_COLUMNS))
        return df.iloc[-self.history_limit:].reset_index(drop=True)

    async def load_history(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Closed candles for an arbitrary range (backtests): DB first, Bybit for missing data."""
        now = self.now()
        end = min(end, last_closed_open_time(now, timeframe))
        async with self._session_factory()() as session:
            df = await store.load_candles(session, self.exchange, symbol, timeframe, start, end)
        step = tf_delta(timeframe)
        expected = int((end - start) / step) + 1 if end >= start else 0
        if len(df) < expected:
            rows = filter_closed(await self.client.get_klines_range(symbol, timeframe, start, end), timeframe, now)
            rows = validate_candles(rows, timeframe, context=f"{symbol}:{timeframe}:history")
            async with self._session_factory()() as session:
                await store.upsert_candles(session, self.exchange, symbol, timeframe, rows)
                df = await store.load_candles(session, self.exchange, symbol, timeframe, start, end)
        return df

    async def load_funding(self, symbol: str, start: datetime, end: datetime) -> list[dict]:
        rows = await self.client.get_funding_history(symbol, start, end)
        async with self._session_factory()() as session:
            await store.upsert_funding(session, self.exchange, symbol, rows)
            return await store.load_funding(session, self.exchange, symbol, start, end)

    # ------------------------------------------------------------------ instrument rules

    async def get_instrument_rules(self, symbol: str) -> InstrumentRules | None:
        """Tick size, quantity step/limits, min notional, max leverage and the lowest-tier
        maintenance margin rate, cached for ``rules_ttl_seconds``. ``None`` if Bybit has no
        such instrument. Raises ``ExchangeError`` only when nothing is cached."""
        loop_now = asyncio.get_running_loop().time()
        cached = self._rules.get(symbol)
        if cached and loop_now - cached[0] < self.rules_ttl_seconds:
            return cached[1]
        try:
            info = await self.client.get_instrument(symbol)
            if info is None:
                return None
            try:
                tiers = await self.client.get_risk_tiers(symbol)
            except ExchangeError as exc:
                log.warning("risk tiers unavailable; using configured maintenance margin",
                            extra={"symbol": symbol, "error": str(exc)})
                tiers = []
            rules = InstrumentRules.from_bybit(info, tiers)
        except ExchangeError:
            if cached:
                log.warning("instrument rules refresh failed; using cached rules", extra={"symbol": symbol})
                return cached[1]
            raise
        self._rules[symbol] = (loop_now, rules)
        return rules

    # ------------------------------------------------------------------ live data

    def live_candle(self, symbol: str, timeframe: str) -> dict[str, Any] | None:
        return self.state.live_candles.get((symbol, timeframe))

    async def _on_ticker(self, symbol: str, data: dict[str, Any]) -> None:
        view = self.state.update_ticker(symbol, data, "websocket")
        self.state.ws_last_message_at = utcnow()
        await self._notify_ticker(symbol, view)
        loop_t = asyncio.get_running_loop().time()
        if loop_t - self._last_ticker_broadcast.get(symbol, 0) >= 1.0:
            self._last_ticker_broadcast[symbol] = loop_t
            await self.hub.broadcast("ticker", view)

    async def _notify_ticker(self, symbol: str, view: dict[str, Any]) -> None:
        for listener in self.ticker_listeners:
            try:
                await listener(symbol, view)
            except Exception:  # noqa: BLE001 - a listener must never break the stream
                log.exception("ticker listener failed", extra={"symbol": symbol})

    async def _on_kline(self, symbol: str, timeframe: str, candle: dict[str, Any]) -> None:
        self.state.ws_last_message_at = utcnow()
        self.state.live_candles[(symbol, timeframe)] = candle
        await self.hub.broadcast("kline", {"symbol": symbol, "timeframe": timeframe,
                                           **{**candle, "open_time": candle["open_time"].isoformat()}})

    async def _on_status(self, connected: bool, error: str | None) -> None:
        if not connected and self.state.ws_connected:
            self.state.ws_reconnects += 1
        self.state.ws_connected = connected
        if error:
            self.state.last_error = error
        await self.hub.broadcast("connection", self.state.connection_view())

    def start_stream(self, ws_url: str, symbols: list[str], timeframes: list[str]) -> asyncio.Task:
        self._stream = BybitStream(ws_url, self._on_ticker, self._on_kline, self._on_status)
        self._stream.set_subscriptions(symbols, timeframes)
        return asyncio.create_task(self._stream.run(), name="bybit-stream")

    def update_subscriptions(self, symbols: list[str], timeframes: list[str]) -> None:
        if self._stream is not None:
            self._stream.set_subscriptions(symbols, timeframes)

    def stop_stream(self) -> None:
        if self._stream is not None:
            self._stream.stop()

    async def poll_tickers(self, symbols: list[str]) -> None:
        """REST snapshot of tickers (used at startup and as a fallback when the stream is stale)."""
        try:
            for t in await self.client.get_tickers(symbols):
                view = self.state.update_ticker(t["symbol"], t, "rest")
                await self.hub.broadcast("ticker", view)
                await self._notify_ticker(t["symbol"], view)
        except ExchangeError as exc:
            self.state.last_error = str(exc)
            log.warning("ticker REST poll failed", extra={"error": str(exc)})

    async def ticker_fallback_loop(self, get_symbols, interval: float = 5.0) -> None:
        """Poll REST tickers whenever the WebSocket is down or a symbol's data went stale."""
        while True:
            symbols = get_symbols()
            stale = [s for s in symbols if not self.state.ws_connected or self.state.is_stale(s)]
            self.state.rest_fallback_active = bool(stale)
            if stale:
                await self.poll_tickers(stale)
            await asyncio.sleep(interval)
