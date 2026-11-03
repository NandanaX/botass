# API reference

Base URL: `http://localhost:8000`. Interactive OpenAPI docs: `/docs` (Swagger) and `/redoc`.
All timestamps are ISO-8601 UTC. Validation errors return `422` with a `detail` array.

## Health

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness: `{"status":"ok"}` |
| GET | `/api/health/ready` | Readiness: database, market-data staleness, Bybit stream, scanner leader/last cycle, Telegram configured. Returns `503` if the database is down. |

## Market

| Method | Path | Query | Description |
|---|---|---|---|
| GET | `/api/market/tickers` | | Live tickers for the watchlist, plus stream connection state. Each ticker has `available`, `stale`, `source` (`websocket`/`rest`) and `updated_at`. |
| GET | `/api/market/overview` | `timeframe` (default `15m`) | Tickers + latest analysis snapshot per watchlisted symbol + scanner status. |
| GET | `/api/market/{symbol}/candles` | `timeframe`, `limit` (50–1000, default 500) | Closed candles, `live_candle` (forming, from the stream, or `null`), EMA overlays, `zones` (entry timeframe), `htf_zones` (`{"1h": [...], "4h": [...]}`), confirmed `swings`, `levels` (single-price view of the zones, kept for older clients), `signals` in range, `analysis` of the last closed candle (including `setups`), `data_quality`. `503` if Bybit is unreachable and nothing is cached. |
| GET | `/api/market/{symbol}/setup` | `timeframe`, optional `account_balance`, `risk_pct` (≤10), `leverage` (≤ configured max) | Trade-setup analysis of the last closed candle, see below. Works for any Bybit USDT perpetual, not only the watchlist. |
| GET | `/api/market/{symbol}/analysis` | `timeframe` | Latest scanner snapshot (`404` if not scanned yet). |
| GET | `/api/market/{symbol}/explanation` | `timeframe` | `{context, explanation}` from the explainer (template in v1). |
| GET | `/api/market/timeframes` | | `["5m","15m","30m","1h","4h","1d"]` |

### Trade-setup response

```text
market      ticker, candle times, close, trend, structure, indicators, data_current
structure   nearest_support, nearest_resistance (entry timeframe), htf {tf: {support, resistance}},
            zones, htf_zones, swings (recent confirmed pivots)
setups      every candidate: QUALIFIED | WATCHLIST | REJECTED, best first
primary_setup, decision (QUALIFIED | WATCHLIST | REJECTED | NO_SETUP), decision_note
sizing_inputs, instrument (Bybit tick size, qty step, min qty/notional, max leverage, maintenance margin)
active_signals, disclaimer
```

Zone fields:
`id`, `symbol`, `timeframe`, `lower`, `upper`, `mid`, `zone_type`, `origin`, `status`, `tests`, `rejections`, `crossings`, `strength`, `first_test_time`, `last_test_time`, `explanation`.
- `zone_type` is `SUPPORT` or `RESISTANCE`, relative to the close.
- `status` is `ACTIVE`, `BROKEN` or `INVALIDATED`.
- `strength` (0–100) ranks zones against each other. It is not a probability.

Setup fields:
`direction`, `strategy` (`breakout`/`pullback`), `status`, `entry_reference`, `stop_loss`, `take_profit` (the primary target), `risk_reward`, `score`, `score_components`, `conditions[]`, `reasons[]`, `explanation` and `plan`.
- `conditions[]` items have `key`, `label`, `passed` and `detail`.
- `reasons[]` items have `code` and `message`. They are machine-readable rejection or watchlist reasons, such as:
  - `opposing_zone_too_close`
  - `failed_volume_confirmation`
  - `stop_too_wide`
  - `liquidation_before_stop`
  - `below_min_qty`
  - `net_rr_below_minimum`
  - `awaiting_breakout_close`
  - `awaiting_retest`
  - `rejection_not_confirmed`
- WATCHLIST setups have no prices and no plan yet.

`plan` fields:
- `zone`, `entry_zone` and `confirmation_candle`.
- `stop`: `method`, `price`, `distance`, `distance_atr`, `structural_level`, `widened`, `reasoning`.
- `targets[]`: `label`, `r_multiple`, `price`, `allocation_pct`, `blocked`, `note`.
- `primary_target_index` and `net_risk_reward`.
- `opposing_zone`, with `room_r`.
- `sizing`, in the same shape as the risk calculator output.
- `invalidation[]`, `warnings[]` and `partial_exits`.

