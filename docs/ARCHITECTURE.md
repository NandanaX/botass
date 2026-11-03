# Architecture

```
                      ┌──────────────── Bybit V5 (public) ────────────────┐
                      │  REST /v5/market/*            WS public/linear     │
                      └──────────┬───────────────────────────┬────────────┘
                                 │ history, tickers,          │ tickers.*, kline.*
                                 │ funding, instruments       │
┌────────────────────────────────▼───────────────────────────▼─────────────────────┐
│ backend (FastAPI, single process)                                                │
│                                                                                  │
│  exchanges/  BybitClient (rate limit, retry, validation)   BybitStream (WS)      │
│  market/     MarketDataService: closed-candle series (DB + REST, gap backfill),  │
│              live MarketState (tickers, forming candles, staleness)              │
│  indicators/ causal numpy indicators, swing structure, S/R                       │
│  strategy/   compute_features() → evaluate_at()  ◄─── single source of truth     │
│  risk/       ATR levels, position-size calculator                                │
│  signals/    deterministic ids, idempotent persistence, outcome evaluation       │
│  alerts/     Telegram formatting + delivery                                      │
│  backtest/   run_backtest() uses evaluate_at(); execution model; metrics         │
│  workers/    Scanner loop + PostgreSQL advisory-lock leader election             │
│  ai/         Explainer interface (template implementation in v1)                 │
│  api/        REST routes + /ws event hub                                         │
└───────────────┬──────────────────────────────────────────────┬───────────────────┘
                │ SQLAlchemy (asyncpg / aiosqlite)              │ REST + WebSocket
         ┌──────▼──────┐                               ┌────────▼────────┐
         │ PostgreSQL  │                               │ Next.js frontend│
         └─────────────┘                               └─────────────────┘
```

## Backend modules

| Module | Responsibility |
|---|---|
| `app/config.py` | Environment configuration and secrets (pydantic-settings). |
| `app/settings_store.py` | User-editable runtime settings in the `app_settings` table (watchlist, timeframes, strategy, alerts, risk defaults). |
| `app/exchanges/bybit.py` | V5 REST client: token-bucket rate limiter, exponential backoff with jitter on network/5xx/403/429/rate-limit retCodes, newest-first → ascending reordering, backwards pagination. |
| `app/exchanges/bybit_ws.py` | Public WebSocket: subscriptions in chunks of 10 topics, 20 s `ping`, 45 s receive timeout, reconnect with backoff, ticker delta merging. |
| `app/market/validation.py` | OHLCV sanity checks, timeframe alignment, `filter_closed`, `find_gaps`. |
| `app/market/service.py` | Incremental closed-candle cache per symbol/timeframe (DB first, then REST), gap backfill, persistence, exchange clock-offset correction, REST ticker fallback when the stream is down or stale. |
| `app/indicators/` | Pure, causal indicator functions (tested against reference values). |
| `app/indicators/zones.py` | Support/resistance zones from confirmed pivots (Phase 2). |
| `app/strategy/` | `StrategyParams`, `compute_features`, `evaluate_at`, scoring, multi-timeframe alignment; `setups.py`: breakout and pullback detection and the qualification engine. |
| `app/risk/` | `stops.py` (ATR/structure/hybrid), `targets.py` (R-multiple targets, opposing zones), `sizing.py` (exchange-rule sizing, margin, liquidation), `calculator.py` (API). |
| `app/signals/` | Signal ids, `SignalService` (persist, alert, broadcast), outcome methodology. |
| `app/backtest/` | Execution model shared with the outcome evaluator, the simulation loop, metrics, and the background job service. |
| `app/workers/` | `Scanner` and `LeaderLock`. |
| `app/api/` | Routes, dependency wiring, WebSocket hub. |

## Data flow of a live signal

