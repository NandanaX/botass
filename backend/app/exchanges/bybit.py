"""Bybit V5 public market-data REST client (USDT perpetuals, ``category=linear``).

Docs: https://bybit-exchange.github.io/docs/v5/market/kline
No API key is used or required.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from app.exchanges.ratelimit import TokenBucket, backoff_delay
from app.market.validation import validate_candles
from app.timeutil import dt_to_ms, ms_to_dt, tf_delta

log = logging.getLogger(__name__)

EXCHANGE = "bybit"
CATEGORY = "linear"
INTERVALS = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D", "1w": "W"}
INTERVAL_TO_TF = {v: k for k, v in INTERVALS.items()}
KLINE_LIMIT = 1000
FUNDING_LIMIT = 200

# retCodes worth retrying: rate limit / server busy / timeouts.
RETRYABLE_RET_CODES = {10000, 10002, 10006, 10016, 10018}


class ExchangeError(Exception):
    def __init__(self, message: str, ret_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.ret_code = ret_code
        self.retryable = retryable


class BybitClient:
    exchange = EXCHANGE

    def __init__(
        self,
        base_url: str = "https://api.bybit.com",
        rate_per_second: float = 8.0,
        timeout: float = 10.0,
        max_retries: int = 5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                                   headers={"User-Agent": "CryptoSignalAI/1.0"})
        self._owns_client = client is None
        self._bucket = TokenBucket(rate_per_second)
        self.max_retries = max_retries

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await self._bucket.acquire()
            try:
                resp = await self._client.get(path, params=params)
                if resp.status_code in (403, 429) or resp.status_code >= 500:
                    raise ExchangeError(f"HTTP {resp.status_code} from Bybit {path}", retryable=True)
                if resp.status_code >= 400:
                    raise ExchangeError(f"HTTP {resp.status_code} from Bybit {path}: {resp.text[:200]}")
                body = resp.json()
                code = int(body.get("retCode", -1))
                if code != 0:
                    raise ExchangeError(f"Bybit retCode {code}: {body.get('retMsg')}", code,
                                        retryable=code in RETRYABLE_RET_CODES)
                return body.get("result") or {}
            except (httpx.TransportError, ValueError) as exc:
                last_exc = ExchangeError(f"network/decode error calling Bybit {path}: {exc}", retryable=True)
            except ExchangeError as exc:
                if not exc.retryable:
                    raise
                last_exc = exc
            if attempt < self.max_retries:
                delay = backoff_delay(attempt)
                log.warning("bybit request failed, retrying", extra={"path": path, "attempt": attempt + 1,
                                                                    "delay": round(delay, 2), "error": str(last_exc)})
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------ market data

    async def server_time(self) -> datetime:
        result = await self._get("/v5/market/time", {})
        return ms_to_dt(int(result["timeNano"]) // 1_000_000)

    @staticmethod
    def parse_kline(item: list[str]) -> dict[str, Any]:
        return {
            "open_time": ms_to_dt(item[0]),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
            "turnover": float(item[6]),
        }

    async def get_klines(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = KLINE_LIMIT,
    ) -> list[dict[str, Any]]:
        """Candles sorted ascending. May include the currently forming candle; callers
        must apply ``filter_closed`` before using them for confirmed signals."""
        params: dict[str, Any] = {"category": CATEGORY, "symbol": symbol, "interval": INTERVALS[timeframe],
                                  "limit": min(limit, KLINE_LIMIT)}
        if start is not None:
            params["start"] = dt_to_ms(start)
        if end is not None:
            params["end"] = dt_to_ms(end)
        result = await self._get("/v5/market/kline", params)
        rows = [self.parse_kline(item) for item in result.get("list", [])]  # Bybit returns newest first
        return validate_candles(rows, timeframe, context=f"{symbol}:{timeframe}")

    async def get_klines_range(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict]:
        """All candles with ``start <= open_time <= end`` using backwards pagination."""
        step = tf_delta(timeframe)
        out: dict[datetime, dict] = {}
        cursor_end = end
        while cursor_end >= start:
            batch = await self.get_klines(symbol, timeframe, start=start, end=cursor_end, limit=KLINE_LIMIT)
            if not batch:
                break
            for row in batch:
                if start <= row["open_time"] <= end:
                    out[row["open_time"]] = row
            earliest = batch[0]["open_time"]
            if earliest <= start or len(batch) < KLINE_LIMIT:
                break
            cursor_end = earliest - step
        return [out[k] for k in sorted(out)]

    async def get_tickers(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"category": CATEGORY}
        if symbols and len(symbols) == 1:
            params["symbol"] = symbols[0]
        result = await self._get("/v5/market/tickers", params)
        wanted = set(symbols or [])
        return [parse_ticker(t) for t in result.get("list", []) if not wanted or t.get("symbol") in wanted]

    async def get_funding_history(self, symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Historical funding rates (ascending) for ``start <= time <= end``."""
        out: dict[datetime, float] = {}
        cursor_end = end
        while cursor_end >= start:
            result = await self._get("/v5/market/funding/history", {
                "category": CATEGORY, "symbol": symbol, "startTime": dt_to_ms(start),
                "endTime": dt_to_ms(cursor_end), "limit": FUNDING_LIMIT})
            items = result.get("list", [])
            if not items:
                break
            times = []
            for it in items:
                t = ms_to_dt(it["fundingRateTimestamp"])
                times.append(t)
                if start <= t <= end:
                    out[t] = float(it["fundingRate"])
            earliest = min(times)
            if earliest <= start or len(items) < FUNDING_LIMIT:
                break
            cursor_end = ms_to_dt(dt_to_ms(earliest) - 1)
        return [{"funding_time": t, "rate": out[t]} for t in sorted(out)]

    async def get_instrument(self, symbol: str) -> dict[str, Any] | None:
        result = await self._get("/v5/market/instruments-info", {"category": CATEGORY, "symbol": symbol})
        items = result.get("list", [])
        return items[0] if items else None

    async def get_open_interest(self, symbol: str, interval: str, limit: int = 50) -> list[dict[str, Any]]:
        """Open interest history (``interval``: 5min, 15min, 30min, 1h, 4h, 1d), ascending."""
        result = await self._get("/v5/market/open-interest", {"category": CATEGORY, "symbol": symbol,
                                                                "intervalTime": interval, "limit": limit})
        rows = [{"time": ms_to_dt(r["timestamp"]), "open_interest": float(r["openInterest"])}
                for r in result.get("list", [])]
        return sorted(rows, key=lambda r: r["time"])

    async def get_account_ratio(self, symbol: str, period: str, limit: int = 50) -> list[dict[str, Any]]:
        """Long/short *account* ratio (share of accounts net long vs short), ascending."""
        result = await self._get("/v5/market/account-ratio", {"category": CATEGORY, "symbol": symbol,
                                                               "period": period, "limit": limit})
        rows = [{"time": ms_to_dt(r["timestamp"]), "buy_ratio": float(r["buyRatio"]),
                 "sell_ratio": float(r["sellRatio"])} for r in result.get("list", [])]
        return sorted(rows, key=lambda r: r["time"])

    async def get_risk_tiers(self, symbol: str) -> list[dict[str, Any]]:
        """Risk-limit tiers (maintenance margin rate per position size)."""
        result = await self._get("/v5/market/risk-limit", {"category": CATEGORY, "symbol": symbol})
        return list(result.get("list", []))


def _f(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_ticker(t: dict[str, Any]) -> dict[str, Any]:
    pct = _f(t.get("price24hPcnt"))
    next_funding = t.get("nextFundingTime")
    return {
        "symbol": t.get("symbol"),
        "last_price": _f(t.get("lastPrice")),
        "mark_price": _f(t.get("markPrice")),
        "index_price": _f(t.get("indexPrice")),
        "prev_price_24h": _f(t.get("prevPrice24h")),
        "change_24h_pct": pct * 100 if pct is not None else None,
        "high_24h": _f(t.get("highPrice24h")),
        "low_24h": _f(t.get("lowPrice24h")),
        "volume_24h": _f(t.get("volume24h")),
        "turnover_24h": _f(t.get("turnover24h")),
        "open_interest": _f(t.get("openInterest")),
        "open_interest_value": _f(t.get("openInterestValue")),
        "funding_rate": _f(t.get("fundingRate")),
        "bid_price": _f(t.get("bid1Price")),
        "ask_price": _f(t.get("ask1Price")),
        "next_funding_time": ms_to_dt(next_funding).isoformat() if next_funding and next_funding != "0" else None,
    }
