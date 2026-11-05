# LONG / SHORT audit

**Finding:** LONG and SHORT are both fully implemented and treated the same by the rules. The nine SHORT signals on November 3 came from market conditions, not from a bug or a bias against LONG. No LONG signals were added and no strategy rule or locked configuration changed.

Audited version: `2.0.0+77d75b51`. Rerun with `python -m app.research.direction_audit`; permanent tests are in `tests/test_directions.py`.

## Why all nine November 3 signals were SHORT

| Evidence | Result |
|---|---|
| Prior 7 days | All 9 coins fell, by 2.5% to 8.9% |
| 4h trend during the day | 54 of 54 bars bearish, none bullish |
| 1h trend | 186 bearish, 30 neutral, 0 bullish |
| 15m trend | 392 bearish, 298 neutral, 174 bullish |
| Candidates that triggered | 77 SHORT, 9 LONG |
| Share that qualified | SHORT 11 of 77 (14%), LONG 1 of 9 (11%) |

A trend-following strategy can only look for longs when EMA20 > EMA50 > EMA200. On November 3 that was never true on 1h or 4h, so longs could only appear on 15m. The rules passed LONG candidates at about the same rate as SHORT ones.

**The strategy did find one LONG that day, and it was never sent.** A replay of November 3 gives 12 qualified signals against the 9 sent:

| Signal | Why it wasn't sent |
|---|---|
| DOGE 15m SHORT, 01:45 and 02:00 UTC | The app hadn't started scanning yet (first signal 03:00) |
| AVAX 15m LONG, 21:45 UTC (entry 8.531, stop 8.493, target 8.607) | The PC was asleep from 17:38 to 01:14 UTC |

The AVAX signal still qualifies when replayed with live settings and real exchange limits, so the logic didn't block it. Every qualifying signal generated while the PC was awake was sent.

The direction fit the trend, but that didn't make the signals good: 8 of the 9 hit their stop, because most coins rose slightly that day (BTC +0.8%) after the week's fall. Lagging behind a rebound like this is a known weakness of trend following.

## Is there a directional bias?

### Mirror test on real history

Price history for 9 coins on 15m, 1h and 4h (about 11 months) was reflected so that every rally became an identical fall, and the production strategy ran on both versions. An unbiased strategy must make the opposite decision at the same bar.

| Run | Candidates | Same decision | Stop, target and entry mirrored |
|---|---|---|---|
| Production zones re-detected | 28,305 | 76.1% | 71.5% |
| Zones reflected, all other logic unchanged | 28,305 | **99.965%** | **100%** (0 errors) |

So every rule apart from zone detection is an exact mirror image. The remaining 30 of 28,325 candidates appear in only one version and have not been investigated; there are no status or level differences among matched decisions.

### One real asymmetry: zone clustering

`detect_zones` groups swing points by scanning prices from low to high, and a zone with equal swing highs and lows defaults to resistance. Reflected prices can therefore form slightly different zones, which changes individual decisions.

It does **not** favour a direction. Qualified counts change by a similar small amount both ways:

| Direction | On original prices | Its mirror image |
|---|---|---|
| LONG | 1,304 | 1,326 (+1.7%) |
| SHORT | 1,767 | 1,811 (+2.5%) |

**Not changed.** Fixing it would alter which zones the live strategy sees while its locked version stays the same, which would invalidate the forward test. If it is ever fixed, that belongs in a new strategy version with its own evaluation period.

### Funnel over the whole history

| | LONG | SHORT |
|---|---|---|
| Bars in a trend stack | 121,524 | 153,473 |
| Candidates per 1,000 trend bars | 101 | 104 |
| Share of candidates qualifying | 6.9% | 7.8% |
| Rejected: opposing zone too close | 71.7% | 73.0% |
| Rejected: net R:R too low | 37.3% | 32.9% |
| Rejected: volume | 18.1% | 17.9% |
| Rejected: RSI band | 14.3% | 13.9% |

