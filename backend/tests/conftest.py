from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("SCANNER_ENABLED", "false")
os.environ.setdefault("MARKET_STREAM_ENABLED", "false")
os.environ.pop("TELEGRAM_BOT_TOKEN", None)


def make_candles(n: int = 600, timeframe_minutes: int = 15, seed: int = 7, drift: float = 0.0004,
                 vol: float = 0.004, start: datetime | None = None, price: float = 100.0) -> pd.DataFrame:
    """Deterministic synthetic OHLCV random walk (clearly test data, never shown to users)."""
    rng = np.random.default_rng(seed)
    start = start or datetime(2026, 1, 1, tzinfo=UTC)
    rets = rng.normal(drift, vol, n)
    close = price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[price], close[:-1]])
    spread = np.abs(rng.normal(0, vol, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.uniform(50, 150, n)
    times = [start + timedelta(minutes=timeframe_minutes * i) for i in range(n)]
    return pd.DataFrame({"open_time": pd.to_datetime(times, utc=True), "open": open_, "high": high, "low": low,
                         "close": close, "volume": volume, "turnover": volume * close})


@pytest.fixture
def candles() -> pd.DataFrame:
    return make_candles()


@pytest.fixture
async def db(tmp_path):
    from app.db.models import Base
    from app.db.session import dispose_engine, init_engine

    engine = init_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await dispose_engine()


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def phase1_params(**overrides):
    """Phase 1 rule set expressed with Phase 2 parameters: range breakout, ATR stop,
    primary target 2R, no volume/body/extension/room filters, pullback disabled."""
    from app.strategy.params import StrategyParams

    base = {
        "breakout": {"level_source": "range", "min_rvol": 0, "min_body_ratio": 0, "max_extension_atr": 0},
        "pullback": {"enabled": False},
        "stops": {"method": "atr", "atr_multiplier": 1.5},
        "qualification": {"min_rr": 0, "min_net_rr": 0},
    }
    return StrategyParams.model_validate(_merge(base, overrides))