1. Every `SCAN_INTERVAL_SECONDS` the scanner asks `MarketDataService` for each required symbol/timeframe. Higher timeframes are fetched first so they are current.
2. The service returns only candles with `open_time + interval <= now - grace` (closed candles), refreshing incrementally from Bybit when a new candle should exist.
3. If the last closed candle is new, features are recomputed and `evaluate_at(features, last_index, params, htf_features)` runs.
4. The analysis snapshot is upserted into `analysis_snapshots` and broadcast as `analysis`.
5. If all five rules pass (and filter-mode multi-timeframe agreement holds), a signal row is inserted with `ON CONFLICT DO NOTHING RETURNING id`. Only the inserter dispatches the Telegram alert, so duplicates are impossible even with concurrent writers.
6. The signal id is `sha256(exchange|symbol|timeframe|direction|candle_open_time|strategy_version)`. `strategy_version` is the rules semver plus a hash of the parameter set.
7. On later candles, `evaluate_pending` applies the outcome methodology to pending signals.

Signals are skipped (logged, not stored) when the series has gaps within the warm-up window or could not be refreshed (`DATA_ISSUE`).

## Preventing look-ahead bias

* **Closed candles only.** The forming candle returned by Bybit is filtered out by time, not by position.
* **Causal indicators.** Every value at index *i* depends only on indices ≤ *i*. EMA/RMA are SMA-seeded recursions; the breakout level is the max/min of the *previous* N bars; relative volume uses the *previous* N bars.
* **Swing pivots** need *k* bars on the right, so a pivot at bar *j* becomes visible only at bar *j + k* (`confirmed_at`).
* **Higher timeframes** are aligned with `searchsorted(htf.close_time, decision_close_time, side="right") - 1`. Only HTF candles that had closed by the signal candle's close are used.
* **Tests** prove it. `evaluate_at` on a series truncated at *i* equals `evaluate_at` on the full series at *i*. Mutating candles after *i* does not change the decision at *i*. Backtest trades that closed before candle *k* are identical when all data after *k* is replaced.

## Phase 2: zones, entry setups and trade plans

`evaluate_at` is still the only decision function: the live scanner, the `/setup` endpoint and the backtester all call it. In Phase 2 it returns every entry *candidate* (`ev.setups`), each with a qualification status. `ev.setup` is the best QUALIFIED candidate, and only that one becomes a stored signal and a Telegram alert.

```
closed candles ─► compute_features (EMAs, RSI, MACD, ATR, rvol, confirmed pivots)
                      │
     zones at i-1 ────┼──► detect_breakout / detect_pullback (Strategy A / B, LONG and SHORT)
                      │            │ candidate: triggered or WATCHLIST, mandatory checks, trigger zone
     zones at i  ─────┤            ▼
 1h/4h zones (closed) ┴──► qualify(): stop ─► targets ─► opposing-zone room ─► HTF filter
                                   ─► sizing with Bybit rules ─► net R:R ─► QUALIFIED | REJECTED (+ reasons)
```

### Support/resistance zones (`app/indicators/zones.py`)

* **Inputs.** Pivots come from `find_pivots`, computed once per series. A pivot at bar *j* with window *k* is used only from bar *j + k*.
* **Clustering.** Pivots inside `lookback` are sorted by price and clustered greedily. A cluster's spread never exceeds `cluster_atr_mult × ATR`. Narrow zones are widened to `min_width_atr × ATR`.
* **Tests.** A zone needs `min_tests` pivots, and each pivot counts as one test. A test whose candle closed back outside the zone counts as a rejection.
* **Status** comes from closes after the zone formed. A close beyond a boundary by `break_buffer_atr × ATR` flips the side price is on.
  * No flips: `ACTIVE`.
  * One or more flips: `BROKEN` (role reversal).
  * `invalidate_after_crossings` or more: `INVALIDATED`.
