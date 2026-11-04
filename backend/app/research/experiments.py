"""Strategy experimentation framework.

Data: the historical candidate set (``candidates.py``): every QUALIFIED production signal
of the watchlist on 15m/1h/4h, plus research-only strategy families, each with
decision-time context and cost-aware hypothetical outcomes.

Periods (by decision time, all assets together, never shuffled):

* ``dev``  first 50%: hypotheses are explored and any threshold is chosen here only;
* ``val``  next 25%: first out-of-sample check;
* ``test`` final 25%: unseen until the final comparison.

Every hypothesis changes **one** thing relative to the baseline (the live rule: every
QUALIFIED signal, production stop, production target, overlapping signals allowed). All
results are net of fees, slippage and funding (``simulate.py``), in R per trade.

Acceptance (``verdict``) of a change, judged on val and test only:

* ``INSUFFICIENT_DATA``: fewer than 30 trades in val or in test.
* Expectancy must improve in **both** val and test. A change with nothing fitted (a fixed
  rule) must also improve dev, which is then just another independent period: a rule
  that helps in two periods and hurts in the third is unstable, not validated.
* Day-block bootstrap of the out-of-sample (val + test) difference in average net R
  (days are resampled, so clustered same-day signals are not treated as independent):
  ``P(improvement) >= 0.95`` -> ``VALIDATED``; ``>= 0.80`` -> ``PROMISING``.
* Otherwise ``NOT_VALIDATED``.

A validated *improvement* is not the same as a *profitable* strategy: ``positive_expectancy``
reports separately whether the variant's own out-of-sample average net R is above zero with
its bootstrap 95% interval excluding zero. Nothing here changes the production engine.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.research import ml
from app.research.metrics import MIN_TRADES, by_group, monthly, summarize
from app.research.portfolio import CorrelationBook, PortfolioParams, independent_baseline, run_portfolio
from app.research.regime import RegimeArrays, regime_alignment

PERIODS = ("dev", "val", "test")
DAY_NS = 86_400_000_000_000


# ----------------------------------------------------------------------------- data prep

def enrich(rows: list[dict[str, Any]], btc_regimes: dict[str, tuple[np.ndarray, RegimeArrays]]) -> None:
    """Add decision-time context that needs other assets: BTC regime alignment, 4h regime
    alignment and the size of the same-direction signal cluster (other symbols within
    +-60 minutes, counted from decision times only)."""
    for c in rows:
        c["regime_alignment"] = regime_alignment(c["regime"]["regime"], c["direction"])
        h4 = c.get("htf_regime", {}).get("4h")
        c["htf_alignment_4h"] = regime_alignment(h4, c["direction"]) if h4 else "NEUTRAL"
        b = btc_regimes.get(c["timeframe"])
        c["btc_alignment"] = "SELF" if c["symbol"] == "BTCUSDT" else "NEUTRAL"
        if b is not None and c["symbol"] != "BTCUSDT":
            close_ns, ra = b
            k = int(np.searchsorted(close_ns, c["decision_ns"], side="right")) - 1
            if k >= 0:
                c["btc_regime"] = str(ra.label[k])
                c["btc_alignment"] = regime_alignment(str(ra.label[k]), c["direction"])
    prod = [c for c in rows if c["family"] == "production"]
    times = np.array([c["decision_ns"] for c in prod])
    for c in prod:
        lo = np.searchsorted(times, c["decision_ns"] - 3_600_000_000_000, side="left")
        hi = np.searchsorted(times, c["decision_ns"] + 3_600_000_000_000, side="right")
        c["cluster_size"] = sum(1 for o in prod[lo:hi] if o["direction"] == c["direction"]
                                and o["symbol"] != c["symbol"])
        c["cluster_prior"] = sum(1 for o in prod[lo:hi] if o["direction"] == c["direction"]
                                 and o["symbol"] != c["symbol"] and o["decision_ns"] <= c["decision_ns"]
                                 and o is not c)


def to_trade(c: dict[str, Any], method: str = "C_hybrid") -> dict[str, Any] | None:
    o = (c.get("outcomes") or {}).get(method)
    if not o or o.get("status") != "FILLED" or o.get("net_r") is None:
        return None
    t = {k: v for k, v in c.items() if k != "outcomes"}
    t.update({"method": method, "net_r": o["net_r"], "gross_r": o["gross_r"], "cost_r": o["cost_r"],
              "funding_r": o["funding_r"], "reason": o["reason"], "mfe_r": o["mfe_r"], "mae_r": o["mae_r"],
              "post_exit_target": o["post_exit_target"], "exit_close_ns": o["exit_close_ns"],
              "bars_held": o["bars_held"], "stop_atr": o["stop_atr"]})
    return t


@dataclass
class Split:
    start: int
    t50: int
    t75: int
    end: int

    def period(self, ns: int) -> str:
        return "dev" if ns < self.t50 else "val" if ns < self.t75 else "test"

    def to_dict(self) -> dict[str, str]:
        f = lambda x: pd.Timestamp(x, tz="UTC").isoformat()  # noqa: E731
        return {"dev": f"{f(self.start)} -> {f(self.t50)}", "val": f"{f(self.t50)} -> {f(self.t75)}",
                "test": f"{f(self.t75)} -> {f(self.end)}"}


def make_split(rows: list[dict[str, Any]]) -> Split:
    a = min(c["decision_ns"] for c in rows)
    b = max(c["decision_ns"] for c in rows)
    return Split(a, a + (b - a) // 2, a + 3 * (b - a) // 4, b)


# ----------------------------------------------------------------------------- statistics

def by_period(trades: list[dict[str, Any]], sp: Split) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {p: [] for p in PERIODS}
    for t in trades:
        out[sp.period(t["decision_ns"])].append(t)
    return out


def report(trades: list[dict[str, Any]], sp: Split, detail: bool = False) -> dict[str, Any]:
    per = by_period(trades, sp)
    oos = per["val"] + per["test"]
    out: dict[str, Any] = {p: summarize(per[p]) for p in PERIODS}
    out["oos"] = summarize(oos)
    s = out["oos"]
    out["positive_expectancy"] = bool(s.get("avg_r_ci95") and s["avg_r_ci95"][0] > 0)
    if detail:
        out["oos_by_symbol"] = by_group(oos, lambda t: t["symbol"])
        out["oos_by_timeframe"] = by_group(oos, lambda t: t["timeframe"])
        out["monthly_r"] = monthly(trades)
    return out


def day_block_bootstrap(base: list[dict[str, Any]], var: list[dict[str, Any]], reps: int = 2000, seed: int = 11
                        ) -> dict[str, Any]:
    """P(avg net R of ``var`` > avg net R of ``base``) resampling whole UTC days."""
    days = sorted({t["decision_ns"] // DAY_NS for t in base} | {t["decision_ns"] // DAY_NS for t in var})
    if not days or not var:
        return {"p_improvement": None, "delta_ci95": None}
    idx = {d: k for k, d in enumerate(days)}
    D = len(days)
    bs, bn, vs, vn = np.zeros(D), np.zeros(D), np.zeros(D), np.zeros(D)
    for t in base:
        k = idx[t["decision_ns"] // DAY_NS]
        bs[k] += t["net_r"]
        bn[k] += 1
    for t in var:
        k = idx[t["decision_ns"] // DAY_NS]
        vs[k] += t["net_r"]
        vn[k] += 1
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, D, size=(reps, D))
    bm = bs[pick].sum(1) / np.maximum(bn[pick].sum(1), 1)
    vm = vs[pick].sum(1) / np.maximum(vn[pick].sum(1), 1)
    delta = vm - bm
    return {"p_improvement": round(float((delta > 0).mean()), 4),
            "delta_ci95": [round(float(np.quantile(delta, 0.025)), 4), round(float(np.quantile(delta, 0.975)), 4)],
            "days": D}


COST_PCT = 2 * 0.055 + 0.02 + 0.05  # fees both sides + entry slippage + stop slippage (percent of price)


def est_cost_r(t: dict[str, Any], cost_pct: float = COST_PCT) -> float:
    """Round-trip cost of a stopped-out trade in R, known at decision time."""
    stop_pct = t["stop_atr"] * t["atr_pct"]
    return cost_pct / stop_pct if stop_pct > 0 else 99.0


def drawdown_vs_random(base: list[dict[str, Any]], var: list[dict[str, Any]], reps: int = 500, seed: int = 5
                       ) -> dict[str, Any]:
    """Is the variant's max drawdown lower than that of random subsets of the baseline with
    the same number of trades? (Fewer trades alone lower drawdown.)"""
    if not var or len(var) >= len(base):
        return {"percentile": None, "note": "no random-subset comparison (variant not smaller than baseline)"}
    rng = np.random.default_rng(seed)
    dd = summarize(var)["max_drawdown_r"]
    sims = []
    for _ in range(reps):
        pick = sorted(rng.choice(len(base), size=len(var), replace=False))
        sims.append(summarize([base[k] for k in pick])["max_drawdown_r"])
    pct = float((np.array(sims) <= dd).mean())
    return {"variant_max_dd_r": dd, "random_subset_median_dd_r": round(float(np.median(sims)), 3),
            "percentile": round(pct, 4),
            "note": (f"OOS drawdown {dd:.1f}R vs {np.median(sims):.1f}R median for random subsets of the same size "
                     f"(percentile {pct:.2f}; < 0.10 means the limits reduce drawdown beyond trading less)")}


def verdict(base_rep: dict[str, Any], var_rep: dict[str, Any], boot: dict[str, Any], fitted: bool = False
            ) -> tuple[str, list[str]]:
    notes = []
    nv, nt = var_rep["val"].get("n", 0), var_rep["test"].get("n", 0)
    if nv < MIN_TRADES or nt < MIN_TRADES:
        return "INSUFFICIENT_DATA", [f"val n={nv}, test n={nt}; need >= {MIN_TRADES} in each"]
    up_val = var_rep["val"]["avg_r"] > base_rep["val"]["avg_r"]
    up_test = var_rep["test"]["avg_r"] > base_rep["test"]["avg_r"]
    notes.append(f"val {base_rep['val']['avg_r']:+.3f}R -> {var_rep['val']['avg_r']:+.3f}R; "
                 f"test {base_rep['test']['avg_r']:+.3f}R -> {var_rep['test']['avg_r']:+.3f}R per trade")
    if var_rep["oos"]["total_r"] < base_rep["oos"]["total_r"] and var_rep["oos"]["avg_r"] > base_rep["oos"]["avg_r"]:
        notes.append("higher expectancy per trade but lower total R (fewer trades)")
    if var_rep["oos"].get("max_drawdown_r") is not None and base_rep["oos"].get("max_drawdown_r") is not None:
        notes.append(f"OOS max drawdown {base_rep['oos']['max_drawdown_r']:.1f}R -> {var_rep['oos']['max_drawdown_r']:.1f}R")
    if not (up_val and up_test):
        return "NOT_VALIDATED", notes + ["expectancy did not improve in both validation and test"]
    if not fitted and var_rep["dev"].get("n", 0) >= MIN_TRADES and var_rep["dev"]["avg_r"] <= base_rep["dev"]["avg_r"]:
        return "NOT_VALIDATED", notes + [f"unstable: dev {base_rep['dev']['avg_r']:+.3f}R -> {var_rep['dev']['avg_r']:+.3f}R "
                                         "(worse in the development period although nothing was fitted on it)"]
    p = boot.get("p_improvement") or 0
    if p >= 0.95:
        return "VALIDATED", notes
    if p >= 0.80:
        return "PROMISING", notes + [f"bootstrap P(improvement) {p:.2f} < 0.95"]
    return "NOT_VALIDATED", notes + [f"bootstrap P(improvement) {p:.2f} < 0.80"]


# ----------------------------------------------------------------------------- the study

@dataclass
class Hypothesis:
    key: str
    title: str
    hypothesis: str
    change: str
    params: dict[str, Any]
    build: Callable[[], list[dict[str, Any]]]
    chosen_on_dev: dict[str, Any] | None = None
    keep: Callable[[dict[str, Any]], bool] | None = None  # decision-time filter predicate, if a filter
    fitted: bool = False  # True when a parameter was chosen on dev


def _choose_on_dev(base: list[dict[str, Any]], sp: Split, grid: list[Any], make: Callable[[Any], Callable]
                   ) -> tuple[Any, list[dict[str, Any]]]:
    """Pick the grid value with the best dev expectancy (>= 30 dev trades)."""
    dev = [t for t in base if sp.period(t["decision_ns"]) == "dev"]
    table = []
    best, best_v = None, -1e9
    for g in grid:
        keep = make(g)
        sub = [t for t in dev if keep(t)]
        s = summarize(sub)
        row = {"value": g, "dev_n": s["n"], "dev_avg_r": s.get("avg_r")}
        for per in ("val", "test"):  # sensitivity only; never used for the choice
            ss = summarize([t for t in base if sp.period(t["decision_ns"]) == per and keep(t)])
            row[f"{per}_n"], row[f"{per}_avg_r"] = ss["n"], ss.get("avg_r")
        table.append(row)
        if s["n"] >= MIN_TRADES and s["avg_r"] > best_v:
            best, best_v = g, s["avg_r"]
    return best, table


def regime_selection(train: list[dict[str, Any]], min_n: int = MIN_TRADES) -> dict[tuple[str, str], bool]:
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    for t in train:
        cells[(t["strategy"], t["regime"]["regime"])].append(t["net_r"])
    return {k: (len(v) >= min_n and float(np.mean(v)) > 0) for k, v in cells.items()}


def walk_forward(trades: list[dict[str, Any]], folds: int, fit: Callable[[list], Callable[[dict], bool]]
                 ) -> list[dict[str, Any]]:
    """Expanding-window walk-forward: fit on everything before fold k, apply to fold k."""
    if not trades:
        return []
    ts = sorted(t["decision_ns"] for t in trades)
    edges = [ts[0] + (ts[-1] - ts[0]) * k // folds for k in range(folds + 1)]
    edges[-1] += 1
    out = []
    for k in range(1, folds):
        train = [t for t in trades if t["exit_close_ns"] is not None and t["exit_close_ns"] < edges[k] - DAY_NS]
        test = [t for t in trades if edges[k] <= t["decision_ns"] < edges[k + 1]]
        keep = fit(train)
        kept = [t for t in test if keep(t)]
        out.append({"fold": k, "test_from": pd.Timestamp(edges[k], tz="UTC").isoformat(),
                    "test_to": pd.Timestamp(edges[k + 1], tz="UTC").isoformat(),
                    "baseline": summarize(test), "variant": summarize(kept)})
    return out


def run_study(rows: list[dict[str, Any]], corr: CorrelationBook | None, meta: dict[str, Any]) -> dict[str, Any]:
    prod = [c for c in rows if c["family"] == "production"]
    sp = make_split(prod)
    base = [t for t in (to_trade(c) for c in prod) if t is not None]
    base.sort(key=lambda t: t["decision_ns"])
    for t in base:
        t["est_cost_r"] = est_cost_r(t)
    base_rep = report(base, sp, detail=True)
    invalidated = sum(1 for c in prod if (c["outcomes"].get("C_hybrid") or {}).get("status") == "INVALIDATED")
    open_ = sum(1 for c in prod if (c["outcomes"].get("C_hybrid") or {}).get("status") == "OPEN")

    def flt(keep: Callable[[dict], bool]) -> Callable[[], list[dict[str, Any]]]:
        def build() -> list[dict[str, Any]]:
            return [t for t in base if keep(t)]
        build.keep = keep  # type: ignore[attr-defined]
        return build

    hyps: list[Hypothesis] = []
    hyps.append(Hypothesis("B1", "One position per symbol/timeframe",
                           "Repeated signals while an earlier one is still open add correlated risk, not edge.",
                           "skip a signal while the previous signal on the same symbol and timeframe is open "
                           "(the backtester's rule)", {}, lambda: independent_baseline(base)))
    hyps.append(Hypothesis("H1a", "Late-entry filter (EXTENDED + EXHAUSTION_RISK)",
                           "Entries far from the short-term mean or exhausted have lower expectancy.",
                           "reject entries classified EXTENDED or EXHAUSTION_RISK", {},
                           flt(lambda t: t["entry_class"] not in ("EXTENDED", "EXHAUSTION_RISK"))))
    hyps.append(Hypothesis("H1b", "Exhaustion filter only", "Only exhausted entries are harmful.",
                           "reject entries classified EXHAUSTION_RISK", {},
                           flt(lambda t: t["entry_class"] != "EXHAUSTION_RISK")))
    ext_best, ext_table = _choose_on_dev(base, sp, [0.75, 1.0, 1.25, 1.5, 2.0, 2.5],
                                         lambda k: (lambda t: (t["entry"]["ext_fast_atr"] or 0) < k))
    hyps.append(Hypothesis("H1c", "Extension threshold (chosen on dev)",
                           "There is an extension beyond EMA-fast above which entries should wait.",
                           f"reject entries with extension >= {ext_best} ATR (threshold chosen on dev only)",
                           {"threshold_atr": ext_best},
                           flt(lambda t, k=ext_best: k is None or (t["entry"]["ext_fast_atr"] or 0) < k),
                           {"grid": ext_table, "chosen": ext_best}, fitted=True))
    room_best, room_table = _choose_on_dev(base, sp, [0.5, 1.0, 1.5, 2.0],
                                           lambda k: (lambda t: not (t["entry"]["swing_room_atr"] is not None
                                                                     and 0 <= t["entry"]["swing_room_atr"] < k)))
    hyps.append(Hypothesis("H1d", "Nearby opposing swing filter (chosen on dev)",
                           "Entries close to the last opposing swing (support for shorts) have little room.",
                           f"reject entries within {room_best} ATR of the last opposing confirmed swing",
                           {"min_swing_room_atr": room_best},
                           flt(lambda t, k=room_best: k is None or not (t["entry"]["swing_room_atr"] is not None
                                                                        and 0 <= t["entry"]["swing_room_atr"] < k)),
                           {"grid": room_table, "chosen": room_best}, fitted=True))
    for m, title in (("A_atr", "ATR stop (1.5 ATR)"), ("B_structure", "Structure stop (no buffer)"),
                     ("D_noise", "Volatility/noise-adaptive stop")):
        hyps.append(Hypothesis(f"H2{m[0]}", title, f"{title} reduces premature exits versus the hybrid stop.",
                               f"replace the production hybrid stop with {title}; target 2R; same entries; "
                               "risk per trade unchanged (position size recalculated)", {"method": m},
                               lambda m=m: sorted([t for t in (to_trade(c, m) for c in prod) if t is not None],
                                                  key=lambda t: t["decision_ns"])))
    mult_methods = {1.0: "A_atr_1", 1.5: "A_atr", 2.0: "A_atr_2", 2.5: "A_atr_2.5"}
    mult_trades = {m: sorted([t for t in (to_trade(c, k) for c in prod) if t is not None], key=lambda t: t["decision_ns"])
                   for m, k in mult_methods.items()}
    mult_table, best_m, best_v = [], None, -1e9
    for m, tr in mult_trades.items():
        sd = summarize([t for t in tr if sp.period(t["decision_ns"]) == "dev"])
        mult_table.append({"value": m, "dev_n": sd["n"], "dev_avg_r": sd.get("avg_r")})
        if sd["n"] >= MIN_TRADES and sd["avg_r"] > best_v:
            best_m, best_v = m, sd["avg_r"]
    hyps.append(Hypothesis("H2E", "ATR stop, multiplier chosen on dev",
                           "Wider volatility stops reduce the share of each trade lost to fees and slippage and to "
                           "normal noise; the multiplier is chosen on dev only.",
                           f"ATR stop at {best_m:g} x ATR with a 2R target (chosen on dev from 1.0/1.5/2.0/2.5); "
                           "same entries; risk per trade unchanged",
                           {"method": mult_methods[best_m], "atr_multiplier": best_m},
                           lambda m=best_m: mult_trades[m],
                           {"grid": mult_table, "chosen": best_m,
                            "note": "the chosen value is the edge of the grid" if best_m == max(mult_methods) else None},
                           fitted=True))
    pp = PortfolioParams()
    hyps.append(Hypothesis("H3", "Portfolio & correlation limits", "Limiting clustered, correlated, same-direction "
                           "exposure reduces drawdown without destroying expectancy.",
                           "apply PortfolioParams defaults", pp.model_dump(),
                           lambda: run_portfolio(base, pp, corr)[0]))
    hyps.append(Hypothesis("H3b", "Correlation limit only", "Correlated same-direction positions are the main risk.",
                           "only one_per_symbol + max_correlated (0.7)", {},
                           lambda: run_portfolio(base, PortfolioParams(max_positions=0, max_total_risk_r=0,
                                                                       max_same_direction=0, max_daily_loss_r=0,
                                                                       max_consecutive_losses=0), corr)[0]))
    hyps.append(Hypothesis("H4", "Full higher-timeframe confirmation",
                           "Requiring every higher timeframe's EMA trend to agree improves signal quality.",
                           "reject signals with mtf_confirmed != True", {},
                           flt(lambda t: t.get("mtf_confirmed") is True)))
    hyps.append(Hypothesis("H4e", "HTF confirmation where a higher timeframe exists",
                           "The H4 effect is the HTF agreement itself, not the removal of 4h signals (which have no "
                           "configured higher timeframe and so are never 'confirmed').",
                           "reject signals with mtf_confirmed == False; keep signals without a higher timeframe", {},
                           flt(lambda t: t.get("mtf_confirmed") is not False)))
    hyps.append(Hypothesis("H8", "No 4h signals", "4h signals of this strategy underperform.",
                           "reject 4h signals", {}, flt(lambda t: t["timeframe"] != "4h")))
    hyps.append(Hypothesis("H4b", "4h regime not against", "Trading against the 4h regime lowers expectancy.",
                           "reject signals whose 4h regime is a trend against the trade", {},
                           flt(lambda t: t["htf_alignment_4h"] != "AGAINST")))
    hyps.append(Hypothesis("H4c", "BTC regime not against", "Alt signals against BTC's regime underperform.",
                           "reject non-BTC signals when BTC's regime (same timeframe) trends against the trade", {},
                           flt(lambda t: t["btc_alignment"] != "AGAINST")))
    hyps.append(Hypothesis("H4d", "No clustered signals", "Signals that arrive while other coins already signalled "
                           "the same direction (within 60 min) are late moves of one market-wide swing.",
                           "reject a signal if another coin produced a same-direction signal in the previous 60 min", {},
                           flt(lambda t: t.get("cluster_prior", 0) == 0)))
    cost_best, cost_table = _choose_on_dev(base, sp, [0.1, 0.15, 0.2, 0.3, 0.5],
                                           lambda k: (lambda t: t["est_cost_r"] <= k))
    hyps.append(Hypothesis("H6", "Cost-drag filter (chosen on dev)",
                           "Signals whose stop is small relative to trading costs cannot overcome fees and slippage.",
                           f"reject signals whose estimated round-trip cost exceeds {cost_best}R "
                           "(fees + slippage / stop distance, known at decision time)", {"max_cost_r": cost_best},
                           flt(lambda t, k=cost_best: k is None or t["est_cost_r"] <= k),
                           {"grid": cost_table, "chosen": cost_best}, fitted=True))
    score_best, score_table = _choose_on_dev(base, sp, [0, 50, 60, 70, 80, 90],
                                             lambda k: (lambda t: (t.get("score") or 0) >= k))
    hyps.append(Hypothesis("H7", "Score threshold (chosen on dev)", "Higher technical scores identify better signals.",
                           f"reject signals with score < {score_best} (threshold chosen on dev only)",
                           {"min_score": score_best},
                           flt(lambda t, k=score_best: k is None or (t.get("score") or 0) >= k),
                           {"grid": score_table, "chosen": score_best}, fitted=True))
    sel = regime_selection([t for t in base if sp.period(t["decision_ns"]) == "dev"])
    allowed = sorted(f"{s} in {r}" for (s, r), ok in sel.items() if ok)
    hyps.append(Hypothesis("H5", "Regime-aware strategy selection (learned on dev)",
                           "Each strategy only has an edge in some regimes; elsewhere return NO TRADE.",
                           "allow a strategy only in regimes where it had positive dev expectancy with n >= 30",
                           {"allowed_cells": allowed},
                           flt(lambda t, sel=sel: sel.get((t["strategy"], t["regime"]["regime"]), False)),
                           fitted=True))
    for ms in (60, 70, 80):
        hyps.append(Hypothesis(f"S{ms}", f"Minimum score {ms} (sensitivity)", "Higher scores identify better signals.",
                               f"reject signals with score < {ms}", {"min_score": ms},
                               flt(lambda t, ms=ms: (t.get("score") or 0) >= ms)))

    for h in hyps:
        h.keep = getattr(h.build, "keep", None)
    results = []
    for h in hyps:
        var = h.build()
        var_rep = report(var, sp, detail=False)
        per_b, per_v = by_period(base, sp), by_period(var, sp)
        boot = day_block_bootstrap(per_b["val"] + per_b["test"], per_v["val"] + per_v["test"])
        v, notes = verdict(base_rep, var_rep, boot, h.fitted)
        risk = None
        if h.key.startswith("H3"):
            risk = drawdown_vs_random(per_b["val"] + per_b["test"], per_v["val"] + per_v["test"])
            notes = notes + [risk["note"]]
        results.append({"key": h.key, "title": h.title, "hypothesis": h.hypothesis, "change": h.change,
                        "params": h.params, "dev_selection": h.chosen_on_dev, "results": var_rep,
                        "bootstrap": boot, "verdict": v, "notes": notes, "fitted": h.fitted,
                        "risk_test": risk})

    # Walk-forward for the parameterised hypotheses (fit only on data before each fold).
    def fit_ext(train):
        best, _ = _choose_on_dev(train, Split(0, 1 << 62, 1 << 62, 1 << 62), [0.75, 1.0, 1.25, 1.5, 2.0, 2.5],
                                 lambda k: (lambda t: (t["entry"]["ext_fast_atr"] or 0) < k))
        return (lambda t: best is None or (t["entry"]["ext_fast_atr"] or 0) < best)

    def fit_regime(train):
        s = regime_selection(train)
        return lambda t: s.get((t["strategy"], t["regime"]["regime"]), False)

    wf = {"H1c": walk_forward(base, 5, fit_ext), "H5": walk_forward(base, 5, fit_regime)}

    # ML quality filter (walk-forward, purged).
    ml_folds = []
    ts = sorted(t["decision_ns"] for t in base)
    edges = [ts[0] + (ts[-1] - ts[0]) * k // 5 for k in range(6)]
    edges[-1] += 1
    for k in range(2, 5):
        train = [t for t in base if t["decision_ns"] < edges[k]]
        test = [t for t in base if edges[k] <= t["decision_ns"] < edges[k + 1]]
        res = ml.train_and_eval(train, test, embargo_ns=DAY_NS)
        if res.get("enabled"):
            p = np.array(res.pop("probabilities"))
            thr = res["threshold"]
            kept = [t for t, pi in zip(test, p, strict=True) if thr is None or pi >= thr]
            res["baseline"] = summarize(test)
            res["filtered"] = summarize(kept)
        res["fold"] = k
        res["test_from"] = pd.Timestamp(edges[k], tz="UTC").isoformat()
        ml_folds.append(res)
    ml_ok = [f for f in ml_folds if f.get("enabled")]
    ml_summary = {
        "folds": ml_folds,
        "verdict": ("INSUFFICIENT_DATA" if len(ml_ok) < 2 else
                    "NOT_VALIDATED" if not all(f["brier_model"] < f["brier_climatology"] and
                                               f["filtered"].get("avg_r", -9) > f["baseline"].get("avg_r", 9)
                                               for f in ml_ok) else "PROMISING"),
        "note": "Model is used only if it beats climatology (Brier) and improves net R in every fold.",
    }

    validated = [r for r in results if r["verdict"] == "VALIDATED" and r["key"].startswith(("H", "B"))]
    promising = [r for r in results if r["verdict"] in ("VALIDATED", "PROMISING") and r["key"].startswith(("H", "B"))]
    combined = combine(base, prod, corr, sp, [r["key"] for r in promising], hyps,
                       {r["key"]: r["verdict"] for r in results})
    combined_rep = report(combined["trades"], sp, detail=True) if combined["trades"] else None
    if combined_rep:
        per_b, per_v = by_period(base, sp), by_period(combined["trades"], sp)
        cb = day_block_bootstrap(per_b["val"] + per_b["test"], per_v["val"] + per_v["test"])
        cv, cn = verdict(base_rep, combined_rep, cb, fitted=any(
            r["fitted"] for r in results if r["key"] in combined["keys"]))
    else:
        cb, cv, cn = None, "NONE", ["no change qualified for combination"]

    return {
        "meta": {**meta, "split": sp.to_dict(), "candidates": len(rows), "production_candidates": len(prod),
                 "filled_trades": len(base), "invalidated": invalidated, "still_open": open_},
        "baseline": {"definition": "every QUALIFIED production signal (live rule), hybrid stop, primary target, "
                                   "overlapping signals allowed; net of costs", "results": base_rep},
        "hypotheses": results,
        "walk_forward": wf,
        "ml": ml_summary,
        "validated_changes": [r["key"] for r in validated],
        "promising_changes": [r["key"] for r in promising],
        "combined": {"keys": combined["keys"], "results": combined_rep, "bootstrap": cb, "verdict": cv,
                     "notes": cn, "trades": len(combined["trades"])},
        "analyses": analyses(rows, base, sp, corr),
        "trades_sample": compact_trades(base, combined["trades"], sp),
    }


def combine(base, prod, corr, sp, keys: list[str], hyps: list[Hypothesis], verdicts: dict[str, str] | None = None
            ) -> dict[str, Any]:
    """Apply the selected changes together: entry filters, then the stop method, then
    portfolio limits (in that order)."""
    if not keys:
        return {"keys": [], "trades": []}
    verdicts = verdicts or {}
    hmap = {h.key: h for h in hyps}
    stop_keys = [k for k in keys if k.startswith("H2")]
    rank = {"VALIDATED": 0, "PROMISING": 1}
    stop_keys.sort(key=lambda k: (rank.get(verdicts.get(k, ""), 9), not hmap[k].fitted, k))
    keys = [k for k in keys if not k.startswith("H2") or k == (stop_keys[0] if stop_keys else None)]
    method = hmap[stop_keys[0]].params["method"] if stop_keys else "C_hybrid"
    trades = sorted([t for t in (to_trade(c, method) for c in prod) if t is not None], key=lambda t: t["decision_ns"])
    for k in keys:
        keep = hmap[k].keep
        if keep is not None:
            trades = [t for t in trades if keep(t)]
    if "B1" in keys:
        trades = independent_baseline(trades)
    if "H3" in keys or "H3b" in keys:
        pp = PortfolioParams() if "H3" in keys else PortfolioParams(max_positions=0, max_total_risk_r=0,
                                                                     max_same_direction=0, max_daily_loss_r=0,
                                                                     max_consecutive_losses=0)
        trades = run_portfolio(trades, pp, corr)[0]
    return {"keys": keys, "trades": trades}


def analyses(rows, base, sp: Split, corr) -> dict[str, Any]:
    prod = [c for c in rows if c["family"] == "production"]
    oos = [t for t in base if sp.period(t["decision_ns"]) != "dev"]
    # Score calibration (all periods and OOS).
    band = lambda t: ("0-20" if t["score"] <= 20 else "21-40" if t["score"] <= 40 else "41-60" if t["score"] <= 60  # noqa: E731
                      else "61-80" if t["score"] <= 80 else "81-100")
    scores = np.array([t["score"] for t in base], float)
    rs = np.array([t["net_r"] for t in base], float)
    spearman = None
    if len(scores) > 10:
        spearman = float(pd.Series(scores).rank().corr(pd.Series(rs).rank()))
    comp_keys = sorted({k for t in base for k in (t.get("components") or {})})
    comp = {}
    for k in comp_keys:
        vals = [(t["components"].get(k), t["net_r"]) for t in base if t["components"].get(k) is not None]
        if len(vals) < 20:
            continue
        med = float(np.median([v for v, _ in vals]))
        hi = [r for v, r in vals if v > med]
        lo = [r for v, r in vals if v <= med]
        comp[k] = {"median": round(med, 3), "above_median": summarize([{"net_r": r} for r in hi]),
                   "at_or_below_median": summarize([{"net_r": r} for r in lo])}
    score_cal = {"bands_all": by_group(base, band), "bands_oos": by_group(oos, band),
                 "spearman_score_vs_net_r": round(spearman, 4) if spearman is not None else None,
                 "components": comp}
    # Stops.
    stops = {}
    for m in ("A_atr", "B_structure", "C_hybrid", "D_noise"):
        tr = [t for t in (to_trade(c, m) for c in prod) if t is not None]
        stopped = [t for t in tr if t["reason"] in ("STOP", "STOP_GAP", "AMBIGUOUS_STOP")]
        stops[m] = {
            "trades": len(tr), "stop_hit_pct": round(100 * len(stopped) / len(tr), 2) if tr else None,
            "avg_stop_atr": round(float(np.mean([t["stop_atr"] for t in tr])), 3) if tr else None,
            "avg_mfe_r": round(float(np.mean([t["mfe_r"] for t in tr])), 3) if tr else None,
            "avg_mae_r": round(float(np.mean([t["mae_r"] for t in tr])), 3) if tr else None,
            "avg_cost_r": round(float(np.mean([t["cost_r"] for t in tr])), 4) if tr else None,
            "noise_stopouts_pct": round(100 * sum(1 for t in stopped if t["post_exit_target"]) / len(stopped), 2)
            if stopped else None,
            "gross": summarize(tr, "gross_r"), "net": summarize(tr),
            "net_oos": summarize([t for t in tr if sp.period(t["decision_ns"]) != "dev"]),
        }
    for mult in (1.0, 1.5, 2.0, 2.5):
        key = "A_atr" if mult == 1.5 else f"A_atr_{mult:g}"
        tr = sorted([t for t in (to_trade(c, key) for c in prod) if t is not None], key=lambda t: t["decision_ns"])
        if tr:
            stops.setdefault("atr_multiplier_sensitivity", {})[f"{mult:g}"] = {
                p: summarize([t for t in tr if sp.period(t["decision_ns"]) == p]).get("avg_r") for p in PERIODS
            } | {"avg_cost_r": round(float(np.mean([t["cost_r"] for t in tr])), 4),
                 "stop_hit_pct": round(100 * sum(1 for t in tr if t["reason"] in ("STOP", "STOP_GAP", "AMBIGUOUS_STOP"))
                                       / len(tr), 2)}
    # Entry timing.
    entry = {"by_class": by_group(base, lambda t: t["entry_class"]),
             "by_extension": by_group(base, lambda t: _bin(t["entry"]["ext_fast_atr"], [0, 0.5, 1.0, 1.5, 2.0, 2.5])),
             "by_swing_room": by_group(base, lambda t: _bin(t["entry"]["swing_room_atr"], [0, 0.5, 1.0, 2.0, 4.0])),
             "by_run": by_group(base, lambda t: _bin(t["entry"]["run"], [1, 3, 5]))}
    # Regimes and strategies.
    research = [t for t in (to_trade(c) for c in rows if c["family"] == "research") if t is not None]
    allt = base + research
    regimes = {"by_regime": by_group(base, lambda t: t["regime"]["regime"]),
               "by_alignment": by_group(base, lambda t: t["regime_alignment"]),
               "by_strategy_regime": by_group(allt, lambda t: f"{t['strategy']} | {t['regime']['regime']}"),
               "by_btc_alignment": by_group(base, lambda t: t["btc_alignment"]),
               "by_htf4h_alignment": by_group(base, lambda t: t["htf_alignment_4h"])}
    strategies = {s: report([t for t in allt if t["strategy"] == s], sp)
                  for s in sorted({t["strategy"] for t in allt})}
    # Correlation / clustering.
    pf_trades, reasons = run_portfolio(base, PortfolioParams(), corr)
    cluster = {"by_cluster_prior": by_group(base, lambda t: _bin(t.get("cluster_prior", 0), [1, 2, 4])),
               "same_hour_same_direction_share_pct": round(100 * sum(1 for t in base if t.get("cluster_size", 0) > 0)
                                                           / max(len(base), 1), 2),
               "portfolio_rejections": dict(reasons), "portfolio_accepted": len(pf_trades)}
    # Failure categories: how often each measured condition appears among losers vs winners.
    flags = {
        "late_entry (EXTENDED/EXHAUSTION)": lambda t: t["entry_class"] in ("EXTENDED", "EXHAUSTION_RISK"),
        "near opposing swing (<1 ATR)": lambda t: t["entry"]["swing_room_atr"] is not None
        and 0 <= t["entry"]["swing_room_atr"] < 1.0,
        "stop inside 3-bar noise": lambda t: (t.get("noise_atr") or 0) > t["stop_atr"],
        "clustered (other coin same direction, prior 60 min)": lambda t: t.get("cluster_prior", 0) > 0,
        "4h regime against": lambda t: t["htf_alignment_4h"] == "AGAINST",
        "BTC regime against": lambda t: t["btc_alignment"] == "AGAINST",
        "momentum fading": lambda t: bool(t["entry"].get("momentum_fading")),
        "unfavourable regime (conflicted/sideways/exhaustion/high-vol)": lambda t: t["regime"]["regime"] in (
            "CONFLICTED", "SIDEWAYS", "EXHAUSTION", "HIGH_VOL_EXPANSION"),
    }
    noise = {c["id"]: (c["outcomes"].get("D_noise") or {}).get("stop_atr") for c in prod}
    for t in base:
        t["noise_atr"] = noise.get(t["id"])
    losers = [t for t in base if t["net_r"] <= 0]
    winners = [t for t in base if t["net_r"] > 0]
    fail = []
    for name, fn in flags.items():
        with_flag = [t for t in base if fn(t)]
        fail.append({"category": name,
                     "share_of_losers_pct": round(100 * sum(1 for t in losers if fn(t)) / max(len(losers), 1), 2),
                     "share_of_winners_pct": round(100 * sum(1 for t in winners if fn(t)) / max(len(winners), 1), 2),
                     "with_flag": summarize(with_flag),
                     "without_flag": summarize([t for t in base if not fn(t)])})
    return {"score_calibration": score_cal, "stops": stops, "entry_timing": entry, "regimes": regimes,
            "strategies": strategies, "correlation": cluster, "failure_categories": fail}


def _bin(x: float | None, edges: list[float]) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/a"
    if x < edges[0]:
        return f"< {edges[0]:g}"
    for a, b in zip(edges, edges[1:], strict=False):
        if a <= x < b:
            return f"{a:g} - {b:g}"
    return f">= {edges[-1]:g}"


def compact_trades(base, combined, sp: Split, limit: int = 400) -> list[dict[str, Any]]:
    """Most recent out-of-sample baseline trades with the combined-variant decision, for the
    dashboard's counterfactual table."""
    kept = {t["id"] for t in combined}
    out = []
    for t in sorted(base, key=lambda t: -t["decision_ns"])[:limit]:
        out.append({"id": t["id"], "symbol": t["symbol"], "timeframe": t["timeframe"], "strategy": t["strategy"],
                    "direction": t["direction"], "time": pd.Timestamp(t["signal_ns"], tz="UTC").isoformat(),
                    "period": sp.period(t["decision_ns"]), "score": t["score"], "entry_class": t["entry_class"],
                    "regime": t["regime"]["regime"], "net_r": round(t["net_r"], 4), "reason": t["reason"],
                    "proposed_takes": t["id"] in kept if combined else None})
    return out


def counts(rows) -> dict[str, int]:
    return dict(Counter(c["strategy"] for c in rows))
