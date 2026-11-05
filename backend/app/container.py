"""Wires services together; one instance per process, stored on ``app.state``."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from app.alerts.price import PriceAlertService
from app.alerts.telegram import TelegramClient
from app.analysis.service import AnalysisService
from app.api.hub import EventHub
from app.backtest.service import BacktestService
from app.config import Settings
from app.exchanges.bybit import BybitClient
from app.forward.experiments import ForwardExperiments
from app.forward.service import ForwardService
from app.market.service import MarketDataService
from app.notifications.health import HealthMonitor
from app.notifications.service import NotificationService
from app.research.service import ResearchService
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
    analysis: AnalysisService
    research: ResearchService
    forward: ForwardService
    experiments: ForwardExperiments
    notifications: NotificationService
    health: HealthMonitor
    leader: LeaderLock | None = None
    telegram: TelegramClient | None = None
    tasks: list[asyncio.Task] = field(default_factory=list)
    started_at: datetime | None = None
    api_latency_ms: deque | None = None


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
    analysis = AnalysisService(market, settings)
    research = ResearchService(client, market, settings, session_factory)
    forward = ForwardService(market, settings, session_factory, client)
    market.ticker_listeners.append(forward.on_ticker)
    scanner.forward = forward
    experiments = ForwardExperiments(settings, session_factory)
    scanner.experiments = experiments
    notifications = NotificationService(session_factory, hub, settings, telegram)
    signals.center, signals.settings = notifications, settings
    scanner.center = notifications
    forward.center = notifications
    price_alerts.center = notifications
    health = HealthMonitor(notifications, market, scanner, forward, settings, env)
    return Container(env, settings, hub, client, market, signals, scanner, backtests, price_alerts, analysis,
                     research, forward, experiments, notifications, health, telegram=telegram)