(A candidate can have several rejection reasons.) By timeframe, SHORT qualifies slightly more often on 15m and 1h (5.9% vs 5.2%, 11.9% vs 11.1%) and LONG more often on 4h (23.0% vs 19.2%), so no direction is favoured consistently. There are more SHORT signals mainly because the market spent more time in downtrends: BTC fell 16.5% over the period.

### Direction follows the market

Historical signals, excluding the two partial months at the ends of the period:

| Month | BTC | LONG | SHORT |
|---|---|---|---|
| 2025-12 | +0.7% | 30 | 109 |
| 2026-01 | -10.3% | 88 | 125 |
| 2026-02 | -15.1% | 29 | 271 |
| 2026-03 | +2.2% | 92 | 81 |
| 2026-04 | +11.7% | 73 | 40 |
| 2026-05 | -3.6% | 108 | 74 |
| 2026-06 | -20.6% | 46 | 243 |
| 2026-07 | +7.4% | 68 | 48 |
| 2026-08 | +24.9% | 136 | 30 |
| 2026-09 | +6.3% | 129 | 42 |
| 2026-10 | -7.1% | 35 | 132 |

In every month where BTC moved more than 5%, the majority direction matched the move. Overall there were 838 LONG and 1,213 SHORT signals.

## Performance by direction

Historical replay after fees, slippage and funding. **All of this period has already been examined, so none of it is an untouched test.**

| Strategy | Direction | Trades | Win rate | Avg net R | 95% range | P(avg > 0) | Avg without funding |
|---|---|---|---|---|---|---|---|
| A | LONG | 838 | 30.2% | -0.327 | -0.45 to -0.20 | 0.00 | -0.324 |
| A | SHORT | 1,211 | 35.8% | -0.132 | -0.25 to -0.01 | 0.02 | -0.132 |
| B | LONG | 838 | 30.9% | -0.212 | -0.33 to -0.09 | 0.00 | -0.208 |
| B | SHORT | 1,202 | 40.4% | +0.065 | -0.06 to +0.20 | 0.83 | +0.064 |
| C | LONG | 366 | 33.3% | -0.116 | -0.32 to +0.09 | 0.13 | -0.112 |
| C | SHORT | 788 | 42.9% | +0.146 | -0.03 to +0.31 | 0.95 | +0.146 |

- **The positive results are all on the SHORT side.** Strategy C's overall +0.06R combines a positive SHORT side and a negative LONG side, in a year when the market fell. It may be capturing the falling market rather than a lasting edge.
- **Longs are not losing because of funding.** Removing funding changes results by under 0.005R.
- **A short-only filter is not recommended.** It would be fitted to one mostly falling year and would probably fail in a rising market. No strategy is validated, in either direction.

## Requirements checked

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | Detection across coins and timeframes | Pass | Both directions on all 9 coins and 3 timeframes |
| 2 | Bullish and bearish structure | Pass | Mirrored within the mirror test; unit test |
| 3 | Multi-timeframe confirmation | Pass | Confirms and disagrees correctly in both directions |
| 4 | Entry, stop and take-profit | Pass, one SHORT bug fixed | 0 level errors in the isolation run. A stop so wide that a SHORT target fell below zero could crash evaluation |
| 5 | Sizing and risk | Pass | Same risk budget; liquidation on the stop side |
| 6 | Paper trading A, B, C | Pass in tests | Fills, exits and P&L signs correct. **No live LONG paper trade has occurred yet** |
| 7 | Notifications and Telegram | Pass in tests | Both directions tested. **No live LONG alert has been sent yet** |
| 8 | Backtest by direction | Gap fixed | Backtests reported only trade counts; they now report win rate, P&L, average R and profit factor per direction |

Alerts say LONG and SHORT rather than BUY and SELL.

## Limitations

- The history covers one mostly falling year, so LONG results have less support from favourable markets.
- Synthetic tests prove the mechanics, not that a signal is profitable.
- The mirror test zeroes fees and uses a tiny position, because percentage costs depend on price level. Those costs are a small real difference between directions and are included in the performance table.
