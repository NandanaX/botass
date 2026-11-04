"""``AnalysisFrame``: strategy ``Features`` plus the extra indicator arrays the analyzers use.

Built once per (closed-candle series, parameter set) and cached by the caller; analyzers
then read rows ``<= i`` only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.analysis import indicators as ind
from app.analysis.params import AnalysisParams
from app.indicators import core
from app.indicators.structure import find_pivots
from app.indicators.zones import PivotArrays
from app.strategy.features import Features, compute_features
from app.strategy.params import StrategyParams


@dataclass(slots=True)
class AnalysisFrame:
    f: Features
    ema9: np.ndarray
    ema100: np.ndarray
    sma50: np.ndarray
    adx: np.ndarray
    plus_di: np.ndarray
    minus_di: np.ndarray
    supertrend: np.ndarray
    supertrend_dir: np.ndarray
    stoch_k: np.ndarray
    stoch_d: np.ndarray
    stochrsi_k: np.ndarray
    stochrsi_d: np.ndarray
    bb_width: np.ndarray
    obv: np.ndarray
    vwap: np.ndarray | None
    pivots: PivotArrays  # structure pivots (analysis pivot window)
    major_pivots: PivotArrays  # larger pivots for chart patterns / Fibonacci
    params: AnalysisParams
    cache: dict[str, Any] = field(default_factory=dict)  # per-frame memo (pattern log, events)

    @property
    def timeframe(self) -> str:
        return self.f.timeframe

    def __len__(self) -> int:
        return len(self.f)

    def iso(self, i: int) -> str:
        return np.datetime_as_string(np.datetime64(int(self.f.open_time[i]), "ns"), unit="s") + "Z"

    def unix(self, i: int) -> int:
        return int(self.f.open_time[i] // 1_000_000_000)


def build_frame(df: pd.DataFrame, timeframe: str, strategy: StrategyParams, params: AnalysisParams,
                features: Features | None = None) -> AnalysisFrame:
    f = features or compute_features(df, timeframe, strategy)
    h, lo, c, v = f.high, f.low, f.close, f.volume
    adx_, pdi, mdi = ind.adx(h, lo, c, params.adx_period)
    st, st_dir = ind.supertrend(h, lo, c, f.atr, params.supertrend_mult)
    sk, sd = ind.stochastic(h, lo, c, params.stoch_period)
    srk, srd = ind.stoch_rsi(c, strategy.rsi_period, params.stoch_period)
    session = ind.vwap_session_seconds(timeframe)
    return AnalysisFrame(
        f=f,
        ema9=core.ema(c, 9),
        ema100=core.ema(c, 100),
        sma50=core.sma(c, 50),
        adx=adx_, plus_di=pdi, minus_di=mdi,
        supertrend=st, supertrend_dir=st_dir,
        stoch_k=sk, stoch_d=sd, stochrsi_k=srk, stochrsi_d=srd,
        bb_width=ind.bb_width(f.bb_upper, f.bb_mid, f.bb_lower),
        obv=ind.obv(c, v),
        vwap=ind.session_vwap(h, lo, c, v, f.open_time, session) if session else None,
        pivots=PivotArrays.from_pivots(find_pivots(h, lo, params.structure.pivot_window)),
        major_pivots=PivotArrays.from_pivots(find_pivots(h, lo, params.patterns.pivot_window)),
        params=params,
    )
