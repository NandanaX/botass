"""Evidence items and their aggregation into a bias and an alignment score.

* Each analyzer emits ``Evidence`` with a category, a direction (+1 bullish, -1 bearish,
  0 neutral/context), a strength in [0, 1] and a **group**. Items in one group describe the
  same underlying information (e.g. RSI, Stochastic and Stochastic RSI are all bounded
  oscillators of recent closes), so a group contributes once: the strength-weighted mean of
  its items. This prevents double counting of correlated indicators.
* Category score = mean of its directional groups, in [-1, 1]. Neutral items are reported
  but do not move the score.
* Net score = weighted mean of evaluable categories (``EvidenceWeights``).
* Bias: ``|net| >= bias_threshold`` gives BULLISH/BEARISH BIAS, else NEUTRAL. CONFLICTED when
  two categories of meaningful weight point strongly in opposite directions (each
  ``|score| >= 0.5``) and the net is not decisive; INSUFFICIENT DATA when fewer than
  ``min_evidence_categories`` categories could be evaluated.
* Alignment score (0-100) = ``|net| x 100``: how one-sided the evidence is. It is **not** a
  probability of success; historical hit rates live in the validation module.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.analysis.params import AnalysisParams

CATEGORIES = ("structure", "trend", "momentum", "levels", "price_action", "volume", "volatility", "futures")
CATEGORY_LABELS = {
    "structure": "Market structure", "trend": "Trend", "momentum": "Momentum", "levels": "Support / resistance",
    "price_action": "Price action", "volume": "Volume", "volatility": "Volatility", "futures": "Futures positioning",
}


@dataclass(slots=True)
class Evidence:
    category: str
    group: str
    key: str
    direction: int  # +1 / -1 / 0
    strength: float  # 0..1
    text: str
    time: str | None = None
    value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["direction_label"] = {1: "BULLISH", -1: "BEARISH", 0: "NEUTRAL"}[self.direction]
        return d


Bias = Literal["BULLISH BIAS", "BEARISH BIAS", "NEUTRAL", "CONFLICTED", "INSUFFICIENT DATA"]


def aggregate(items: list[Evidence], params: AnalysisParams) -> dict[str, Any]:
    weights = params.weights.model_dump()
    cats: dict[str, dict[str, list[Evidence]]] = defaultdict(lambda: defaultdict(list))
    for e in items:
        cats[e.category][e.group].append(e)
    breakdown: list[dict[str, Any]] = []
    scores: dict[str, float] = {}
    for c in CATEGORIES:
        groups = cats.get(c, {})
        vals = []
        for _g, evs in groups.items():
            directional = [e for e in evs if e.direction != 0 and e.strength > 0]
            if directional:
                tot = sum(e.strength for e in directional)
                vals.append(sum(e.direction * e.strength for e in directional) / max(tot, 1e-12)
                            * min(1.0, max(e.strength for e in directional)))
        evaluable = bool(vals)
        score = sum(vals) / len(vals) if vals else 0.0
        if evaluable:
            scores[c] = score
        breakdown.append({"category": c, "label": CATEGORY_LABELS[c], "weight": weights[c], "evaluable": evaluable,
                          "score": round(score, 3), "groups": len(groups),
                          "items": [e.to_dict() for evs in groups.values() for e in evs]})
    total_w = sum(weights[c] for c in scores)
    net = sum(weights[c] * s for c, s in scores.items()) / total_w if total_w > 0 else 0.0
    strong_pos = [c for c, s in scores.items() if s >= 0.5 and weights[c] >= 10]
    strong_neg = [c for c, s in scores.items() if s <= -0.5 and weights[c] >= 10]
    if len(scores) < params.min_evidence_categories:
        bias: Bias = "INSUFFICIENT DATA"
    elif strong_pos and strong_neg and abs(net) < 2 * params.bias_threshold:
        bias = "CONFLICTED"
    elif net >= params.bias_threshold:
        bias = "BULLISH BIAS"
    elif net <= -params.bias_threshold:
        bias = "BEARISH BIAS"
    else:
        bias = "NEUTRAL"
    sign = 1 if net > 0 else -1 if net < 0 else 0
    supporting = [e.to_dict() for e in items if sign and e.direction == sign]
    opposing = [e.to_dict() for e in items if sign and e.direction == -sign]
    conflicts = []
    if strong_pos and strong_neg:
        conflicts.append(f"{', '.join(CATEGORY_LABELS[c] for c in strong_pos)} lean bullish while "
                         f"{', '.join(CATEGORY_LABELS[c] for c in strong_neg)} lean bearish.")
    return {
        "bias": bias,
        "net_score": round(net, 3),
        "alignment_score": int(round(abs(net) * 100)) if bias not in ("INSUFFICIENT DATA",) else 0,
        "alignment_note": "Share of weighted evidence pointing one way (0-100). Not a probability of success.",
        "categories": breakdown,
        "supporting": supporting,
        "opposing": opposing,
        "conflicts": conflicts,
        "evaluable_categories": len(scores),
    }


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if not math.isfinite(x) else max(lo, min(hi, x))
