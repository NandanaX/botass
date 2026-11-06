# Why Strategy D takes about four times as many 15m trades as Strategy C

**Research only.** This report changes nothing about Strategy D, which keeps running in shadow as `D-1.0.0+fb0be3f0`. It doesn't create or activate a D v2. All of this history has been inspected before, so the results are hypothetical research data, not forward trades. Groups are descriptive and not corrected for multiple comparisons, so a group that looks good is a question for forward testing, not a finding.

Reproduce with `python -m app.research.p10_excess`. Output: `research_cache/p10_excess.json`.

## Summary

D's extra trades mostly come from accepting what the existing engine rejects. They have almost no edge before costs and lose money after them.

| Replay | Trades | Per week | Win rate | Gross | Costs | Net | 95% range for net | Total |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| Strategy D | 2,553 | 52.2 | 35.7% | +0.032R | 0.159R | **−0.127R** | −0.21 to −0.05 | −324.4R |
| Strategy C, 15m | 598 | 12.2 | 41.3% | +0.191R | 0.112R | **+0.079R** | −0.10 to +0.26 | +47.2R |

Both rows use the same exit plan. Replay period: 2025-11-27 to 2026-11-05, nine coins.

The main reasons, largest first:

1. **D ignores the engine's rejections.** 1,703 of D's 2,264 extra trades (75%) are setups the engine triggered and then rejected. D only keeps its volatility objection.
2. **D has two entry types the engine doesn't.** Structure continuation and breakout retest add 505 extra trades (22%).
3. **D's higher-timeframe rule is looser than C's.** C needs the 30m, 1H and 4H trends all aligned. D needs a score of 70 with 4H not opposed. 1,004 extra trades (44%) don't have both 4H and 1H aligned on D's own measure, and most of these have a neutral 4H.
4. **Many trades aren't independent.** 83% of D's extra trades were taken while the same coin, or a correlated coin in the same direction, was already open. Under the portfolio limits with no halts, D would take 579 trades, not 2,553.

D isn't a stricter or larger version of C. It skips 309 of C's 598 trades, and those 309 did better than the ones they share.

## How the two sets differ

Both were replayed with the same candles, 2.5 ATR stop, next-candle entry, fees, slippage, funding, 96-bar limit and simulator. The 289 trades they share came out identical to the last decimal, so the differences below come from which trades were selected, not from execution.

| Set | Trades | Win rate | Gross | Costs | Net | 95% range | Total |
|---|---:|---:|---:|---:|---:|---|---:|
| In both | 289 | 39.5% | +0.132R | 0.114R | +0.018R | −0.17 to +0.21 | +5.3R |
| D only | 2,264 | 35.2% | +0.019R | 0.164R | **−0.146R** | −0.22 to −0.06 | −329.7R |
| C only | 309 | 43.0% | +0.246R | 0.110R | +0.136R | −0.05 to +0.32 | +41.9R |

These use D's 30/40/30 exits. A single 2R exit gives the same picture: +0.024R, −0.150R and +0.144R. D-only trades were negative in all three parts of the replay (−0.20R, −0.07R and −0.10R).

**Why D skipped 309 of C's trades:**

| Reason | Trades |
|---|---:|
| D's 4-candle cooldown after an earlier trade on that coin and direction | 174 |
| D's own reward check: less than 1R of room to the nearest level | 131 |
| D's score was too low (watchlist) | 4 |

C's replay has no cooldown, so some of these C trades overlap each other, and its paper account's limits would refuse many of them.

## The extra trades, by category

### Existing engine rejection reason

Each trade is listed once, under the engine's first reason. Setups often fail several checks at once.

