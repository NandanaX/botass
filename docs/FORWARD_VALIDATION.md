# Forward validation, robustness testing and the promotion gate (Phase 6)

Real-money trading stays disabled. Nothing in this phase places orders, changes the production signal engine, or switches the decision engine to enforce mode.

## Components

| Module | Role |
|---|---|
| `app/forward/variants.py` | The three variants and their immutable versions |
| `app/forward/costs.py` | Bid/ask fills, exit triggers, stop slippage, fees, net R |
| `app/forward/risk.py` | Paper-account risk engine: exposure, safeguards and circuit breakers |
| `app/forward/service.py` | Live paper-trading tracker (scanner and ticker hooks, missed-candle detection, funding) |
| `app/forward/report.py` | Forward results and the promotion gate |
| `app/research/stats.py` | Cluster bootstrap, block-bootstrap drawdown distribution, period consistency, Holm adjustment |
| `app/research/robustness.py` | Historical stop-multiplier, stress and portfolio-replay study |
| `app/api/routes_validation.py` | API |
| `frontend/src/app/validation` | Strategy Validation Monitor page |

### Variants

All three variants see the same production signals: QUALIFIED setups from `evaluate_at` on closed candles. They differ only in how a signal becomes a paper trade:

| Key | Rule |
|---|---|
| A | Production plan (hybrid stop, primary target) |
| B | Stop at entry ± 2.5 × ATR, target at 2R. Position size is recalculated so the risk stays the same. |
| C | B, but only when every higher timeframe's EMA trend agrees; otherwise REJECTED |

`version` is a hash of the variant rules, the production strategy version, the paper costs and the risk limits. If any of these change, the current evaluation period closes (`strategy_variants.period_end`) and a new one starts. The results of a period therefore always come from one fixed configuration.

### Paper trades

Every live QUALIFIED signal produces one `paper_trades` row per variant.

**Decisions:**
- `ACCEPTED`: the variant's rule passed.
- `REJECTED`: the variant's rule failed (for example, no higher-timeframe agreement for C).
- `MISSED`: the signal was detected later than `max_signal_delay_candles`. Signals on candles that closed while the tracker was not running are also re-evaluated from closed data and recorded as MISSED.

**Statuses:**
- `OPEN` → `CLOSED`;
- `UNFILLED`: the live price was already beyond the stop or target, no price was available, or exchange quantity rules made the trade infeasible;
- `REJECTED`;
- `MISSED`.

**Entry fill:**
- It is taken at detection time.
- A LONG buys at the live ask and a SHORT sells at the bid, plus `slippage_pct`.
- If the bid/ask is older than `max_quote_age_s`, the last price ± half of `assumed_spread_pct` is used instead, flagged `estimated_from_last_price`.
- Quantity is rounded down to Bybit's quantity step.

**Exits:**
- Exits trigger on the side a closing order would trade: the bid for a LONG, the ask for a SHORT.
- Stops and time exits fill `stop_slippage_pct` worse than the trigger. A gap fills at the gapped price, so the stop price is never guaranteed.
- Take-profits fill at their price and pay the maker fee.
- If the tracker was down, touches are reconstructed from the candles that closed meanwhile (`candle_reconstructed`, stop first if both levels were touched).
- Time exit after `max_holding_bars` candles.

**Funding:**
- It is accrued at each funding time from the last predicted rate (`estimated_from_predicted_rate`).
- On close it is replaced by Bybit's funding history when available (`exchange_funding_history`).

**Recorded per trade:**
- detection delay and the quote used;
- entry and exit fees, spread and slippage costs (already inside the fill prices, shown separately);
- funding, gross and net P&L, and net R (net P&L ÷ quantity × |fill − stop|);
- `cost_flags`, which says whether each cost was observed or estimated.

**Signal level vs portfolio level:**
- The strategy (signal-level) statistics use every filled trade.
- `portfolio_taken` marks the trades the variant's paper account actually took under the risk limits. Portfolio statistics and equity curves use only these.

## Risk limits (defaults)

These are paper-account safeguards. They are risk controls, not signal improvements: they limit exposure whether or not they raise expectancy.

