"""Historical data for research: closed Bybit candles and funding, cached on disk.

Research studies need far more history than the live scanner keeps (about 1000 candles),
so candles are cached per symbol/timeframe as pickled DataFrames under ``cache_dir``.
Only candles that had closed when they were fetched are stored; missing ranges are
fetched again from Bybit. Nothing here is synthetic: if Bybit has no data, the study
reports less history instead of inventing it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.exchanges.bybit import BybitClient
from app.market.validation import filter_closed, validate_candles
from app.strategy.features import CANDLE_COLUMNS, candles_to_frame
from app.timeutil import tf_delta, utcnow

log = logging.getLogger(__name__)

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "research_cache"


@dataclass
class ResearchData:
    client: BybitClient
    cache_dir: Path = DEFAULT_CACHE

    def _path(self, symbol: str, timeframe: str) -> Path:
        return self.cache_dir / f"{symbol}_{timeframe}.pkl"

    def _read(self, symbol: str, timeframe: str) -> pd.DataFrame:
        p = self._path(symbol, timeframe)
        if p.exists():
            return pd.read_pickle(p)
        return pd.DataFrame(columns=CANDLE_COLUMNS)

    def _write(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_pickle(self._path(symbol, timeframe))

    async def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime | None = None) -> pd.DataFrame:
        """Closed candles with ``start <= open_time <= end`` (ascending)."""
        now = utcnow()
        end = min(end or now, now)
        step = tf_delta(timeframe)
        df = self._read(symbol, timeframe)
        have_lo = df["open_time"].iloc[0] if len(df) else None
        have_hi = df["open_time"].iloc[-1] if len(df) else None
        ranges: list[tuple[datetime, datetime]] = []
        if have_lo is None:
            ranges.append((start, end))
        else:
            if start < have_lo:
                ranges.append((start, have_lo - step))
            if end > have_hi + step:
                ranges.append((have_hi + step, end))
        fetched = False
        for a, b in ranges:
            rows = await self.client.get_klines_range(symbol, timeframe, a, b)
            rows = validate_candles(filter_closed(rows, timeframe, now), timeframe, context=f"{symbol}:{timeframe}:research")
            if rows:
                df = candles_to_frame(pd.concat([df, pd.DataFrame(rows, columns=CANDLE_COLUMNS)], ignore_index=True)
                                      if len(df) else pd.DataFrame(rows, columns=CANDLE_COLUMNS))
                fetched = True
        if fetched:
            self._write(symbol, timeframe, df)
        if not len(df):
            return df
        mask = (df["open_time"] >= pd.Timestamp(start)) & (df["open_time"] <= pd.Timestamp(end))
        return df.loc[mask].reset_index(drop=True)

    async def funding(self, symbol: str, start: datetime, end: datetime | None = None) -> pd.DataFrame:
        """Funding events (``funding_time``, ``rate``), cached like candles."""
        end = end or utcnow()
        p = self.cache_dir / f"{symbol}_funding.pkl"
        df = pd.read_pickle(p) if p.exists() else pd.DataFrame(columns=["funding_time", "rate"])
        need = (not len(df) or df["funding_time"].iloc[0] > pd.Timestamp(start) + timedelta(hours=8)
                or df["funding_time"].iloc[-1] < pd.Timestamp(end) - timedelta(hours=8))
        if need:
            rows = await self.client.get_funding_history(symbol, start, end)
            if rows:
                new = pd.DataFrame(rows)
                new["funding_time"] = pd.to_datetime(new["funding_time"], utc=True)
                df = (pd.concat([df, new]) if len(df) else new).drop_duplicates("funding_time").sort_values(
                    "funding_time").reset_index(drop=True)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                df.to_pickle(p)
        if not len(df):
            return df
        mask = (df["funding_time"] >= pd.Timestamp(start)) & (df["funding_time"] <= pd.Timestamp(end))
        return df.loc[mask].reset_index(drop=True)
