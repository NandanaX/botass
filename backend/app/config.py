"""Process-level configuration loaded from environment variables.

Secrets (database URL, Telegram token) live here and are never sent to the
frontend. User-editable runtime settings (watchlist, strategy parameters, ...)
live in the database; see ``app.settings_store``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "sqlite+aiosqlite:///./cryptosignal.db"

    # CORS origins allowed to call the API (comma separated).
    cors_origins: str = "http://localhost:3000"

    # Bybit V5 public endpoints. No API key is required for market data.
    bybit_rest_url: str = "https://api.bybit.com"
    bybit_ws_url: str = "wss://stream.bybit.com/v5/public/linear"
    bybit_max_requests_per_second: float = 8.0
    bybit_request_timeout: float = 10.0

    # Background workers
    scanner_enabled: bool = True
    market_stream_enabled: bool = True
    scan_interval_seconds: float = 15.0
    candle_close_grace_seconds: float = 3.0
    stale_after_seconds: float = 30.0
    candle_history_limit: int = 1000

    # Telegram (server-side only)
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    telegram_api_url: str = "https://api.telegram.org"

    # Initial watchlist used to seed runtime settings on first start.
    default_watchlist: str = "BTCUSDT,ETHUSDT,SOLUSDT"

    # Number of seconds a backtest may run before being aborted.
    backtest_timeout_seconds: float = Field(default=600.0, gt=0)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_bot_token.get_secret_value() and self.telegram_chat_id)

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
