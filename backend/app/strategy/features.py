"""Feature computation shared by the live scanner and the backtester.

``compute_features`` converts *closed* candles into causal indicator arrays. Strategy
evaluation then reads row ``i`` of these arrays only (see ``app.strategy.engine``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.indicators import core
from app.indicators.structure import StructureArrays, find_pivots, structure_arrays
from app.indicators.zones import PivotArrays
from app.strategy.params import StrategyParams
from app.timeutil import tf_delta

CANDLE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "turnover"]


@dataclass(slots=True)
class Features:
    timeframe: str
    open_time: np.ndarray  # datetime64[ns, UTC] values as int64 nanoseconds
    close_time: np.ndarray  # int64 nanoseconds
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    ema_fast: np.ndarray
    ema_mid: np.ndarray
    ema_slow: np.ndarray
    sma_long: np.ndarray
    rsi: np.ndarray
    macd: np.ndarray
    macd_signal: np.ndarray
    macd_hist: np.ndarray
    atr: np.ndarray
    bb_upper: np.ndarray
    bb_mid: np.ndarray
    bb_lower: np.ndarray
    volume_ma: np.ndarray
    rvol: np.ndarray
    prior_high: np.ndarray  # highest high of previous N bars
    prior_low: np.ndarray  # lowest low of previous N bars
    structure: StructureArrays
    pivots: PivotArrays

    def __len__(self) -> int:
        return len(self.close)

    def time_at(self, i: int) -> pd.Timestamp:
        return pd.Timestamp(self.open_time[i], tz="UTC")

    def close_time_at(self, i: int) -> pd.Timestamp:
        return pd.Timestamp(self.close_time[i], tz="UTC")


def candles_to_frame(rows: list[dict] | pd.DataFrame) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows, columns=CANDLE_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)
    for col in CANDLE_COLUMNS[1:]:
        df[col] = df[col].astype(float)
    return df


def compute_features(df: pd.DataFrame, timeframe: str, params: StrategyParams) -> Features:
    """``df`` must contain only closed candles sorted ascending by ``open_time``."""
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)
    open_ns = df["open_time"].astype("int64").to_numpy() if len(df) else np.array([], dtype=np.int64)
    close_ns = open_ns + int(tf_delta(timeframe).total_seconds() * 1e9)
    macd_line, macd_sig, macd_hist = core.macd(c, params.macd_fast, params.macd_slow, params.macd_signal)
    bb_u, bb_m, bb_l = core.bollinger(c, params.bb_period, params.bb_std)
    vol_ma, rvol = core.relative_volume(v, params.volume_ma_period)
    return Features(
        timeframe=timeframe,
        open_time=open_ns,
        close_time=close_ns,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=v,
        ema_fast=core.ema(c, params.ema_fast),
        ema_mid=core.ema(c, params.ema_mid),
        ema_slow=core.ema(c, params.ema_slow),
        sma_long=core.sma(c, params.sma_long),
        rsi=core.rsi(c, params.rsi_period),
        macd=macd_line,
        macd_signal=macd_sig,
        macd_hist=macd_hist,
        atr=core.atr(h, lo, c, params.atr_period),
        bb_upper=bb_u,
        bb_mid=bb_m,
        bb_lower=bb_l,
        volume_ma=vol_ma,
        rvol=rvol,
        prior_high=core.rolling_max_prior(h, params.breakout_lookback),
        prior_low=core.rolling_min_prior(lo, params.breakout_lookback),
        structure=structure_arrays(h, lo, params.swing_strength),
        pivots=PivotArrays.from_pivots(find_pivots(h, lo, params.swing_strength)),
    )