| Why the engine didn't give C this trade | Trades | Win rate | Gross | Costs | Net | 95% range |
|---|---:|---:|---:|---:|---:|---|
| Opposing zone too close | 351 | 38.8% | +0.091R | 0.178R | −0.087R | −0.24 to +0.06 |
| None: D's continuation entry | 349 | 33.8% | −0.044R | 0.128R | −0.172R | −0.33 to −0.01 |
| RSI outside the short band | 284 | 33.8% | −0.002R | 0.170R | −0.172R | −0.37 to +0.03 |
| MACD hadn't turned | 246 | 32.5% | +0.004R | 0.159R | −0.155R | −0.33 to +0.02 |
| Volume confirmation failed | 198 | 40.4% | +0.143R | 0.169R | −0.026R | −0.25 to +0.20 |
| RSI outside the long band | 185 | 31.4% | −0.081R | 0.178R | −0.259R | −0.45 to −0.06 |
| None: D's retest entry | 156 | 36.5% | +0.012R | 0.153R | −0.141R | −0.33 to +0.06 |
| MACD histogram not negative | 150 | 36.0% | +0.048R | 0.159R | −0.110R | −0.34 to +0.14 |
| Net reward below the minimum | 141 | 41.8% | +0.208R | 0.227R | −0.019R | −0.24 to +0.22 |
| MACD histogram not positive | 70 | 34.3% | +0.107R | 0.153R | −0.045R | −0.37 to +0.28 |
| RSI outside the pullback band | 57 | 26.3% | −0.231R | 0.165R | −0.395R | −0.74 to −0.04 |
| Engine qualified it, C's check refused | 56 | 16.1% | −0.455R | 0.140R | −0.595R | −0.83 to −0.33 |
| Candle body too small | 14 | 50.0% | +0.134R | 0.165R | −0.032R | too few |
| Overextended | 7 | 57.1% | +0.414R | 0.207R | +0.207R | too few |

Only the 7 overextended trades are positive, and that's far too few to mean anything. Every group with enough trades to judge is negative after costs. The RSI and MACD-turn rejections stand out, because those trades have little or no edge even before costs. Two groups, volume and net reward, are close to break-even after costs, and their ranges include zero.

### Market regime

| Regime at the decision | Trades | Win rate | Gross | Costs | Net | 95% range |
|---|---:|---:|---:|---:|---:|---|
| Weak bear | 533 | 39.4% | +0.142R | 0.165R | −0.022R | −0.17 to +0.12 |
| Weak bull | 325 | 36.6% | +0.098R | 0.187R | −0.089R | −0.25 to +0.06 |
| High volatility expansion | 315 | 35.6% | −0.036R | 0.098R | −0.133R | −0.31 to +0.06 |
| Strong bear | 281 | 28.8% | −0.175R | 0.153R | −0.329R | −0.50 to −0.15 |
| Conflicted | 255 | 37.6% | +0.098R | 0.178R | −0.080R | −0.26 to +0.10 |
| Exhaustion | 228 | 27.6% | −0.168R | 0.177R | −0.345R | −0.54 to −0.14 |
| Strong bull | 148 | 31.8% | −0.084R | 0.181R | −0.265R | −0.48 to −0.04 |
| Sideways | 132 | 33.3% | −0.026R | 0.168R | −0.194R | −0.44 to +0.05 |
| Low volatility consolidation | 47 | 53.2% | +0.524R | 0.322R | +0.202R | −0.21 to +0.59 |

Strong bear, exhaustion and strong bull together hold 29% of the extra trades and 64% of their loss (−210R of −330R). This is descriptive: regimes weren't tested as a filter here.

### Entry pattern

| Entry | Trades | Win rate | Gross | Costs | Net | 95% range |
|---|---:|---:|---:|---:|---:|---|
| Breakout | 1,230 | 35.9% | +0.045R | 0.173R | −0.128R | −0.22 to −0.02 |
| Pullback | 509 | 34.6% | +0.016R | 0.173R | −0.157R | −0.27 to −0.04 |
| Continuation | 349 | 33.8% | −0.044R | 0.128R | −0.172R | −0.33 to −0.01 |
| Retest | 176 | 35.2% | −0.028R | 0.154R | −0.182R | −0.37 to +0.01 |

No extra entry type is positive. Nearly all retests and every continuation are D's own entries, but most extra trades (77%) are the same breakout and pullback patterns the engine uses, just without its rejections.

### Higher-timeframe alignment

Measured with D's bias classification, relative to the trade's direction.

