"""User-editable runtime settings persisted in the ``app_settings`` table.

Secrets are *not* stored here; Telegram credentials come from the environment only.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select

from app.analysis.params import AnalysisParams
from app.config import get_settings
from app.db.models import AppSetting
from app.db.session import session_factory
from app.forward.settings import ValidationSettings
from app.notifications.settings import NotificationSettings
from app.research.settings import DecisionSettings
from app.risk.sizing import SizingConfig
from app.strategy.params import StrategyParams
from app.timeutil import TIMEFRAMES

SETTINGS_KEY = "runtime"
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")


class AlertSettings(BaseModel):
    """Only QUALIFIED setups become signals, so only QUALIFIED setups are ever alerted."""

    telegram_enabled: bool = True
    min_score: int = Field(0, ge=0, le=100)
    # Do not alert on signals whose candle closed longer ago than this many candles.
    max_signal_age_candles: int = Field(1, ge=1, le=10)


class RiskDefaults(BaseModel):
    """Account and cost assumptions used to size every trade plan (estimates only)."""

    account_balance: float = Field(10_000, gt=0, le=1e9, description="USDT")
    risk_per_trade_pct: float = Field(1.0, gt=0, le=10)
    fee_rate_pct: float = Field(0.055, ge=0, le=5)
    slippage_pct: float = Field(0.02, ge=0, le=5)
    leverage: float = Field(5, ge=1, le=200)
    max_leverage: float = Field(20, ge=1, le=200)
    maintenance_margin_pct: float = Field(0.5, ge=0, le=50)

    @model_validator(mode="after")
    def _check(self) -> RiskDefaults:
        if self.leverage > self.max_leverage:
            raise ValueError("leverage must not exceed max_leverage")
        if self.risk_per_trade_pct > 5 and self.leverage > 10:
            raise ValueError("unsafe combination: risk above 5% per trade with leverage above 10x")
        return self

    def sizing(self) -> SizingConfig:
        return SizingConfig(account_balance=self.account_balance, risk_pct=self.risk_per_trade_pct,
                            fee_rate_pct=self.fee_rate_pct, slippage_pct=self.slippage_pct, leverage=self.leverage,
                            maintenance_margin_pct=self.maintenance_margin_pct)


class EvaluationSettings(BaseModel):
    """Signal outcome methodology (see app.signals.evaluation)."""

    max_bars: int = Field(96, ge=1, le=5000)


class RuntimeSettings(BaseModel):
    watchlist: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    timeframes: list[str] = Field(default_factory=lambda: ["15m", "1h", "4h"])
    strategy: StrategyParams = Field(default_factory=StrategyParams)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    risk: RiskDefaults = Field(default_factory=RiskDefaults)
    evaluation: EvaluationSettings = Field(default_factory=EvaluationSettings)
    analysis: AnalysisParams = Field(default_factory=AnalysisParams)
    decision: DecisionSettings = Field(default_factory=DecisionSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)

    @field_validator("watchlist")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for s in v:
            s = s.strip().upper()
            if not SYMBOL_RE.match(s):
                raise ValueError(f"invalid USDT perpetual symbol: {s!r}")
            if s not in out:
                out.append(s)
        if not 1 <= len(out) <= 30:
            raise ValueError("watchlist must contain between 1 and 30 symbols")
        return out

    @field_validator("timeframes")
    @classmethod
    def _tfs(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one timeframe is required")
        for tf in v:
            if tf not in TIMEFRAMES:
                raise ValueError(f"unsupported timeframe {tf}")
        return sorted(set(v), key=lambda t: TIMEFRAMES[t])

    @property
    def required_timeframes(self) -> list[str]:
        """Scanned timeframes plus higher timeframes needed for confirmation, longest first."""
        tfs = set(self.timeframes) | set(self.strategy.zones.htf_timeframes)
        if self.strategy.mtf.enabled:
            tfs |= set(self.strategy.mtf.timeframes)
        return sorted(tfs, key=lambda t: -TIMEFRAMES[t])


Listener = Callable[[RuntimeSettings], Awaitable[None]]


class SettingsStore:
    def __init__(self) -> None:
        self._current: RuntimeSettings | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[Listener] = []

    @property
    def current(self) -> RuntimeSettings:
        if self._current is None:
            raise RuntimeError("settings not loaded")
        return self._current

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def load(self) -> RuntimeSettings:
        async with session_factory()() as session:
            row = await session.get(AppSetting, SETTINGS_KEY)
            if row is None:
                env = get_settings()
                settings = RuntimeSettings(watchlist=[s for s in env.default_watchlist.split(",") if s.strip()])
                session.add(AppSetting(key=SETTINGS_KEY, value=settings.model_dump(mode="json")))
                await session.commit()
            else:
                settings = RuntimeSettings.model_validate(row.value)
        self._current = settings
        return settings

    async def refresh(self) -> RuntimeSettings:
        """Re-read from the database (used by instances that did not make the change)."""
        async with session_factory()() as session:
            row = (await session.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY))).scalar_one_or_none()
        if row is not None:
            new = RuntimeSettings.model_validate(row.value)
            if self._current is None or new != self._current:
                self._current = new
                await self._notify(new)
        return self.current

    async def update(self, settings: RuntimeSettings) -> RuntimeSettings:
        async with self._lock:
            async with session_factory()() as session:
                row = await session.get(AppSetting, SETTINGS_KEY)
                if row is None:
                    session.add(AppSetting(key=SETTINGS_KEY, value=settings.model_dump(mode="json")))
                else:
                    row.value = settings.model_dump(mode="json")
                await session.commit()
            self._current = settings
        await self._notify(settings)
        return settings

    async def _notify(self, settings: RuntimeSettings) -> None:
        for listener in self._listeners:
            await listener(settings)


settings_store = SettingsStore()
