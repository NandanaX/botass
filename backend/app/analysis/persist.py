"""Detections confirmed on a newly closed candle, as rows for ``pattern_detections``.

Ids are deterministic (symbol, timeframe, kind, name, candle, analysis version), so the
scanner can upsert them repeatedly without duplicates.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pandas as pd

from app.analysis.candles import detect_candles
from app.analysis.chart_patterns import pattern_log, resolve
from app.analysis.frame import AnalysisFrame
from app.analysis.structure import all_structure_events
from app.jsonutil import jsonable
from app.timeutil import utcnow


def _id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def detections_at(fr: AnalysisFrame, i: int, symbol: str, strategy_version: str) -> list[dict[str, Any]]:
    ver = fr.params.version
    t = pd.Timestamp(fr.iso(i)).to_pydatetime()
    rows: list[dict[str, Any]] = []

    def add(kind: str, name: str, direction: str, status: str, data: dict[str, Any]) -> None:
        rows.append({"id": _id(symbol, fr.timeframe, kind, name, fr.iso(i), ver), "symbol": symbol,
                     "timeframe": fr.timeframe, "kind": kind, "name": name, "direction": direction, "status": status,
                     "candle_time": t, "analysis_version": ver, "strategy_version": strategy_version,
                     "data": jsonable(data), "created_at": utcnow()})

    for e in all_structure_events(fr):
        if e.index == i:
            add("structure", e.kind, e.direction, "CONFIRMED", e.to_dict())
    for inst in pattern_log(fr):
        if inst.detected_at > i:
            break
        cp = resolve(fr, inst, i)
        if cp.resolved_index == i and cp.status == "CONFIRMED":
            add("chart_pattern", cp.name, cp.breakout_direction or cp.expected, "CONFIRMED", cp.to_dict())
        elif inst.detected_at == i:
            add("chart_pattern", cp.name, cp.expected, "DEVELOPING", cp.to_dict())
    for p in detect_candles(fr, i, None):
        if p.in_context and p.direction != "NEUTRAL":
            add("candlestick", p.name, p.direction, "CONFIRMED", p.to_dict())
    return rows
