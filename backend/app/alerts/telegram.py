"""Telegram Bot API delivery (https://core.telegram.org/bots/api#sendmessage)."""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.exchanges.ratelimit import backoff_delay
from app.logging_setup import redact
from app.notifications.messages import decision_view

log = logging.getLogger(__name__)


def _price(x: float) -> str:
    if x >= 1000:
        return f"${x:,.2f}"
    if x >= 1:
        return f"${x:,.4f}"
    return f"${x:.6g}"


def _qty(x: float) -> str:
    return f"{x:,.8f}".rstrip("0").rstrip(".") if x else "0"


def _zone(z: dict[str, Any] | None) -> str:
    if not z:
        return "none detected"
    return f"{_price(z['lower'])}-{_price(z['upper'])} ({html.escape(z.get('timeframe', ''))}, {z.get('tests', 0)} tests)"


def _nearest(zones: list[dict[str, Any]], kind: str, price: float) -> dict[str, Any] | None:
    cands = [z for z in zones if z.get("zone_type") == kind and z.get("status") != "INVALIDATED"]
    if kind == "SUPPORT":
        cands.sort(key=lambda z: price - z["upper"])
    else:
        cands.sort(key=lambda z: z["lower"] - price)
    return cands[0] if cands else None


def format_signal_message(signal: dict[str, Any]) -> str:
    """HTML-formatted alert built from the persisted Signal fields (and its trade plan).

    Every number comes from the stored signal: nothing is recomputed or invented here."""
    plan = signal.get("plan") or {}
    if not plan.get("targets"):
        return _format_legacy(signal)
    direction = signal["direction"]
    long = direction == "LONG"
    emoji = "🚀" if long else "🔻"
    entry = signal["entry_reference"]
    zones = list(plan.get("zones") or [])
    for zs in (plan.get("htf_zones") or {}).values():
        zones += zs
    support = _nearest(zones, "SUPPORT", entry)
    resistance = _nearest(zones, "RESISTANCE", entry)
    stop = plan.get("stop") or {}
    sizing = plan.get("sizing") or {}
    base = (sizing.get("rules") or {}).get("base_coin") or signal["symbol"].removesuffix("USDT")
    targets = plan["targets"]
    primary = plan.get("primary_target_index", 1)
    strategy = {"breakout": "Breakout", "pullback": "Pullback / retest"}.get(plan.get("strategy"), "Trend breakout")
    rr = targets[primary]["r_multiple"] if primary < len(targets) else signal["risk_reward"]
    net = plan.get("net_risk_reward")
    partial = bool(plan.get("partial_exits"))
    lines = [
        f"{emoji} <b>CRYPTO TRADING SETUP</b>",
        f"<b>{direction} {html.escape(signal['symbol'])}</b> · {html.escape(signal['timeframe'])} · {strategy}",
        f"Status: <b>{html.escape(signal.get('setup_status', 'QUALIFIED'))}</b>",
        "",
        "<b>Order plan</b>",
        f"📍 Entry: {_price(entry)}",
        f"🛑 Stop Loss: {_price(stop.get('price', signal['stop_loss']))} "
        f"({html.escape(str(stop.get('method', 'atr')))})",
    ]
    for k, t in enumerate(targets):
        tags = []
        if k == primary:
            tags.append("primary")
        if partial and t.get("allocation_pct"):
            tags.append(f"close {t['allocation_pct']:g}%")
        if t.get("blocked"):
            tags.append("⚠️ beyond opposing zone")
        extra = f" · {', '.join(tags)}" if tags else ""
        lines.append(f"🎯 {t['label']}: {_price(t['price'])} ({t['r_multiple']:g}R){extra}")
    if sizing:
        lev = sizing["leverage"]
        liq = sizing.get("liquidation_price")
        lines += [
            f"⚙️ Leverage: {lev:g}x (isolated)",
            f"📦 Suggested Quantity: {_qty(sizing['quantity'])} {html.escape(base)}",
            f"💵 Position Notional: ${sizing['notional']:,.2f} · Margin: ${sizing['initial_margin']:,.2f}",
        ]
        if liq:
            lines.append(f"☠️ Est. Liquidation: {_price(liq)} (beyond the stop)")
        lines.append(f"💸 Planned Risk: about ${sizing['estimated_loss']:,.2f} incl. fees/slippage "
                     f"({sizing['risk_budget']:,.2f} budget)")
    lines += [
        f"⚖️ Risk/Reward: 1:{rr:g} at TP{primary + 1}" + (f" (about 1:{net:.2f} after est. costs)" if net else ""),
        "",
        f"Support: {_zone(support)}",
        f"Resistance: {_zone(resistance)}",
        "",
        f"Reason: {html.escape(signal['explanation'])}",
        f"Signal Score: {signal['score']}/100 (rule agreement, not a win probability; not validated as predictive)",
    ]
    decision = decision_view(plan.get("decision"))
    if decision:
        lines += ["", f"🧪 {html.escape(decision['label'])}"]
        if decision["outcome"] != "QUALIFIED":
            lines.append(f"<i>{html.escape(decision.get('reason') or '')}</i>")
    lines += [
        "",
        f"<i>Candle closed {html.escape(str(signal['candle_close_time']))} UTC. Entry is the close of that candle; "
        f"skip if price has moved far past it. Research signal only; no order has been placed. Losses can exceed "
        f"the planned risk (gaps, slippage, liquidation).</i>",
    ]
    return "\n".join(lines)


