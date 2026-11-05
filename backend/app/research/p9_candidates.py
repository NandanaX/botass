"""Phase 9 candidate set: replays the production engine with the settings the live scanner uses.

A row holds decision-time information only (see ``app.planning.context``). Outcomes are simulated
later by the study, so no later candle can reach a decision input.

Rows are:

* ``QUALIFIED``: the setup that would create a live signal on that candle;
* ``ROOM_ONLY``: setups refused solely because an opposing zone was too close. They are never counted
  as signals; they exist to test that rule and the short lead.

``python -m app.research.p9_candidates``
"""

from __future__ import annotations

import pickle
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.planning.context import build_context
from app.planning.gate import ROOM_CODE
from app.research.data import DEFAULT_CACHE
from app.research.regime import regime_arrays
from app.settings_store import SETTINGS_KEY, RuntimeSettings
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams
from app.timeutil import TIMEFRAMES

CANDIDATE_FILE = "p9_candidates.pkl"
DB_PATH = Path(__file__).resolve().parents[2] / "cryptosignal.db"
WARMUP_DAYS = 60


def live_settings(db_path: Path = DB_PATH) -> RuntimeSettings:
    """The stored settings, read-only. Falls back to defaults when there is no database."""
    if not db_path.exists():
        return RuntimeSettings()
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute("select value from app_settings where key = ?", (SETTINGS_KEY,)).fetchone()
    finally:
        con.close()
    return RuntimeSettings.model_validate_json(row[0]) if row else RuntimeSettings()


def rows_for(symbol: str, tf: str, df: pd.DataFrame, htf_dfs: dict[str, pd.DataFrame], params: StrategyParams,
             start_ns: int) -> list[dict[str, Any]]:
    f = compute_features(df, tf, params)
    htf = {k: compute_features(v, k, params) for k, v in htf_dfs.items() if len(v)}
    ra = regime_arrays(f)
    first = max(int(np.searchsorted(f.open_time, start_ns)), params.warmup_bars)
    out: list[dict[str, Any]] = []
    for i in range(first, len(f) - 1):
        ev = evaluate_at(f, i, params, htf, context=False)
        for st in ev.setups:
            if st.status == "QUALIFIED":
                if st is not ev.setup:
                    continue  # the engine stores one signal per candle
                kind = "QUALIFIED"
            elif st.status == "REJECTED" and [r.get("code") for r in st.reasons] == [ROOM_CODE]:
                kind = "ROOM_ONLY"
            else:
                continue
            ctx = build_context(f, ra, i, st)
            out.append({"symbol": symbol, "timeframe": tf, "kind": kind, "index": i, "signal_ns": int(f.open_time[i]),
                        "decision_ns": int(f.close_time[i]), "direction": st.direction, "strategy": st.strategy,
                        "ctx": ctx.to_dict(), "atr_pct": ctx.atr_pct})
    return out


def _job(args: tuple) -> list[dict[str, Any]]:
    symbol, tf, cache_dir, htf_tfs, params_json, start_ns = args
    params = StrategyParams.model_validate_json(params_json)
    cache = Path(cache_dir)
    df = pd.read_pickle(cache / f"{symbol}_{tf}.pkl")
    htf_dfs = {h: pd.read_pickle(cache / f"{symbol}_{h}.pkl") for h in htf_tfs if TIMEFRAMES[h] > TIMEFRAMES[tf]}
    return rows_for(symbol, tf, df, htf_dfs, params, start_ns)


def build(cache: Path = DEFAULT_CACHE, workers: int = 6) -> dict[str, Any]:
    t0 = time.time()
    s = live_settings()
    params = s.strategy
    btc = pd.read_pickle(cache / "BTCUSDT_1h.pkl")
    start = btc["open_time"].iloc[0] + pd.Timedelta(days=WARMUP_DAYS)
    htf_tfs = sorted(set(params.zones.htf_timeframes) | set(params.mtf.timeframes), key=lambda t: TIMEFRAMES[t])
    jobs = [(sym, tf, str(cache), htf_tfs, params.model_dump_json(), start.value) for sym in s.watchlist for tf in s.timeframes]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(_job, jobs):
            rows.extend(r)
    rows.sort(key=lambda r: (r["decision_ns"], r["symbol"], r["timeframe"], r["kind"]))
    for k, r in enumerate(rows):
        r["id"] = k
    meta = {"strategy_version": params.version, "symbols": s.watchlist, "timeframes": s.timeframes, "start": start.isoformat(),
            "max_bars": s.evaluation.max_bars, "built_seconds": round(time.time() - t0, 1),
            "built_at": pd.Timestamp.now(tz="UTC").isoformat()}
    with open(cache / CANDIDATE_FILE, "wb") as fh:
        pickle.dump({"meta": meta, "rows": rows}, fh)
    return {"meta": meta, "rows": rows}


if __name__ == "__main__":
    d = build()
    q = [r for r in d["rows"] if r["kind"] == "QUALIFIED"]
    print(d["meta"], "rows", len(d["rows"]), "qualified", len(q))