* **Role** relative to the current close: `SUPPORT` below price, `RESISTANCE` above. When price is inside a zone, the side it came from decides.
* **Strength** is 40% tests, 20% rejection ratio, 25% recency and 15% status. It ranks zones and is not a probability.
* **Stable id:** a hash of symbol, timeframe, origin and first pivot time. Zones are recomputed from candles on demand and are not stored in their own table.
* **Higher timeframes.** By default 4h zones give major structure and 1h zones intermediate structure. They are taken at `searchsorted(close_time, decision_close, "right") - 1`, so a higher-timeframe candle that is still forming is never used.

### Entry strategies (`app/strategy/setups.py`)

Both strategies require EMA fast > mid > slow for a LONG (mirrored for a SHORT). Triggers use **zones as known at the close of the previous bar**, so the candle that breaks a zone is judged against the zone as it was before that candle.

| | Strategy A: breakout | Strategy B: pullback / retest |
|---|---|---|
| Trigger | Close beyond the trigger zone's far boundary + break buffer (or the N-bar range in `level_source="range"`, the Phase 1 rule) | Candle low (LONG) reaches a support zone, including broken resistance, within tolerance, without piercing more than `max_pierce_atr` |
| Confirmation | Configurable: RSI band, MACD histogram sign, relative volume ≥ `min_rvol`, body ≥ `min_body_ratio`, not more than `max_extension_atr` beyond the zone | Configurable: close back beyond the zone, directional candle, close position in range, RSI band, MACD histogram turning |
| WATCHLIST | Price within `watch_distance_atr` of the boundary to break | Approaching the zone, or touched it without a confirmed rejection |
| Structural level for the stop | Far boundary of the broken zone | Far boundary of the retested zone |

### Stops, targets and sizing (`app/risk/`)

* **Stop methods** (`stops.py`):
  * ATR: entry ∓ k × ATR.
  * Structure: one tick beyond the structural level.
  * Hybrid (default): structural level ∓ buffer × ATR.
* **Stop limits.** A stop closer than `min_stop_atr` is *widened*, never tightened. A stop wider than `max_stop_atr` rejects the setup. Stops are rounded to the tick size away from the entry.
* **Targets** (`targets.py`): TP = entry ± R × m for each configured multiple (default 1R/2R/3R), rounded toward the entry.
  * The nearest usable opposing zone on the entry timeframe or a higher timeframe defines the *room* in R. Targets beyond it are flagged `blocked`.
  * Room below `min_rr` rejects the setup with `opposing_zone_too_close`.
* **Sizing** (`sizing.py`):
  * Risk per unit includes adverse slippage on entry and stop, and fees on both sides.
  * Quantity is rounded *down* to the Bybit quantity step and capped at the maximum market-order size.
  * Checks against min quantity and min notional.
  * Margin is notional / leverage.
  * Isolated-margin liquidation is estimated with the lowest risk-tier maintenance margin from `/v5/market/risk-limit`. A liquidation price before the stop rejects the setup.
  * Net R:R after costs at the primary target must reach `min_net_rr`.
  * Instrument rules are cached for 6 h per symbol.

### Qualification statuses

| Status | Meaning |
|---|---|
| QUALIFIED | Trigger confirmed, every mandatory condition passed, every risk rule holds. Stored as a signal and alerted. |
| WATCHLIST | Trend aligned, price at or near a zone, trigger not confirmed. No prices are proposed. |
| REJECTED | Trigger confirmed, but a mandatory condition or risk rule failed. `reasons[]` carries machine-readable codes and messages. |
| EXPIRED | Set on a stored signal when the next candle opened beyond the stop or TP1, so the hypothetical entry never became valid. |

A detected signal is not a fill. The entry reference is the confirmation close; the hypothetical fill is the next open (backtests and outcome evaluation).

## Backtest execution model

Implemented in `app/backtest/execution.py` and `engine.py`, and listed in every result's `assumptions`:

