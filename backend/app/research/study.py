"""CLI and helpers: run the full experiment study on the cached candidate set.

``python -m app.research.study`` -> ``research_cache/study_latest.json`` (plus a copy named
after the strategy version and build time, so every study stays reproducible).
"""

from __future__ import annotations

import json
import pickle
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.jsonutil import jsonable
from app.research.data import DEFAULT_CACHE
from app.research.experiments import enrich, run_study
from app.research.portfolio import CorrelationBook, PortfolioParams
from app.research.regime import regime_arrays
from app.settings_store import RuntimeSettings
from app.strategy.features import compute_features
from app.timeutil import tf_seconds

STUDY_FILE = "study_latest.json"


def load_candidates(cache: Path = DEFAULT_CACHE) -> dict[str, Any]:
    with open(cache / "candidates.pkl", "rb") as fh:
        return pickle.load(fh)


def btc_regimes(cache: Path, timeframes: list[str]) -> dict[str, tuple[np.ndarray, Any]]:
    params = RuntimeSettings().strategy
    out = {}
    for tf in timeframes:
        p = cache / f"BTCUSDT_{tf}.pkl"
        if p.exists():
            f = compute_features(pd.read_pickle(p), tf, params)
            out[tf] = (f.close_time, regime_arrays(f))
    return out


def correlation_book(cache: Path, symbols: list[str]) -> CorrelationBook:
    closes = {s: pd.read_pickle(cache / f"{s}_1h.pkl") for s in symbols if (cache / f"{s}_1h.pkl").exists()}
    return CorrelationBook.build(closes, PortfolioParams().corr_window_hours)


def main(cache: Path = DEFAULT_CACHE) -> dict[str, Any]:
    t = time.time()
    data = load_candidates(cache)
    rows, meta = data["rows"], data["meta"]
    enrich(rows, btc_regimes(cache, meta["timeframes"]))
    corr = correlation_book(cache, meta["symbols"])
    study = run_study(rows, corr, meta)
    study["meta"]["study_seconds"] = round(time.time() - t, 1)
    study["meta"]["generated_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    out = jsonable(study)
    (cache / STUDY_FILE).write_text(json.dumps(out), encoding="utf-8")
    tag = f"study_{meta['strategy_version'].replace('+', '_')}_{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M}.json"
    (cache / tag).write_text(json.dumps(out), encoding="utf-8")
    return out


if __name__ == "__main__":
    s = main()
    b = s["baseline"]["results"]
    print("baseline", {k: (b[k].get("n"), b[k].get("avg_r"), b[k].get("total_r")) for k in ("dev", "val", "test", "oos")})
    for h in s["hypotheses"]:
        r = h["results"]
        print(f"{h['key']:5} {h['verdict']:18} val {r['val'].get('n')}/{r['val'].get('avg_r')} "
              f"test {r['test'].get('n')}/{r['test'].get('avg_r')} oos tot {r['oos'].get('total_r')} "
              f"dd {r['oos'].get('max_drawdown_r')} p={h['bootstrap'].get('p_improvement')} | {h['title']}")
    print("ML", s["ml"]["verdict"], "combined", s["combined"]["keys"], s["combined"]["verdict"])
    print("seconds", s["meta"]["study_seconds"], tf_seconds("1h"))
