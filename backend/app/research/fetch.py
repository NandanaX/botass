"""CLI: fill the research cache.  ``python -m app.research.fetch --days 400``"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from app.config import Settings
from app.exchanges.bybit import BybitClient
from app.research.data import ResearchData
from app.timeutil import utcnow

WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT"]


async def main(days: int, symbols: list[str], timeframes: list[str], minute_days: int) -> None:
    env = Settings()
    client = BybitClient(env.bybit_rest_url, env.bybit_max_requests_per_second, env.bybit_request_timeout)
    data = ResearchData(client)
    now = utcnow()
    try:
        for s in symbols:
            for tf in timeframes:
                df = await data.candles(s, tf, now - timedelta(days=days))
                print(s, tf, len(df), flush=True)
            if minute_days:
                df = await data.candles(s, "1m", now - timedelta(days=minute_days))
                print(s, "1m", len(df), flush=True)
            fu = await data.funding(s, now - timedelta(days=days))
            print(s, "funding", len(fu), flush=True)
    finally:
        await client.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--minute-days", type=int, default=2)
    ap.add_argument("--symbols", nargs="*", default=WATCHLIST)
    ap.add_argument("--timeframes", nargs="*", default=["4h", "1h", "15m"])
    a = ap.parse_args()
    asyncio.run(main(a.days, a.symbols, a.timeframes, a.minute_days))