* Decision at the signal candle's close; entry at the **next candle open** (default), with adverse slippage.
* Stop and target levels come from the signal (signal close ± ATR multiple).
* If the entry candle opens beyond the stop or target, the trade is skipped.
* Stop and target are touched when the low/high reaches them. **When both are touched in one candle, the stop is assumed first.** Gaps through the stop fill at the open.
* Stop, time and end-of-data exits pay slippage; target exits are treated as limit orders.
* Fees are charged on entry and on each exit fill. Size comes from `size_position`: X% of current equity including fees and slippage, rounded down to the instrument quantity step. It uses current Bybit rules, so historical rule changes are not modelled.
* Only QUALIFIED setups are traded. Rejection reasons are counted in `metrics.rejected_setup_reasons`.
* **Partial exits** (`targets.partial_exits`) close the configured allocation at each target, 30/40/30 by default. If a candle touches the stop and any pending target, the whole remainder exits at the stop. Gaps through targets fill at the open. The stop can optionally move to breakeven after TP1. Without partial exits the whole position exits at the primary target (TP2 by default).
* Funding from Bybit funding history: each event between entry and the exit candle's close, with LONG paying positive rates.
* One position at a time. Drawdown is measured on closed-trade equity.

## Signal score

Points per component (configurable weights): trend alignment 25, momentum 20, entry confirmation 20 (breakout distance and body, or rejection-candle quality), relative volume 15, higher-timeframe agreement 15, zone strength 10, reward room 10, volatility 5. Each component stores its points, maximum and a human-readable reason. Components that cannot be evaluated (for example multi-timeframe disabled) are excluded, and the total is rescaled to 0–100. **The score measures rule agreement. It is not a calibrated probability.**

## Signal outcome methodology (v1)

Live signals are classified only by this explicit rule set (`app/signals/evaluation.py`), and always shown as *hypothetical*:

* Hypothetical entry at the next candle's open. If that open is beyond the stop or target: `INVALIDATED`, and the signal's `setup_status` becomes `EXPIRED`.
* Status is decided on the primary target. `evaluation.targets` also reports which of TP1–TP3 were reached before the exit. With partial exits enabled it adds the allocation-weighted gross R.
* First touch of the target is `TARGET_HIT`; of the stop, `STOP_HIT`; of both in one candle, `AMBIGUOUS_STOP` (counted as a stop).
* No touch within `evaluation.max_bars` candles (default 96): `EXPIRED`.
* The R multiple is gross (before fees and slippage).

## Multi-instance deployment

Local and Compose setups run **one** backend process. For multiple instances:

1. **Scanner leadership** is already handled. Each instance tries `pg_try_advisory_lock` on a dedicated connection; only the holder scans, and the others retry every 30 s and take over if the leader dies (verified). SQLite deployments must stay single-instance.
2. **WebSocket fan-out** (`app/api/hub.py`) is in-process. Replace it with Redis pub/sub so `signal`/`analysis` events from the leader reach clients connected to other instances. Followers already read snapshots and signals from the shared database, so REST stays consistent without this change.
3. **Settings** are re-read from the database every scanner cycle, so changes made through any instance propagate.
4. **Backtests** run as in-process asyncio tasks. For scale, move them to a job queue (for example arq or Celery) using the same `run_backtest` function.
5. Each instance keeps its own Bybit WebSocket for live tickers (read-only, low cost). The REST rate limiter is per process, so lower `BYBIT_MAX_REQUESTS_PER_SECOND` when running many instances.

## AI analysis layer

`app/ai/explainer.py` defines `ExplanationContext`: validated indicator values, trend, structure, rule checks, observations, the detected signal and levels. `Explainer.explain(ctx)` returns prose only. v1 ships `TemplateExplainer`, which needs no external API. An LLM provider can implement the same protocol. It must receive only the structured context, and its output is displayed alongside, never instead of, the deterministic numbers. The strategy engine stays the source of truth for prices, levels and signals.

## Database tables

`candles`, `funding_rates`, `signals`, `notifications`, `backtest_runs`, `app_settings`, `analysis_snapshots`. Migrations live in `backend/alembic/versions` and are checked against the ORM models on both SQLite and PostgreSQL.
