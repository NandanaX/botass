"""Explanation layer.

The deterministic strategy engine is the source of truth. Explainers only turn the
validated, structured ``ExplanationContext`` into prose. They never compute or alter
prices, levels or signals. Version 1 ships ``TemplateExplainer`` (no external API).

To add an LLM later, implement ``Explainer.explain`` with a provider that receives
``context.model_dump()`` as its only market input, and keep ``guard_output``: the
numeric fields returned to clients always come from the context, never from the model.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class ExplanationContext(BaseModel):
    symbol: str
    timeframe: str
    candle_close_time: str
    trend: str
    ema_alignment: str
    macd_state: str
    structure: str
    indicators: dict[str, float | None]
    long_checks: list[dict[str, Any]]
    short_checks: list[dict[str, Any]]
    observations: list[str]
    signal: dict[str, Any] | None = None
    levels: list[dict[str, Any]] = []
    historical_context: dict[str, Any] | None = None


class Explanation(BaseModel):
    provider: str
    summary: str
    conditions: list[str]
    invalidation: list[str]
    risks: list[str]
    disclaimer: str = ("Generated from deterministic indicator values. It describes current conditions; "
                       "it is not a prediction or financial advice.")


class Explainer(Protocol):
    name: str

    async def explain(self, ctx: ExplanationContext) -> Explanation: ...


def _f(x: float | None, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


class TemplateExplainer:
    name = "template"

    async def explain(self, ctx: ExplanationContext) -> Explanation:
        ind = ctx.indicators
        rsi = ind.get("rsi")
        close = ind.get("close")
        atr = ind.get("atr")
        trend_word = {"BULLISH": "an uptrend", "BEARISH": "a downtrend"}.get(ctx.trend, "no clear trend")
        summary = (
            f"{ctx.symbol} on the {ctx.timeframe} chart closed at {_f(close)} and shows {trend_word} by EMA alignment "
            f"({ctx.ema_alignment.replace('_', ' ').lower()}). RSI is {_f(rsi, 1)}, MACD momentum is "
            f"{ctx.macd_state.lower()}, and swing structure reads {ctx.structure}."
        )
        if atr and close:
            summary += f" ATR is {_f(atr)} ({atr / close * 100:.2f}% of price)."
        long_ok = sum(c["passed"] for c in ctx.long_checks)
        short_ok = sum(c["passed"] for c in ctx.short_checks)
        conditions = [f"LONG rules met: {long_ok}/{len(ctx.long_checks)}",
                      f"SHORT rules met: {short_ok}/{len(ctx.short_checks)}"]
        conditions += [f"{'✓' if c['passed'] else '✗'} {c['label']} ({c['detail']})"
                       for c in (ctx.long_checks if long_ok >= short_ok else ctx.short_checks)]
        conditions += ctx.observations
        invalidation: list[str] = []
        risks: list[str] = []
        sig = ctx.signal
        if sig:
            d = sig["direction"]
            invalidation.append(f"The {d} setup is invalidated if price reaches the stop-loss at {_f(sig['stop_loss'])}.")
            invalidation.append(
                f"A close back {'below' if d == 'LONG' else 'above'} the broken {'high' if d == 'LONG' else 'low'} "
                f"({_f(ind.get('prior_high' if d == 'LONG' else 'prior_low'))}) would signal a failed breakout.")
            if sig.get("mtf_confirmed") is False:
                risks.append("Higher timeframes do not fully agree with the signal direction.")
        else:
            invalidation.append("No active setup on the latest closed candle.")
        if rsi is not None and (rsi > 70 or rsi < 30):
            risks.append(f"RSI {rsi:.1f} is at an extreme; momentum may be stretched.")
        if ind.get("rvol") is not None and ind["rvol"] < 1:
            risks.append("Volume is below its recent average, so moves have less participation.")
        if atr and close and atr / close * 100 > 3:
            risks.append("Volatility is elevated; stop distances are wide.")
        if not risks:
            risks.append("No specific risk flags beyond normal market risk.")
        return Explanation(provider=self.name, summary=summary, conditions=conditions, invalidation=invalidation,
                           risks=risks)


def get_explainer() -> Explainer:
    return TemplateExplainer()
