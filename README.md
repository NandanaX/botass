# CryptoSignal AI

Crypto futures market analysis and research-signal platform for **Bybit USDT perpetuals**.

It streams live market data, runs a deterministic multi-timeframe technical strategy on **closed candles only**, scores and stores LONG/SHORT setups, sends Telegram alerts, and backtests the same strategy code on historical data with fees, slippage and funding.

> **Version 1 is analysis only.** It never places orders, never needs exchange API keys and never changes leverage. Signals are research outputs, not financial advice.

## Features

| Area | What you get |
|---|---|
| Market data | Bybit V5 REST (history, tickers, funding, open interest) plus a public WebSocket (tickers and klines) with reconnect, heartbeat and REST fallback. Includes rate limiting, retries with exponential backoff, candle validation, gap detection and backfill, persistence, and clear `LIVE` / `STALE` / `NO DATA` states. |
| Indicators | EMA 20/50/200, SMA 200, RSI 14 (Wilder), MACD 12/26/9, ATR 14, Bollinger Bands, volume MA, relative volume, swing pivots, HH/HL and LH/LL structure, support/resistance, breakouts and breakdowns, continuation and reversal observations. |
| Support / resistance zones | Price *zones* (not single lines) from confirmed swing pivots, clustered with an ATR tolerance. Each zone has tests, rejections, ACTIVE/BROKEN/INVALIDATED status, a 0–100 strength ranking and an explanation of how it formed. Zones are computed on the entry timeframe plus 1h (intermediate) and 4h (major). No look-ahead: pivots wait for their confirmation bars, and higher timeframes use closed candles only. |
| Entry strategies | **A: breakout**: confirmed close through a zone in the trend direction, with volume, momentum, body and over-extension checks. **B: pullback / retest**: retest of support (or broken resistance) with a confirmed rejection candle and momentum turn. LONG and SHORT, independently configurable. |
| Trade plan | ATR, market-structure or hybrid stop-loss (structural stops are only ever widened, never tightened); TP1/TP2/TP3 at 1R/2R/3R; checks for opposing zones; optional partial exits (30/40/30); explicit invalidation conditions. |
| Qualification | Each setup is QUALIFIED, WATCHLIST, REJECTED or EXPIRED, with machine-readable reasons (for example "LONG setup rejected because the nearest resistance … leaves only 0.8R"). Only QUALIFIED setups become signals and alerts, and no trade is ever forced. |
| Position sizing | Fee- and slippage-aware quantity, rounded down to the Bybit quantity step; min quantity/notional checks; margin at your leverage; liquidation estimate (lowest Bybit risk tier) checked against the stop; profit at each target. |
| Phase 1 strategy | The original range breakout is still available as `breakout.level_source = "range"`, with an ATR stop and a single target. |
| Multi-timeframe | Optional higher-timeframe trend agreement (default 1h + 4h). In *quality* mode signals are flagged; in *filter* mode signals without agreement are dropped. No look-ahead. |
| Scoring | Transparent 0–100 rule-agreement score with stored per-component reasons. It is **not** a win probability. |
| Signals | Idempotent persistence (deterministic IDs), deduplicated alerts, notification log, and outcome evaluation using a documented methodology, labelled as hypothetical. |
| Telegram | HTML alerts, retries with `retry_after` handling, minimum-score threshold, enable/disable switch, stale-alert suppression, test button. The token stays server-side and is redacted from logs. |
| Backtesting | Same `evaluate_at` as the live scanner. Next-open fills, fees, slippage, Bybit funding history, conservative stop-first handling of ambiguous candles, equity curve, run comparison. |
| Risk calculator | Fee-aware position sizing, notional, implied leverage, R:R, and gross and net profit/loss estimates. |
| Dashboard | Next.js + Tailwind + shadcn-style UI + TradingView Lightweight Charts, with live updates over WebSocket. |

## Quick start (Docker)

Requirements: Docker Desktop (Compose v2.24+).

```bash
cp .env.example .env          # optionally add TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, change POSTGRES_PASSWORD
docker compose up --build
```

* Dashboard: <http://localhost:3000>
* API + interactive docs: <http://localhost:8000/docs>
* Health: <http://localhost:8000/api/health/ready>

