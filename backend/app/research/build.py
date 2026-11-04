"""CLI: build the historical candidate set (``research_cache/candidates.pkl``).

``python -m app.research.build --warmup-days 60``
"""

from __future__ import annotations

import argparse
import pickle
import time

import pandas as pd

from app.research.candidates import generate_all
from app.research.data import DEFAULT_CACHE
from app.research.fetch import WATCHLIST
from app.research.simulate import CostModel
from app.settings_store import RuntimeSettings


def main(warmup_days: int, timeframes: list[str], workers: int) -> None:
    s = RuntimeSettings()
    btc = pd.read_pickle(DEFAULT_CACHE / "BTCUSDT_1h.pkl")
    start = btc["open_time"].iloc[0] + pd.Timedelta(days=warmup_days)
    t = time.time()
    rows = generate_all(WATCHLIST, timeframes, DEFAULT_CACHE, s.strategy, CostModel(), s.evaluation.max_bars,
                        start.value, workers)
    meta = {"start": start.isoformat(), "timeframes": timeframes, "symbols": WATCHLIST,
            "strategy_version": s.strategy.version, "max_bars": s.evaluation.max_bars,
            "costs": CostModel().to_dict(), "built_seconds": round(time.time() - t, 1),
            "data_end": btc["open_time"].iloc[-1].isoformat()}
    with open(DEFAULT_CACHE / "candidates.pkl", "wb") as fh:
        pickle.dump({"meta": meta, "rows": rows}, fh)
    print(len(rows), meta)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--warmup-days", type=int, default=60)
    ap.add_argument("--timeframes", nargs="*", default=["15m", "1h", "4h"])
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    main(a.warmup_days, a.timeframes, a.workers)