def _format_legacy(signal: dict[str, Any]) -> str:
    """Phase 1 format, for signals stored without a trade plan."""
    direction = signal["direction"]
    ind = signal.get("indicators") or {}
    emoji = "🚀" if direction == "LONG" else "🔻"
    rsi = ind.get("rsi")
    hist = ind.get("macd_hist")
    trend = "Bullish" if direction == "LONG" else "Bearish"
    mtf = signal.get("mtf_confirmed")
    mtf_line = {True: "Confirmed", False: "Not confirmed", None: "Not evaluated"}[mtf]
    lines = [
        f"{emoji} <b>CRYPTO SIGNAL DETECTED</b>",
        "",
        f"Symbol: <b>{html.escape(signal['symbol'])}</b>",
        f"Direction: <b>{direction}</b>",
        f"Timeframe: {html.escape(signal['timeframe'])}",
        "",
        f"Entry Reference: {_price(signal['entry_reference'])}",
        f"Stop Loss: {_price(signal['stop_loss'])}",
        f"Take Profit: {_price(signal['take_profit'])}",
        "",
        f"Risk/Reward: 1:{signal['risk_reward']:g}",
        f"Signal Score: {signal['score']}/100 (rule agreement, not a win probability)",
        f"Higher timeframes: {mtf_line}",
        "",
        f"Trend: {trend}",
        f"RSI: {rsi:.1f}" if isinstance(rsi, (int, float)) else "RSI: n/a",
        f"MACD: {'Positive' if (hist or 0) > 0 else 'Negative'}",
        "",
        f"Reason: {html.escape(signal['explanation'])}",
        "",
        f"<i>Candle closed {html.escape(str(signal['candle_close_time']))} UTC. Research signal only; "
        f"no order has been placed.</i>",
    ]
    return "\n".join(lines)


@dataclass(slots=True)
class SendResult:
    ok: bool
    attempts: int
    error: str | None = None


class TelegramClient:
    def __init__(self, token: str, chat_id: str, api_url: str = "https://api.telegram.org", max_attempts: int = 4,
                 client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self.chat_id = chat_id
        self.api_url = api_url.rstrip("/")
        self.max_attempts = max_attempts
        self._client = client or httpx.AsyncClient(timeout=10)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_message(self, text: str) -> SendResult:
        url = f"{self.api_url}/bot{self._token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        error: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            delay: float | None = None
            try:
                resp = await self._client.post(url, json=payload)
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                if resp.status_code == 200 and body.get("ok"):
                    return SendResult(True, attempt)
                description = body.get("description") or resp.text[:200]
                error = f"HTTP {resp.status_code}: {description}"
                if resp.status_code == 429:
                    delay = float((body.get("parameters") or {}).get("retry_after", 1))
                elif resp.status_code < 500:
                    # Bad token, unknown chat, malformed message: retrying will not help.
                    log.error("telegram rejected message", extra={"error": redact(error)})
                    return SendResult(False, attempt, redact(error))
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"
            if attempt < self.max_attempts:
                delay = delay if delay is not None else max(0.5, backoff_delay(attempt, base=1.0, cap=10))
                log.warning("telegram send failed; retrying",
                            extra={"attempt": attempt, "delay": round(delay, 1), "error": redact(error or "")})
                await asyncio.sleep(delay)
        log.error("telegram send failed permanently", extra={"error": redact(error or "")})
        return SendResult(False, self.max_attempts, redact(error or "unknown error"))
