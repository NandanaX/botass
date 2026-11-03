"""Persistence of historical candles and funding rates."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Candle, FundingRate
from app.db.session import upsert_stmt
from app.strategy.features import CANDLE_COLUMNS

_CHUNK = 500


async def upsert_candles(session: AsyncSession, exchange: str, symbol: str, timeframe: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    payload = [
        {"exchange": exchange, "symbol": symbol, "timeframe": timeframe, **{k: r[k] for k in CANDLE_COLUMNS}}
        for r in rows
    ]
    for i in range(0, len(payload), _CHUNK):
        stmt = upsert_stmt(session, Candle, payload[i: i + _CHUNK], ["exchange", "symbol", "timeframe", "open_time"],
                           ["open", "high", "low", "close", "volume", "turnover"])
        await session.execute(stmt)
    await session.commit()
    return len(payload)


async def load_candles(
    session: AsyncSession,
    exchange: str,
    symbol: str,
    timeframe: str,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Candles ascending by open time; ``limit`` keeps the most recent rows."""
    q = select(Candle).where(Candle.exchange == exchange, Candle.symbol == symbol, Candle.timeframe == timeframe)
    if start is not None:
        q = q.where(Candle.open_time >= start)
    if end is not None:
        q = q.where(Candle.open_time <= end)
    q = q.order_by(Candle.open_time.desc())
    if limit is not None:
        q = q.limit(limit)
    rows = (await session.execute(q)).scalars().all()
    data = [{k: getattr(r, k) for k in CANDLE_COLUMNS} for r in reversed(rows)]
    df = pd.DataFrame(data, columns=CANDLE_COLUMNS)
    if not df.empty:
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    return df


async def upsert_funding(session: AsyncSession, exchange: str, symbol: str, rows: list[dict]) -> None:
    if not rows:
        return
    payload = [{"exchange": exchange, "symbol": symbol, "funding_time": r["funding_time"], "rate": r["rate"]}
               for r in rows]
    for i in range(0, len(payload), _CHUNK):
        await session.execute(upsert_stmt(session, FundingRate, payload[i: i + _CHUNK],
                                          ["exchange", "symbol", "funding_time"], ["rate"]))
    await session.commit()


async def load_funding(session: AsyncSession, exchange: str, symbol: str, start: datetime, end: datetime) -> list[dict]:
    q = (
        select(FundingRate)
        .where(FundingRate.exchange == exchange, FundingRate.symbol == symbol,
               FundingRate.funding_time >= start, FundingRate.funding_time <= end)
        .order_by(FundingRate.funding_time)
    )
    return [{"funding_time": r.funding_time, "rate": r.rate} for r in (await session.execute(q)).scalars().all()]