| Alignment at the decision | Trades | Win rate | Gross | Costs | Net | 95% range |
|---|---:|---:|---:|---:|---:|---|
| 4H neutral, 1H aligned | 775 | 32.7% | −0.059R | 0.189R | **−0.248R** | −0.34 to −0.15 |
| All three aligned | 650 | 39.2% | +0.137R | 0.129R | +0.008R | −0.14 to +0.17 |
| 4H and 1H aligned, 1D neutral | 604 | 34.8% | +0.001R | 0.165R | −0.164R | −0.30 to −0.02 |
| 4H aligned, 1H neutral | 229 | 34.5% | +0.018R | 0.181R | −0.163R | −0.35 to +0.03 |
| 4H and 1H aligned, 1D opposed | 6 | 0% | −0.900R | 0.175R | −1.075R | too few |

D's score lets a neutral 4H through, because 1D, 1H and the 15m trigger can reach 70 without it. That single group is the largest and accounts for 58% of the extra trades' loss. "All three aligned" is about break-even, but it was −0.33R in the latest part of the replay, as Phase 10 found for the strict rule, so it isn't dependable.

### Repeated signals and correlated exposure

Each trade is compared with the same strategy's earlier trades still open at its decision, assuming every one was held. This reads only what was open at that moment.

| Exposure at the decision | D-only trades | Share | Net | 95% range | C's share |
|---|---:|---:|---:|---|---:|
| Repeat: same coin and direction still open | 1,145 | 51% | −0.093R | −0.20 to +0.01 | 43% |
| Correlated: another coin open in the same direction | 736 | 33% | −0.204R | −0.31 to −0.10 | 32% |
| Independent | 383 | 17% | −0.190R | −0.32 to −0.06 | 26% |

The last column is for all of C's trades, for comparison. Clustering isn't unique to D, but it's higher. D's cooldown is only four candles, while its trades last about 34 candles on average, so one move can produce a run of "new" trades.

| Replayed through the portfolio limits | Taken | Refused, correlated | Refused, coin already open | Net per trade | Return | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| D, no halts | 579 | 1,474 | 474 | −0.144R | −35.0% | 41.1% |
| C, no halts | 199 | 287 | 111 | −0.065R | −6.7% | 10.7% |
| D, standard limits | 73 | 127 | 61 | −0.258R | −9.1%, halted | 10.3% |
| C, standard limits | 163 | 250 | 88 | −0.103R | −8.4%, halted | 10.1% |

"No halts" switches off the drawdown and daily-loss limits only. Once one-position-per-coin and correlation limits apply, D's advantage in count falls from 4.3 times to 2.9 times.

**A correction to how to read C:** this table uses the same 30/40/30 exits for both. With C's own single 2R exit, Phase 10 reported C's 15m portfolio at −4.0% and not halted. Whether C's account halts therefore depends on the exit plan, so "C didn't halt" isn't a robust result.

### Transaction costs

Comparing D and C overall, D is 0.206R per trade worse. **0.159R of that gap (77%) is lower gross edge and 0.047R (23%) is higher cost.**

D's extra trades cost more in R because they happen in quieter conditions: average ATR is 0.55% of price against 0.72% for C, so the stop is closer (1.38% against 1.79%), and the same percentage cost is a larger share of the risk. Costs aren't the root cause, though. With a gross edge of only +0.019R, the extra trades would make almost nothing even with very low costs.

### Net expectancy

All of D's loss comes from its extra trades:

- **D total:** +5.3R on shared trades, −329.7R on extra trades, for −324.4R.
- **C total:** +5.3R on shared trades, +41.9R on C-only trades, for +47.2R.

## What this doesn't show

- **It doesn't show that C works.** C's range includes zero and its trades shared with D are about break-even. C-only trades look good partly because of repeats on the same coin (+0.368R on 154 trades), and those would often be refused by the live limits.
- **It doesn't identify a better D.** With this many groups, some will look good or bad by chance. Filters chosen from this table would be tuned to data that has already been inspected.
- **It isn't a test of live behaviour.** The engine reasons are from a replay of the settings in force today (strategy version `2.0.0+52ede346`), and D's forward sample is still only a handful of trades.

## Next steps, if you choose them

Nothing here has been applied. The natural question for any later D version is whether to keep the engine's rejections and require an aligned 4H. Answering it needs a new version, with rules fixed in advance and tested forward on new data. D v1 should keep running unchanged in the meantime, so its forward record stays comparable.
