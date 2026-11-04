"""Futures positioning context from Bybit public endpoints.

Sources (all documented Bybit V5 market endpoints):

* Open interest: ``/v5/market/open-interest`` (5min-1d buckets; the latest bucket can lag
  by up to one interval).
* Funding: ``/v5/market/funding/history`` plus the live rate in ``/v5/market/tickers``.
* Long/short account ratio: ``/v5/market/account-ratio``, the share of *accounts* net long
  or short, not position size. No weekly period.

Liquidation clusters and order-book liquidity are **not** derived here: Bybit's public
REST API has no historical liquidation map, and candles alone cannot reveal them.
Every source reports ``available``, ``unavailable`` (request failed) or ``not_supported``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from app.exchanges.bybit import BybitClient, ExchangeError
from app.timeutil import utcnow

log = logging.getLogger(__name__)

OI_INTERVAL = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1d"}
RATIO_PERIOD = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1d"}


async def fetch_futures(client: BybitClient, symbol: str, timeframe: str, ticker: dict[str, Any] | None
                        ) -> dict[str, Any]:
    out: dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, "fetched_at": utcnow().isoformat(),
                           "sources": {}}
    try:
        oi = await client.get_open_interest(symbol, OI_INTERVAL[timeframe], 50)
        out["open_interest"] = oi
        out["sources"]["open_interest"] = {"status": "available" if oi else "unavailable",
                                           "endpoint": "/v5/market/open-interest", "interval": OI_INTERVAL[timeframe],
                                           "note": "latest bucket may lag by up to one interval"}
    except ExchangeError as exc:
        out["sources"]["open_interest"] = {"status": "unavailable", "error": str(exc)}
    period = RATIO_PERIOD.get(timeframe)
    if period is None:
        out["sources"]["long_short_ratio"] = {"status": "not_supported", "note": f"no {timeframe} period on Bybit"}
    else:
        try:
            ratio = await client.get_account_ratio(symbol, period, 50)
            out["long_short"] = ratio
            out["sources"]["long_short_ratio"] = {"status": "available" if ratio else "unavailable",
                                                  "endpoint": "/v5/market/account-ratio", "period": period,
                                                  "note": "share of accounts, not position size"}
        except ExchangeError as exc:
            out["sources"]["long_short_ratio"] = {"status": "unavailable", "error": str(exc)}
    try:
        end = utcnow()
        fh = await client.get_funding_history(symbol, end - timedelta(days=7), end)
        out["funding_history"] = [{"time": r["funding_time"], "rate": r["rate"]} for r in fh]
        out["sources"]["funding"] = {"status": "available" if fh else "unavailable",
                                     "endpoint": "/v5/market/funding/history"}
    except ExchangeError as exc:
        out["sources"]["funding"] = {"status": "unavailable", "error": str(exc)}
    if ticker and ticker.get("funding_rate") is not None:
        out["funding_rate_now"] = ticker.get("funding_rate")
        out["next_funding_time"] = ticker.get("next_funding_time")
    out["sources"]["liquidations"] = {"status": "not_supported",
                                      "note": "no public historical liquidation data; not inferred from candles"}
    return out


def interpret_futures(data: dict[str, Any] | None, price_change_pct: float | None) -> dict[str, Any]:
    """Context statements (not signals). ``price_change_pct``: price change over the same window as OI."""
    if not data:
        return {"available": False, "items": [], "summary": "Futures data not requested."}
    items: list[dict[str, Any]] = []
    oi = data.get("open_interest") or []
    if len(oi) >= 10:
        a, b = oi[-10]["open_interest"], oi[-1]["open_interest"]
        chg = (b / a - 1) * 100 if a else 0.0
        if price_change_pct is not None and abs(chg) >= 1.0:
            if chg > 0 and price_change_pct > 0:
                txt, d = "rising open interest with rising price: new positions support the up-move", "BULLISH"
            elif chg > 0 and price_change_pct < 0:
                txt, d = "rising open interest with falling price: new positions support the down-move", "BEARISH"
            elif chg < 0 and price_change_pct > 0:
                txt, d = "falling open interest with rising price: the move is driven by short covering", "NEUTRAL"
            else:
                txt, d = "falling open interest with falling price: longs are closing", "NEUTRAL"
        else:
            txt, d = "open interest roughly flat", "NEUTRAL"
        items.append({"key": "oi_change", "direction": d, "value": round(chg, 2),
                      "text": f"Open interest {chg:+.1f}% over the last 10 periods; {txt}."})
    rate = data.get("funding_rate_now")
    if isinstance(rate, (int, float)):
        pct = rate * 100
        if pct >= 0.03:
            txt, d = "elevated positive funding: longs are paying, positioning is crowded long", "BEARISH"
        elif pct <= -0.01:
            txt, d = "negative funding: shorts are paying, positioning leans short", "BULLISH"
        else:
            txt, d = "funding near neutral", "NEUTRAL"
        items.append({"key": "funding", "direction": d, "value": round(pct, 4),
                      "text": f"Funding {pct:+.4f}% per interval; {txt} (contrarian context, not a signal)."})
    ls = data.get("long_short") or []
    if ls:
        br = ls[-1]["buy_ratio"]
        if br >= 0.65:
            txt, d = "a large majority of accounts are long (crowded; contrarian caution)", "BEARISH"
        elif br <= 0.35:
            txt, d = "a large majority of accounts are short (crowded; contrarian caution)", "BULLISH"
        else:
            txt, d = "accounts are fairly balanced", "NEUTRAL"
        items.append({"key": "long_short", "direction": d, "value": br,
                      "text": f"Long/short accounts {br:.0%}/{1 - br:.0%}; {txt}."})
    return {"available": bool(items), "items": items, "sources": data.get("sources", {}),
            "summary": " ".join(x["text"] for x in items) or "No futures context available."}