`symbol` must match `^[A-Z0-9]{2,20}USDT$`; `timeframe` must be one of the supported values.

## Scanner

| Method | Path | Query | Description |
|---|---|---|---|
| GET | `/api/scanner` | `symbol` (substring), `timeframe`, `trend` (`BULLISH`/`BEARISH`/`NEUTRAL`), `signal_status` | One row per watchlisted symbol × scanned timeframe: price, trend, RSI, MACD state, EMA alignment, Phase 1 rule counts, `setup` (best candidate: direction, strategy, status, first reason), `nearest_support`, `nearest_resistance`, `signal_status` (`LONG_SIGNAL`, `SHORT_SIGNAL`, `WATCH_LONG`, `WATCH_SHORT`, `REJECTED`, `NONE`, `NOT_READY`, `DATA_ISSUE`), active signals, `updated_at`. |
| GET | `/api/scanner/status` | | Scanner and stream status. |

## Signals

| Method | Path | Query / body | Description |
|---|---|---|---|
| GET | `/api/signals` | `status` (`active`/`closed`/`all`), `symbol`, `timeframe`, `direction`, `evaluation_status`, `min_score`, `sort` (`created_at`/`candle_close_time`/`score`/`symbol`), `order`, `limit` (≤500), `offset` | `{total, items}` |
| GET | `/api/signals/stats` | | Counts by direction, evaluation and notification status, plus hypothetical hit rate and average R. |
| GET | `/api/signals/{id}` | | Signal with `notifications`. |
| GET | `/api/notifications` | `limit`, `offset` | Telegram delivery log. |

Signal fields: `id`, `symbol`, `timeframe`, `direction`, `strategy_name`, `strategy_version`, `created_at`, `candle_open_time`, `candle_close_time`, `entry_reference`, `stop_loss`, `take_profit`, `risk_reward`, `atr`, `score`, `score_breakdown[]`, `conditions[]`, `indicators{}`, `explanation`, `mtf_confirmed`, `mtf_details[]`, `strategy_type` (`breakout`/`pullback`), `setup_status` (`QUALIFIED` at creation, `EXPIRED` if no valid hypothetical entry), `plan` (the trade plan above plus the zones at signal time), `notification_status` (`SENT`, `FAILED`, `DISABLED`, `NOT_CONFIGURED`, `BELOW_THRESHOLD`, `SKIPPED_STALE`), `evaluation_status` (`PENDING`, `TARGET_HIT`, `STOP_HIT`, `AMBIGUOUS_STOP`, `EXPIRED`, `INVALIDATED`; decided on the primary target), `evaluation{}` (includes `targets.targets_hit` and, with partial exits, `targets.weighted_r`).

Only QUALIFIED setups are stored as signals. The signal id also includes the strategy name, so a breakout and a pullback on the same candle are separate signals.

## Backtests

| Method | Path | Description |
|---|---|---|
| POST | `/api/backtests` | Start a run (`202`). Runs in the background. |
| GET | `/api/backtests` | Run summaries (newest first). |
| GET | `/api/backtests/{id}` | Run with `metrics`, `trades`, `equity_curve`, `assumptions`. |
| GET | `/api/backtests/compare?ids=a,b,c` | Up to 10 runs with metrics and equity curves. |
| DELETE | `/api/backtests/{id}` | `204`; `409` if still running. |

Request body:

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "1h",
  "start": "2026-05-01T00:00:00Z",
  "end": "2026-11-01T00:00:00Z",
  "initial_equity": 10000,
  "risk_per_trade_pct": 1.0,
  "leverage": 5,
  "fee_rate_pct": 0.055,
  "slippage_pct": 0.02,
  "entry_mode": "next_open",
  "max_holding_bars": 200,
  "include_funding": true,
  "min_score": 0,
  "strategy": {
    "breakout": { "enabled": true, "level_source": "zone" },
    "pullback": { "enabled": true },
    "stops": { "method": "hybrid", "atr_multiplier": 1.5 },
    "targets": { "r_multiples": [1, 2, 3], "primary_index": 1, "partial_exits": true, "allocation_pct": [30, 40, 30] },
    "mtf": { "enabled": true, "timeframes": ["4h"], "mode": "quality" }
  }
}
```

- `start` and `end` must include a timezone.
- `strategy` accepts any subset of the strategy parameters; omitted fields use defaults.
- `risk_per_trade_pct` is limited to 10.
- Trades record `strategy`, `targets`, `stop_method`, `quantity`, `notional` and `fills[]` (partial exits).
- Metrics add `trades_by_strategy` and `rejected_setup_reasons`.

## Risk calculator

`POST /api/risk/calculate`

```json
{ "account_balance": 10000, "risk_pct": 1, "direction": "LONG", "entry_price": 68500,
  "stop_loss": 67800, "take_profit": 69900, "take_profits": [69200, 69900, 70600],
  "fee_rate_pct": 0.055, "slippage_pct": 0.02, "leverage": 5, "symbol": "BTCUSDT", "include_fees_in_sizing": true }
