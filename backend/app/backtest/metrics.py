from __future__ import annotations

from collections import Counter
from typing import Any


def max_drawdown(equity: list[float]) -> tuple[float, float]:
    """Return (max drawdown in currency, max drawdown in % of the running peak)."""
    peak = -float("inf")
    dd_abs = dd_pct = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            dd_abs = max(dd_abs, peak - value)
            dd_pct = max(dd_pct, (peak - value) / peak * 100)
    return dd_abs, dd_pct


def direction_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    pnls = [t["net_pnl"] for t in trades]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p <= 0)
    return {
        "trades": n,
        "win_rate_pct": sum(p > 0 for p in pnls) / n * 100 if n else None,
        "net_pnl": sum(pnls),
        "avg_r_multiple": sum(t["r_multiple"] for t in trades) / n if n else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
    }


def compute_metrics(trades: list[dict[str, Any]], initial_equity: float, curve: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    pnls = [t["net_pnl"] for t in trades]
    rs = [t["r_multiple"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    final_equity = curve[-1]["equity"] if curve else initial_equity
    dd_abs, dd_pct = max_drawdown([pt["equity"] for pt in curve])
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": len(wins) / n * 100 if n else None,
        "avg_pnl": sum(pnls) / n if n else None,
        "avg_win": gross_win / len(wins) if wins else None,
        "avg_loss": -gross_loss / len(losses) if losses else None,
        # Expectancy = average net result per trade (equivalently win% * avg win + loss% * avg loss).
        "expectancy": sum(pnls) / n if n else None,
        "avg_r_multiple": sum(rs) / n if n else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "max_drawdown": dd_abs,
        "max_drawdown_pct": dd_pct,
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "net_profit": final_equity - initial_equity,
        "net_return_pct": (final_equity / initial_equity - 1) * 100,
        "total_fees": sum(t["fees"] for t in trades),
        "total_funding": sum(t["funding"] for t in trades),
        "long_trades": sum(t["direction"] == "LONG" for t in trades),
        "short_trades": sum(t["direction"] == "SHORT" for t in trades),
        "by_direction": {d: direction_metrics([t for t in trades if t["direction"] == d]) for d in ("LONG", "SHORT")},
        "exit_reasons": dict(Counter(t["exit_reason"] for t in trades)),
        "avg_bars_held": sum(t["bars_held"] for t in trades) / n if n else None,
    }