| Setting | Default | Why |
|---|---|---|
| `risk_per_trade_pct` | 0.5% | Half the app's 1% default. Historically, losing streaks of 30 trades occurred (about 15% of equity at 0.5%). |
| `max_open_risk_pct` | 2% | At most about four simultaneous full-risk positions |
| `max_correlated_positions` / `corr_threshold` | 1 / 0.7 | November 3 showed 7 shorts on coins correlated 0.83–0.94 behaving as one bet |
| `daily_loss_limit_pct` | 2% | Four full losses in one UTC day stop new entries until the next day |
| `max_drawdown_pct` | 10% | Halts the paper account until a manual reset (`POST /api/validation/risk/{key}/reset-drawdown`, logged) |
| `require_fresh_quote` | true | Data-integrity breaker: no entry without a bid/ask newer than 5 seconds. Data gaps also block entry. |

## Promotion gate (defaults)

A variant is `READY_FOR_REVIEW` only when every criterion passes on **forward** data:
- `INSUFFICIENT_FORWARD_DATA` means the period, trade or cluster minimums are not met yet.
- `NOT_READY` means another criterion failed.

READY_FOR_REVIEW only requests a human review. `POST /api/validation/promotion/{key}/decision` records APPROVED, REJECTED or DEFERRED with a note and the reviewer's name. APPROVED is refused unless the gate passed. Recording a decision never changes production.

| Criterion | Default | How it was chosen |
|---|---|---|
| Forward period | ≥ 60 days | Several regime changes; at least two monthly settlements of funding behaviour |
| Completed trades | ≥ 60 | Historical C produced about 3.4 trades/day before portfolio limits but only about 0.75/day after them; 60 is roughly 2–3 months |
| Independent clusters | ≥ 40 | Same-direction trades within 60 minutes count once. 40 clusters gives a bootstrap interval of roughly ±0.35R. |
| Expectancy | avg ≥ +0.05R and cluster-bootstrap P(avg > 0) ≥ 0.90 | +0.05R is about a quarter of the historical cost per trade, a margin against cost underestimation |
| Concentration | best month ≤ 50% of positive R, and ≥ 2 of 3 sub-periods positive | Phase 5 showed one month producing most of the gain |
| Drawdown | forward max DD ≤ 15R, P(DD > 20R) ≤ 0.2, no tripped breaker | 20R is 10% of equity at 0.5% risk, the same as the circuit breaker |
| Costs | ≤ 25% of trades with estimated fills, exits or funding | Results must rest on observed bid/ask prices |
| Data integrity | missed + data-integrity rejections ≤ 5% of signals | |
| Regression tests | last full run passed, ≤ 7 days old | Written by `tests/conftest.py` to `research_cache/test_status.json`. Partial runs never overwrite it. |

All thresholds are configurable under `settings.validation`. The defaults were set before any forward data existed and should not be loosened after looking at forward results.

## Database migration

`0005_forward_validation` adds:
- `strategy_variants`
- `paper_trades`
- `forward_cursors`
- `promotion_decisions`

The migration matches the models (the autogenerate diff is empty), and downgrade then upgrade is clean on SQLite.

Apply it with:

```
cd backend && python -m alembic upgrade head
```

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/validation/overview` | Forward results per variant: counts, signal-level and portfolio-level statistics (cluster bootstrap, drawdown distribution, monthly and weekly), costs, exposure and breakers, the promotion checklist, recent records, periods and the review log |
| GET | `/api/validation/trades?variant=&status=&limit=` | Paper-trade records |
| GET | `/api/validation/historical` | Historical robustness and stress study (labelled HISTORICAL) |
| POST | `/api/validation/historical` | Re-run it in the background (about 10 seconds) |
| POST | `/api/validation/promotion/{A\|B\|C}/decision` | Manual review `{decision, note, reviewer}`; 409 for APPROVED before the gate passes |
| POST | `/api/validation/risk/{A\|B\|C}/reset-drawdown` | Manual circuit-breaker reset `{note, reviewer}` |

Ticker payloads now also contain `bid_price`, `ask_price` and `quote_at`.

## Command line

```
python -m app.research.fetch --days 400   # refresh history (optional)
python -m app.research.build              # replay production signals
python -m app.research.robustness         # historical robustness and stress study
```
