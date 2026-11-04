"""Loads closed candles, builds and caches ``AnalysisFrame``s, and runs the engine.

Frames are cached per (symbol, timeframe, last closed candle, parameter versions), so
nothing is recomputed between candle closes; the live price stream does not trigger any
recalculation. Futures data is cached for 60 s.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from app.analysis.candles import scan_candles
from app.analysis.chart_patterns import pattern_log, resolve
from app.analysis.engine import analyze_timeframe
from app.analysis.frame import AnalysisFrame, build_frame
from app.analysis.futures import fetch_futures
from app.analysis.levels import trendlines
from app.analysis.liquidity import equal_levels, fair_value_gaps, fibonacci
from app.analysis.report import build_report
from app.analysis.structure import all_structure_events, structure_at
from app.exchanges.bybit import ExchangeError
from app.jsonutil import jsonable
from app.market.service import MarketDataService
from app.settings_store import SettingsStore
from app.strategy.engine import evaluate_at
from app.strategy.features import Features
from app.strategy.setups import htf_index, zones_at
from app.timeutil import TIMEFRAMES

log = logging.getLogger(__name__)

TOP_DOWN = ["1w", "1d", "4h", "1h", "15m", "5m"]
ROLE = {"1w": "macro trend", "1d": "macro trend", "4h": "intermediate structure", "1h": "setup",
        "15m": "entry", "5m": "entry refinement (noisy, costly)"}


class AnalysisService:
    def __init__(self, market: MarketDataService, settings: SettingsStore) -> None:
        self.market = market
        self.settings = settings
        self._frames: dict[tuple[str, str], tuple[str, AnalysisFrame, Any]] = {}
        self._futures: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _key(self, last: str) -> str:
        s = self.settings.current
        return f"{last}|{s.strategy.version}|{s.analysis.version}"

    async def frame(self, symbol: str, tf: str) -> tuple[AnalysisFrame, Any]:
        series = await self.market.get_closed_candles(symbol, tf)
        if series.df.empty:
            raise LookupError(f"no candles for {symbol} {tf}")
        key = self._key(series.df["open_time"].iloc[-1].isoformat())
        lock = self._locks.setdefault((symbol, tf), asyncio.Lock())
        async with lock:
            cached = self._frames.get((symbol, tf))
            if cached and cached[0] == key:
                return cached[1], series
            s = self.settings.current
            fr = await asyncio.to_thread(build_frame, series.df, tf, s.strategy, s.analysis)
            await asyncio.to_thread(pattern_log, fr)  # warm the pattern event log
            self._frames[(symbol, tf)] = (key, fr, series)
            return fr, series

    async def futures(self, symbol: str, tf: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop().time()
        c = self._futures.get((symbol, tf))
        if c and loop - c[0] < 60:
            return c[1]
        data = await fetch_futures(self.market.client, symbol, tf, self.market.state.ticker_view(symbol))
        self._futures[(symbol, tf)] = (loop, data)
        return data

    async def analyze(self, symbol: str, tf: str, futures: bool = True) -> dict[str, Any]:
        s = self.settings.current
        sp = s.strategy
        fr, series = await self.frame(symbol, tf)
        i = len(fr) - 1
        decision = int(fr.f.close_time[i])
        htf_frames: dict[str, AnalysisFrame] = {}
        for htf in ("1h", "4h", "1d", "1w"):
            if TIMEFRAMES[htf] > TIMEFRAMES[tf] and (htf in sp.zones.htf_timeframes or htf in ("1d", "1w")
                                                     or (sp.mtf.enabled and htf in sp.mtf.timeframes)):
                try:
                    htf_frames[htf], _ = await self.frame(symbol, htf)
                except (ExchangeError, LookupError):
                    continue
        htf_zones = {}
        for htf, hf in htf_frames.items():
            j = htf_index(hf.f, decision)
            if j >= 0 and (htf in sp.zones.htf_timeframes or htf in ("1d", "1w")):
                htf_zones[htf] = zones_at(hf.f, j, sp, symbol, sp.zones.max_zones_per_side)
        try:
            rules = await self.market.get_instrument_rules(symbol)
        except ExchangeError:
            rules = None
        strategy_htf: dict[str, Features] = {k: v.f for k, v in htf_frames.items()}
        ev = await asyncio.to_thread(evaluate_at, fr.f, i, sp, strategy_htf, s.risk.sizing(), rules, True, symbol)
        zones = zones_at(fr.f, i, sp, symbol, sp.zones.max_zones_per_side)
        fut = None
        if futures:
            try:
                fut = await self.futures(symbol, tf)
            except Exception:  # noqa: BLE001 - futures context is optional
                log.warning("futures context unavailable", extra={"symbol": symbol})
        res = await asyncio.to_thread(analyze_timeframe, fr, i, sp, zones, htf_zones, ev, fut, True)
        res["symbol"] = symbol
        res["data_quality"] = {"current": series.is_current, "candles": len(fr),
                               "gaps": [[a.isoformat(), b.isoformat()] for a, b in series.gaps]}
        res["data_source"] = {"exchange": "Bybit", "market": "USDT perpetual (linear)",
                              "candles": "/v5/market/kline (closed candles only)",
                              "analysis_version": s.analysis.version, "strategy_version": sp.version,
                              "note": f"Bybit {symbol} {tf}, closed candles to {res['candle_open_time']}"}
        res["setups"] = [{"direction": x.direction, "strategy": x.strategy, "status": x.status,
                          "reasons": x.reasons} for x in ev.setups]
        if len(fr) < sp.warmup_bars:
            res["warning"] = f"Only {len(fr)} candles; indicators needing {sp.warmup_bars} bars are partly unavailable."
        return jsonable(res)

    async def mtf(self, symbol: str) -> dict[str, Any]:
        rows = []
        for tf in TOP_DOWN:
            try:
                fr, _ = await self.frame(symbol, tf)
            except (ExchangeError, LookupError) as exc:
                rows.append({"timeframe": tf, "role": ROLE[tf], "bias": "UNAVAILABLE", "trend": "UNKNOWN",
                             "structure": "UNKNOWN", "candle_open_time": None, "error": str(exc)})
                continue
            i = len(fr) - 1
            sp = self.settings.current.strategy
            zones = zones_at(fr.f, i, sp, symbol, sp.zones.max_zones_per_side)
            a = await asyncio.to_thread(analyze_timeframe, fr, i, sp, zones, {}, None, None, False)
            events = [e for e in a["structure_detail"]["events"] if e["kind"] != "WICK_BREAK"]
            rows.append({
                "timeframe": tf, "role": ROLE[tf], "bias": a["bias"], "alignment_score": a["alignment_score"],
                "trend": a["trend"], "structure": a["structure"], "momentum": a["momentum"],
                "volatility": a["volatility"], "close": a["close"], "candle_open_time": a["candle_open_time"],
                "nearest_support": a["nearest_support"], "nearest_resistance": a["nearest_resistance"],
                "last_event": events[-1] if events else None,
                "patterns": [p["name"] + f" ({p['status'].lower()})" for p in a["chart_patterns"]][-3:],
                "candles": [p["name"] for p in a["candlestick_patterns"]][-3:],
                "candles_available": len(fr),
            })
        dirs = {r["timeframe"]: (1 if r["bias"] == "BULLISH BIAS" else -1 if r["bias"] == "BEARISH BIAS" else 0)
                for r in rows}
        macro = dirs.get("1w", 0) + dirs.get("1d", 0)
        conflicts = []
        for lower in ("4h", "1h", "15m", "5m"):
            for higher in ("1w", "1d"):
                if dirs.get(lower) and dirs.get(higher) and dirs[lower] != dirs[higher]:
                    conflicts.append(f"{lower} bias {'bullish' if dirs[lower] > 0 else 'bearish'} conflicts with the "
                                     f"{higher} bias ({'bullish' if dirs[higher] > 0 else 'bearish'}).")
                    break
        agree = sum(1 for v in dirs.values() if v and (v > 0) == (macro > 0)) if macro else 0
        if macro > 0:
            summary = f"Macro (1W/1D) leans bullish; {agree}/{len(dirs)} timeframes agree."
        elif macro < 0:
            summary = f"Macro (1W/1D) leans bearish; {agree}/{len(dirs)} timeframes agree."
        else:
            summary = "Macro timeframes are neutral or disagree; no clear top-down direction."
        return jsonable({"symbol": symbol, "timeframes": rows, "summary": summary, "conflicts": conflicts})

    async def overlays(self, symbol: str, tf: str, limit: int = 500) -> dict[str, Any]:
        """Chart annotations for the displayed range (all detected at or before the last bar)."""
        fr, _ = await self.frame(symbol, tf)
        n = len(fr)
        i = n - 1
        lo = max(0, n - limit)
        st = structure_at(fr, i)
        events = [e.to_dict() | {"unix": fr.unix(e.index)} for e in all_structure_events(fr) if e.index >= lo]
        swings = [s.to_dict() | {"unix": fr.unix(s.index)} for s in st.swings]
        patt = []
        for inst in pattern_log(fr):
            if inst.detected_at < lo:
                continue
            cp = resolve(fr, inst, i)
            if cp.status in ("CONFIRMED", "DEVELOPING"):
                patt.append(cp.to_dict())
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for p in patt:
            latest[(p["name"], p["status"])] = p
        dev = [p for p in latest.values() if p["status"] == "DEVELOPING"]
        final = sorted((p for p in latest.values() if p["status"] != "DEVELOPING"), key=lambda p: p["detected_index"])
        candles = [p.to_dict() | {"unix": fr.unix(p.end_index)} for p in scan_candles(fr, max(lo, n - 200), i)
                   if p.in_context and p.direction != "NEUTRAL"]
        fib = fibonacci(fr, i)
        return jsonable({
            "symbol": symbol, "timeframe": tf, "candle_open_time": fr.iso(i),
            "structure_events": events, "swings": swings,
            "chart_patterns": final[-6:] + dev,
            "candlestick_patterns": candles[-40:],
            "trendlines": [t.to_dict() for t in trendlines(fr, i)],
            "fibonacci": fib.to_dict() if fib else None,
            "fair_value_gaps": fair_value_gaps(fr, i),
            "equal_levels": equal_levels(fr, i),
            "moving_averages": {name: [{"time": fr.unix(k), "value": float(arr[k])} for k in range(lo, n)
                                       if math.isfinite(arr[k])]
                                for name, arr in (("ema9", fr.ema9), ("ema100", fr.ema100), ("sma50", fr.sma50),
                                                  ("sma200", fr.f.sma_long))},
        })

    async def report(self, symbol: str, tf: str) -> dict[str, Any]:
        a = await self.analyze(symbol, tf)
        try:
            m = await self.mtf(symbol)
        except Exception:  # noqa: BLE001
            m = None
        from app.timeutil import utcnow
        rep = build_report(symbol, a, m, a["data_source"])
        rep["generated_at"] = utcnow().isoformat()
        return rep
