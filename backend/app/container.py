"""Wires services together; one instance per process, stored on ``app.state``."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.alerts.price import PriceAlertService
from app.alerts.telegram import TelegramClient
from app.api.hub import EventHub
from app.backtest.service import BacktestService
from app.config import Settings
from app.exchanges.bybit import BybitClient
from app.market.service import MarketDataService
from app.settings_store import SettingsStore
from app.signals.service import SignalService
from app.workers.leader import LeaderLock
from app.workers.scanner import Scanner


@dataclass
class Container:
    env: Settings
    settings: SettingsStore
    hub: EventHub
    client: BybitClient
    market: MarketDataService
    signals: SignalService
    scanner: Scanner
    backtests: BacktestService
    price_alerts: PriceAlertService
    leader: LeaderLock | None = None
    telegram: TelegramClient | None = None
    tasks: list[asyncio.Task] = field(default_factory=list)


def build_container(env: Settings, settings: SettingsStore, hub: EventHub, session_factory,
                    client: BybitClient | None = None, telegram: TelegramClient | None = None) -> Container:
    client = client or BybitClient(env.bybit_rest_url, env.bybit_max_requests_per_second, env.bybit_request_timeout)
    if telegram is None and env.telegram_configured:
        assert env.telegram_bot_token is not None and env.telegram_chat_id is not None
        telegram = TelegramClient(env.telegram_bot_token.get_secret_value(), env.telegram_chat_id,
                                  env.telegram_api_url)
    market = MarketDataService(client, session_factory, hub, env.candle_history_limit, env.stale_after_seconds)
    signals = SignalService(session_factory, hub, env, telegram)
    scanner = Scanner(market, settings, signals, hub, session_factory, env.scan_interval_seconds,
                      env.candle_close_grace_seconds)
    backtests = BacktestService(market, session_factory, env.backtest_timeout_seconds)
    price_alerts = PriceAlertService(session_factory, hub, telegram)
    market.ticker_listeners.append(price_alerts.on_ticker)
    return Container(env, settings, hub, client, market, signals, scanner, backtests, price_alerts,
                     telegram=telegram)