```

Returns:
- **Size:** `max_loss` (the risk budget), `quantity` and `quantity_raw`. When `symbol` is given, `quantity` is rounded down to the Bybit quantity step.
- **Position:** `notional`, `leverage`, `initial_margin`, `implied_leverage` and `stop_distance(_pct)`.
- **Costs and loss:** `estimated_entry_fee` and `estimated_loss_at_stop_gross/net`.
- **Reward:** `risk_reward`, `net_risk_reward`, `potential_gross_profit`, `estimated_fees_at_target`, `potential_net_profit` and `targets[]`.
- **Liquidation:** `liquidation_price` and `max_safe_leverage`.
- **Checks:** `feasible` and `issues[]`. Issue codes are `below_min_qty`, `below_min_notional`, `insufficient_margin`, `liquidation_before_stop` and `leverage_above_exchange_max`.
- **Other:** `warnings[]` (including prices that are off the tick size), `instrument` and `note`.

Errors:
- `422` if the levels are on the wrong side for the direction, or if `symbol` is unknown.
- `503` if the instrument rules cannot be loaded.

Formulas:
- **Loss per unit** = |entry_fill − stop_fill| + fee × (entry_fill + stop_fill). Both fills include adverse slippage.
- **Quantity** = floor(budget / loss per unit), rounded down to the quantity step.
- **Liquidation estimate** (isolated margin, using the lowest Bybit risk tier for `mmr`):
  - LONG: `entry × (1 − 1/L + mmr)`
  - SHORT: `entry × (1 + 1/L − mmr)`

## Settings and alerts

| Method | Path | Description |
|---|---|---|
| GET | `/api/settings` | `{settings, telegram: {configured, chat_id_hint}, available_timeframes, strategy_version}`. Never contains secrets. |
| PUT | `/api/settings` | Replace runtime settings (full object as returned in `settings`). New symbols are validated against Bybit instruments (`422` if not trading, `503` if Bybit is unreachable). Invalid ranges and unsafe combinations return `422`: allocations not summing to 100, non-increasing R multiples, leverage above `max_leverage`, risk above 10%, risk above 5% with leverage above 10x, or both strategies disabled. |
| POST | `/api/alerts/telegram/test` | Send a test message. `400` if not configured, `502` if Telegram rejected it. |

## WebSocket `/ws`

Server → client messages: `{"event": "...", "ts": "...", "data": ...}`. The client may send `"ping"` and receives `pong`.

| Event | Data |
|---|---|
| `hello` | `{connection, tickers[], scanner}` on connect |
| `ticker` | Ticker view (throttled to 1/s per symbol) |
| `kline` | `{symbol, timeframe, open_time, open, high, low, close, volume, turnover, confirmed}` |
| `connection` | Bybit stream state |
| `scanner_status` | Scanner status after each cycle |
| `analysis` | New analysis snapshot (symbol/timeframe) |
| `signal` | Newly created signal |
| `settings` | Settings changed |

## Price alerts

Plain price notifications, not trade signals. A Telegram message is sent once when the live last price of a watchlist symbol trades through the level.

| Method | Path | Description |
|---|---|---|
| GET | `/api/price-alerts` | `status` (`ACTIVE`/`TRIGGERED`/`CANCELLED`), `limit`. Newest first. |
| POST | `/api/price-alerts` | Body `{symbol, price, condition?, note?}`; returns `201`. |
| DELETE | `/api/price-alerts/{id}` | Cancels the alert; returns `404` if unknown. |

`condition` is `above` (fires at or above the level) or `below` (fires at or below). Rules:
- If `condition` is omitted, it is chosen from the current price. Pass it explicitly when the level is close to the current price.
- An alert whose condition is already true is rejected with `422`.
- Symbols outside the watchlist return `422`.
- If there is no live price yet, the request returns `503`.

Each alert fires at most once, enforced by an atomic `UPDATE … WHERE status='ACTIVE'`. Deliveries are logged in `/api/notifications` with `kind = "price"`.