The backend container runs `alembic upgrade head` and then a **single** uvicorn worker. On first start it downloads up to 1000 candles per symbol and timeframe, so the scanner table fills within about a minute.

## Local development (without Docker)

Requirements: Python 3.12+, Node 20+ (22 recommended). PostgreSQL is optional locally: SQLite is used when `DATABASE_URL` is unset.

```bash
# Backend
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
cp ../.env.example .env           # edit as needed; DATABASE_URL defaults to SQLite
alembic upgrade head
uvicorn app.main:app --port 8000  # keep a single process; do not use --workers > 1 or --reload with the scanner

# Frontend (second terminal)
cd frontend
npm install
npm run dev                       # http://localhost:3000 (uses NEXT_PUBLIC_API_URL, default http://localhost:8000)
```

## Telegram setup

1. In Telegram, talk to **@BotFather** and run `/newbot`. Copy the token.
2. Send any message to your new bot (or add it to a group).
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id`.
4. Put both values in `.env` as `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, then restart the backend.
5. Go to **Settings → Send test notification**.

Alerts are sent only for newly created signals whose candle closed at most `max_signal_age_candles` ago (default 1). That stops a burst of old alerts after downtime. You can also set a minimum score, or disable alerts entirely.

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest     # 124 tests, no network needed (exchange + Telegram mocked)
cd frontend && npm test                          # 15 tests (vitest)
cd frontend && npm run lint && npm run build
```

Backend coverage includes:

* EMA, RSI (against a published reference example), MACD, ATR, Bollinger Bands and breakout-window calculations.
* LONG and SHORT generation, with each of the five rules individually negated.
* SL/TP math, closed-candle enforcement, and no-look-ahead checks (truncated-history equality and future-mutation invariance) for both the strategy and the backtester.
* Multi-timeframe candle alignment, signal deduplication with a single alert, alert gating, Telegram formatting/retry/redaction, and Bybit retry/pagination/ordering.
* Backtest fees, slippage, funding and ambiguous-bar handling.
* API validation, through the real Alembic migration.

## Application pages

1. **Overview**: live BTC/ETH/SOL prices, 24h change, turnover, open interest, funding, trend/RSI/EMA/MACD per timeframe, active and recent signals, scanner status, and hypothetical signal performance.
2. **Market Scanner**: sortable, filterable table for each symbol and timeframe (filters: symbol, timeframe, trend, signal status).
3. **Market Detail**: candlestick chart (5m–1d), EMA 20/50/200, volume, shaded support (green) and resistance (red) zones with toggles for 1h/4h zones, broken zones and swing points, LONG/SHORT markers, entry/SL/TP lines, live candle, indicator panel, entry-setup summary, zone list and a plain-English explanation.
4. **Trade Setup** (`/setup/BTCUSDT`): for any symbol and timeframe, shows:
   - market context, nearest support and resistance, the 1h/4h major levels and recent swings;
   - every breakout/pullback candidate with its status and reasons;
   - the entry reference and entry zone, the stop (method and reasoning), TP1–TP3, R:R before and after costs, and invalidation conditions;
   - a position calculator (quantity, notional, margin, liquidation, loss and profit at each target) using your account settings, which you can override on the page;
   - a chart with the entry, stop, targets, confirmation candle, invalidation level and zones.
5. **Trading Signals**: active QUALIFIED signals with the stored trade plan (zone, stop method, TP1–TP3, sizing at signal time, invalidation), score breakdown and higher-timeframe details.
6. **Signal History**: filterable, sortable and paginated history, plus the Telegram notification log.
7. **Backtesting**: configure (strategies, stop method, single or partial exits, leverage), run, inspect (metrics, trades by strategy, rejected-setup reasons, fills, equity curve, assumptions) and compare runs.
8. **Risk Calculator**: futures position sizing with Bybit quantity/tick rules, slippage, leverage, margin, liquidation and TP1–TP3; can load levels from an active signal.
9. **Settings**: watchlist, timeframes, zone detection (pivot window, clustering, minimum tests, 1h/4h zones), entry strategies, stop method and multipliers, take-profit R multiples and allocations, minimum R:R, account balance, risk %, leverage and maximum leverage, alerts and Telegram test. Ranges and unsafe combinations are validated in the browser and again on the server.

## Documentation

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): modules, data flow, look-ahead prevention, execution model, scoring, outcome methodology, multi-instance deployment, AI layer.
* [docs/API.md](docs/API.md): REST endpoints and WebSocket events. Live OpenAPI docs are at `/docs`.

## Verification status

Verified in the development environment (Windows, Python 3.12, Node 22):

* All backend (124) and frontend (15) tests pass; `ruff`, `eslint`, `tsc` and `next build` are clean.
* Phase 2 on live Bybit data:
  * Zones were detected on 15m/1h/4h for BTC, ETH, SOL, XRP and DOGE.
  * Instrument rules and risk tiers loaded from Bybit.
  * Watchlist, rejected and qualified setups appeared on real candles. A live DOGEUSDT 4h SHORT breakout was QUALIFIED, and its stop, targets, quantity rounding and loss-within-budget were checked by hand.
  * The Trade Setup, Market, Settings and Backtesting pages were checked in a headless browser.
* Phase 2 backtests on real Bybit history ran through the API, including partial exits:
  * BTCUSDT 1h, 6 months: 23 trades, −6.1% (buy and hold −0.5%).
  * ETHUSDT 15m, 2 months: 8 trades, −3.4%.
  * Results are reported as computed, not tuned.
* Migration `0002_trade_plans` matches the models (autogenerate diff is empty) and downgrades and upgrades cleanly on SQLite.
* Live Bybit integration: REST history, tickers, funding history, and the WebSocket stream (21 topics). The scanner evaluated real closed candles; the dashboard displayed real prices and indicators (checked in a headless browser).
* A six-month BTCUSDT 1h backtest on real Bybit data completed, including fees, slippage and real funding history.
* PostgreSQL code paths ran against a real PostgreSQL server: migration matches the models, idempotent `INSERT … ON CONFLICT … RETURNING`, upserts, and advisory-lock leader election including failover between two backend instances.
* Signal UI pages rendered with setups produced by replaying real Bybit history through the strategy, in a throwaway database.

**Not verified here:**

* `docker compose up` itself, because Docker isn't installed on the build machine. The Dockerfiles and compose file are written but have not been executed.
* A real Telegram delivery. No bot token was available, so delivery, retries and formatting are covered only by mocked tests. Use **Settings → Send test notification** once configured.
* A live signal firing end-to-end through the running scanner. Market conditions during testing did not meet all five rules; the same code path was exercised by tests and by the historical replay.

## Known limitations

### Phase 2

* Zones are recomputed from the cached candle window (up to 1000 candles per timeframe, 300-bar lookback by default). They are stored inside the analysis snapshots and signal plans, not in a dedicated table.
* Liquidation is an isolated-margin estimate using the lowest Bybit risk tier. It ignores fees, funding, cross margin and higher risk tiers for large positions. Bybit's public API does not expose the account's actual margin mode or tier.
* Backtests apply the *current* instrument rules (tick size, quantity step, minimums). Bybit has no public history of rule changes.
* Signals are sized with the account settings in force when they were created; later setting changes do not resize stored signals.
* With partial exits, if a candle touches the stop and a pending target, the whole remainder is assumed stopped. No lower-timeframe data is used to work out the order of prices inside that candle.
* Default thresholds (minimum 1.5R room, 1.2R net, relative volume 1.2x) are starting points, not optimised values. The sample backtests above were negative after costs.

### General

* Backtest drawdown uses closed-trade equity (intra-trade excursions are not included). Margin, liquidation and leverage limits are not modelled.
* Indicator warm-up differs slightly between live (last 1000 candles) and backtests (full history plus warm-up). EMA 200 converges well within that window.
* OHLC data cannot reveal the order of prices inside a candle. Ambiguous candles are resolved conservatively (stop first), and this is declared in every result.
* Out-of-sample and walk-forward evaluation are not implemented yet; the engine is structured so they can be added.
* The LLM explanation layer is an interface only; v1 uses a deterministic template explainer.
