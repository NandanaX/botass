# Phase 9 research report: signal quality, stops, targets and position sizing

**Bottom line: nothing tested here shows a reliable profitable edge, so every variant stays `Unvalidated`.** Before costs the signals average about 0R. Wider stops lose less because they reduce the cost per trade, not because they find better trades. Two ideas are worth a forward paper test, and neither should change live signals yet.

## What this evidence is

| Evidence | Source | How much it can prove |
|---|---|---|
| **Stored signal outcomes** | 16 signals, Nov 3–5, gross | Very little. They are one two-day market move. |
| **Historical research** | The live engine (`2.0.0+52ede346`) replayed over 2,721 signals: 9 coins, 15m/30m/1h/4h, 2025-11-27 to 2026-11-05 | Stability only. Earlier phases already inspected this data, and 124 configurations were tested here, so some improvement is expected by chance. |
| **Forward paper trades** | Strategies A, B and C at live prices, since 2026-11-04 | The only new evidence, but far too small so far: 7, 7 and 5 closed trades. |

All research figures are net of fees (0.055% per side), slippage (0.02% entry, 0.05% stop) and funding. R is planned risk, so a wider stop means a smaller position, not more risk. Periods: dev to 2026-05-18, val to 2026-08-11, test to 2026-11-05. Only 8 signals fall after the data earlier phases looked at. They average −1.29R, and none of them passes the higher-timeframe filter, so that sample can't test anything.

## Answers

### 1. Are the current stops too tight?

**Tight against volatility and costs, yes. But widening them doesn't produce a profit.**

| Measure (2,719 historical signals) | Original stop |
|---|---|
| Median stop distance | 1.13 ATR; 34% are under 1 ATR |
| Inside normal recent movement (80th percentile of 3-bar moves) | 56% |
| Stopped out, then reached the original target within 96 bars | 53% (some of this happens by chance; no baseline was measured) |
| Costs per trade | 0.145R, against 0.065R at 2.5 ATR |

Even at 3.5 ATR, 55% of trades are still stopped out, against 67% with the original stop. So wider stops avoid some noise but don't turn losers into winners.

**Recent signals point the other way.** The 16 stored signals match your count (5 targets, 11 stops, −1.03R). With costs, those same trades come to **−4.77R**. The median stop was 1.30 ATR and 0.80% of price, and costs took about 23% of the risk. With a 2.5 ATR stop the same 16 signals would have won **0 times and lost 18.09R**, and the forward paper account agrees: B lost all 7 closed trades and C all 5, while A made +2.12R on 7. That is one short market move, so it doesn't settle anything, but it is a clear warning against assuming wider is better.

### 2. Which stop methods perform best out of sample?

All use the same entries and a 2R target.

| Method | Trades | Avg net | 95% range | Dev / val / test | Stopped | Drawdown | Compared with original |
|---|---|---|---|---|---|---|---|
| A: original | 2,719 | −0.223R | −0.31 to −0.14 | −0.30 / −0.09 / −0.19 | 67% | 605R | Baseline |
| B: 2.5 ATR | 2,716 | −0.064R | −0.16 to +0.04 | −0.11 / +0.08 / −0.10 | 62% | 271R | Better in every period |
| C: 3.0 ATR | 2,715 | −0.058R | −0.16 to +0.05 | −0.09 / +0.10 / −0.13 | 59% | 274R | Promising |
| D: 3.5 ATR | 2,710 | −0.059R | −0.15 to +0.04 | −0.09 / +0.09 / −0.14 | 55% | 259R | Promising |
| E: structural + 0.5 ATR | 2,203 | −0.110R | −0.21 to −0.01 | −0.09 / −0.00 / −0.25 | 62% | 360R | No stable improvement |
| F: hybrid | 2,338 | −0.103R | −0.21 to −0.00 | −0.10 / +0.01 / −0.21 | 62% | 342R | No stable improvement |

- **B, C and D are effectively tied, and all are still negative.** Their ranges include zero. There is no evidence for preferring one multiple.
- **The structure-based stops did worse** and refused 19% (E) and 14% (F) of trades.
- **The most recent fold (Aug 28 onward) is negative for every method:** −0.24R for A, −0.23R for B, −0.30R for C, −0.32R for D.
- **Choosing the best method from past results** picked E, E, D, D across the folds and made −0.126R against −0.217R for keeping the original. That is better, but still a loss, and in the last fold it was worse than the original.

Gross of costs, the averages are −0.006R (A), +0.034R (B), +0.024R (C) and +0.012R (D). **That is the central finding: there is almost no edge before costs.**

### 3. Do larger targets improve net profit?

**No.** Full 3R against full 2R: −0.223R against −0.223R on the original stop, and −0.066R against −0.064R at 2.5 ATR. Reaching further wins less often by about as much as it pays. Structural targets (the nearest opposing zone) were worse on the original stop (−0.243R). At 2.5 ATR they looked better (−0.045R), but only by skipping 32% of trades, and the result was unstable: −0.18R in dev, +0.31R in val, −0.08R in test.

### 4. Do partial take-profits improve results?

**Not materially.**

| Exit | Original stop | 2.5 ATR stop |
|---|---|---|
| Full at 2R | −0.223R | −0.064R |
| Full at 3R | −0.223R | −0.066R |
| 50% at 1R, 50% at 2R | −0.214R | −0.072R |
| 30% at 1R, 40% at 2R, 30% at 3R | −0.217R | −0.071R |
| Structural | −0.243R | −0.045R (32% fewer trades) |

Partials change the feel more than the result. On the 2.5 ATR stop, 30/40/30 shortened the worst losing streak from 49 to 30 trades, but the average fell slightly. Choosing an exit from past results didn't help on either stop.

### 5. How is position quantity calculated?

```
risk budget = equity x risk %
entry fill  = entry -/+ entry slippage            (always adverse)
stop fill   = stop  -/+ stop slippage             (always adverse)
loss/unit   = |entry fill - stop fill| + entry fee x entry fill + exit fee x stop fill
quantity    = risk budget / (loss/unit), rounded DOWN to the exchange step
notional    = quantity x entry
margin      = notional / leverage
```

Example: BTCUSDT LONG, 10,000 USDT equity, 0.5% risk, entry 80,000, stop 79,000, 5x, step 0.001:

| | |
|---|---|
| Risk budget | 50.00 USDT |
| Loss per BTC | 80,016.0 − 78,960.5 + 44.01 + 43.43 = 1,142.94 USDT |
| Quantity | 50 / 1,142.94 = 0.0437, rounded down to **0.043 BTC** |
| Notional / margin at 5x | 3,440.00 / 688.00 USDT |
| Planned loss at the stop | 49.15 USDT (0.49% of equity) |
| At a target of 82,000 | 85.31 gross, 81.48 net of fees, 1.66R net |
| If the stop fills 1% worse | 81.40 USDT |

A SHORT mirrors this. Moving the stop to 78,000 cuts the quantity to 0.023 BTC for about the same 49 USDT risk.

The trade is **refused**, never rounded up, if the smallest allowed order would lose more than the budget. It is also refused if margin is short, liquidation comes before the stop, the exchange leverage limit is exceeded, or the 2% open-risk limit would be breached. Leverage and risk are never raised automatically. **A planned loss is not a maximum loss:** gaps, thin liquidity, liquidation and outages can cost more, and funding isn't included.

### 6. Which signals should be rejected, and why?

The entry gate classifies by objective conditions, but **history supports very little of it as a filter.**

| Skip this class | Share of signals | Its average | Effect on the original stop | Effect on 2.5 ATR | Reading |
|---|---|---|---|---|---|
| Conflicting timeframes | 10% | −0.43R / −0.32R | −0.200R, not stable | −0.037R, not stable | The worst class, and the most defensible skip, but it didn't help in the test period on the original stop |
| Overextended | 36% | −0.19R / −0.02R | Worse (−0.240R) | Worse (−0.078R) | **No support.** These trades did no worse than the rest |
| Wait for retest | 2% | −0.01R / +0.22R | Worse (−0.227R) | Worse (−0.067R) | **No support** |
| Insufficient reward (2.5 ATR only) | 41% | −0.04R | Doesn't apply | Worse (−0.078R) | **No support**, and unstable |
| Everything except Qualified | 48–78% skipped | −0.21R / −0.05R | No improvement | No improvement | **Don't use as a filter** |

So the card shows these classes as information and says when history doesn't support skipping. The rules that do hold are mechanical: refuse trades the risk budget can't pay for, and don't stack near-duplicate risk (question 8 below and the section on duplicates).

**The room rule barely separates good from bad.** Setups refused only because an opposing zone was close average −0.248R, against −0.223R for those that qualified.

**Correction: the short lead failed.** A 30-day replay suggested that shorts refused only for that reason were profitable (+0.135R). Over 13 months those setups average **−0.214R** (5,781 setups, range −0.29 to −0.14, negative in every period). The earlier figure came from a single mostly falling month. Its paper tracker was removed on 2026-11-06, before it had recorded any trade. The earlier suggestion of 1h as the best timeframe also doesn't hold: on the original stop 15m, 30m, 1h and 4h average −0.24R, −0.24R, −0.19R and −0.18R.

### 7 and 8. How do changes affect expectancy and drawdown?

| Variant | Trades/day | Avg net | Profit factor | Drawdown, all signals | Worst streak | Portfolio trades | Portfolio return | Portfolio drawdown |
|---|---|---|---|---|---|---|---|---|
| Baseline A | 7.9 | −0.223R | 0.73 | 605R | 37 | 37, then halted | −10.9% | 10.9% |
| 2.5 ATR | 7.9 | −0.064R | 0.91 | 271R | 49 | 57, then halted | −10.1% | 10.4% |
| 3.0 ATR | 7.9 | −0.058R | 0.91 | 274R | 50 | 62, then halted | −10.1% | 10.1% |
| Hybrid (F) | 6.8 | −0.103R | 0.86 | 342R | 44 | 142, then halted | −10.8% | 10.8% |
| 2.5 ATR + gate | 1.7 | −0.055R | 0.92 | 104R | 27 | 151, then halted | −7.3% | 10.4% |
| 2.5 ATR + HTF | 4.8 | +0.049R | 1.08 | 146R | 49 | 181, then halted | −8.9% | 10.2% |
| 3.0 ATR + HTF | 4.8 | +0.069R | 1.11 | 156R | 47 | 230, then halted | −4.4% | 10.3% |
| Hybrid + HTF | 4.1 | −0.027R | 0.96 | 201R | 30 | 93, then halted | −3.7% | 10.4% |
| 2.5 ATR + HTF + 30/40/30 | 4.8 | +0.037R | 1.06 | 134R | 31 | 226 | **+0.3%** | **8.6%** |
| Breakout retest (touch counts as a fill) | 0.7 | +0.056R | 1.08 | 26R | 9 | 186 | +10.7% | 8.8% |

- **Portfolio** means the live paper limits at 0.5% risk per trade, including the 10% drawdown breaker. "Halted" means the breaker stopped new trades.
- **No range excludes zero.** The best, 3.0 ATR + HTF, has a range of −0.08 to +0.22. After correcting for repeated testing, none of the improvements over the baseline is significant (the smallest adjusted p-value is 0.14).
- **The HTF variants aren't stable.** 3.0 ATR + HTF made +0.39R in val and −0.16R in test, and −0.41R in the most recent fold. They also contain no 4h signals, because 4h has no higher timeframe to confirm it.
- **Direction dominates.** Longs lose under every variant (−0.17R to −0.34R). Shorts gain with wider stops (+0.08R to +0.19R). Most of the past year was falling, so this may simply be "shorting a downtrend". It isn't evidence for trading only one side: in strong bull markets 3.0 ATR + HTF averages −0.43R.

### 9. Does any improvement hold under adverse execution?

**No. Nothing survives every test.**

| Scenario (avg net R) | 2.5 ATR + HTF | 3.0 ATR + HTF | + 30/40/30 | Retest |
|---|---|---|---|---|
| Base | +0.049 | +0.069 | +0.037 | +0.056 |
| Fees x2 | −0.014 | +0.017 | −0.027 | −0.196 |
| Slippage x4 | −0.040 | −0.008 | −0.052 | −0.133 |
| Stop executed at the next open | −0.021 | +0.014 | −0.031 | −0.131 |
| Volatility slippage | −0.017 | +0.013 | −0.029 | −0.036 |
| Entry delayed 1 bar | +0.060 | +0.077 | +0.049 | Not applicable |
| Must trade 0.1 ATR past the limit | Not applicable | Not applicable | Not applicable | **−0.093** |
| Must trade 0.25 ATR past the limit | Not applicable | Not applicable | Not applicable | **−0.243** |
| Combined adverse | −0.139 | −0.064 | −0.148 | −0.540 |

3.0 ATR + HTF comes closest, failing only slippage x4 and the combined case.

**The retest result depends on its fill assumption.** It fills only 27% of breakouts with a tight stop (0.6 ATR, so costs take 0.25R per trade). On those same signals, next-open entries lose 0.405R, so it looks excellent, but that comparison flatters it: those are exactly the signals where price came straight back. If price must trade just 0.1 ATR past the limit, the result turns negative, and real limit orders often need that.

### 10. Which variants are worth testing in shadow mode?

Both are running since 2026-11-06 as forward paper tests with no alerts, shown on the Validation page. On 300 real signals replayed through them, all 425 closed records matched the study's simulation exactly, so forward and replay results can be compared. Funding isn't charged in the forward tests.

1. **Breakout retest entries.** The largest effect and the only variant whose result depends on something a replay can't measure: real limit fills. A forward test answers exactly that.
2. **2.5 ATR + HTF with 30/40/30 exits.** It differs from locked strategy C only in the exit, so the comparison is clean, and it is the only next-open variant that avoided the drawdown breaker.

**Not worth testing:** the entry gate as a filter, stops E and F, 3R targets, structural exits and the short lead. 3.0 ATR + HTF is too close to C to justify a separate test.

## Duplicate and correlated signals

| Signal also present while… | Share | Avg with | Avg without | Outcome correlation |
|---|---|---|---|---|
| Same coin, another timeframe open | 32% | −0.232R | −0.218R | 0.49 |
| Same coin, same zone open | 28% | −0.207R | −0.229R | **0.78** |
| A correlated coin open | 69% | −0.232R | −0.203R | 0.38 |
| Any same-direction position open | 81% | −0.221R | −0.232R | Not measured |

Repeats aren't worse on average, but they aren't independent. A same-zone repeat is nearly the same trade. The 2,719 signals behave like about 1,883 independent ones; 71% arrive in a cluster of at least two, with a within-cluster correlation of 0.40.

**Removing the limits does more harm than good.** Without them the baseline falls 84% with an 84% drawdown, against the limits halting it at 10.9%. 3.0 ATR + HTF goes from a 10.3% to a 26.5% drawdown and earns less.

**Portfolio-aware ranking had little effect.** It only matters when several signals share a decision time (about 20% of them). It helped some variants slightly (3.0 ATR + HTF −4.4% to −3.4%) and hurt others (Hybrid + HTF −3.7% to −6.3%). It uses gate class and room, never the technical score. It is measured here only and isn't used live.

## The recent 16 signals

Rebuilt from data available at each decision time, and all 16 matched what was stored.

| Reading | Result |
|---|---|
| Direction | 14 short, 2 long |
| Stored (gross) | 5 wins, 11 stops, −1.03R |
| Re-simulated with costs | 5 wins, 11 stops, −4.77R; average MFE 1.13R, MAE 1.12R |
| Stopped trades that first reached +1R | 2 of 11 |
| Stop inside recent noise | 6 of 16 |
| Gate on the original stop | 7 Qualified (−2.74R), 7 Overextended (−2.63R), 2 Conflicting (+0.60R) |
| Other stops, same signals, 2R target | 2.5 ATR −18.09R; 3.0 ATR −17.73R; 3.5 ATR −17.48R; hybrid −17.01R (15 trades); structural −13.07R (14 trades, 1 win). The structural and hybrid methods refused the rest |

Signal-by-signal detail, including higher-timeframe trend, structure, relative volume, funding, MFE, MAE and paper fills, is in `research_cache/p9_audit.json`.

**Replay against live, Nov 3–5.** The replay finds 33 qualified signals, including all 16 the live system stored, so research and live agree where both were running. The other 17:

- **8 on Nov 3–4:** 7 on 30m, which wasn't being scanned yet, and 1 while the PC was asleep.
- **9 on Nov 5:** all inside the PC's sleep from 02:26 to 13:49 UTC. The 8 that have finished were all losers (average −1.29R). That result is luck, not a reason to accept the gap: while the PC sleeps, nothing is scanned and no alert goes out.

## Limitations

- Fills are simulated from candles. A bar touching both levels counts as a stop first, which is cautious.
- Target exits pay the taker fee, which is cautious. Paper trading uses the lower maker fee.
- Research prices aren't rounded to ticks; the live card's are.
- Trades still open when the data ends aren't scored (2 to 11 per stop method), which slightly favours quick outcomes at the very end of the data.
- 13 months is one market cycle at most, and it was mostly falling.
- The gate's thresholds were set before seeing results and weren't tuned, so a gate class failing here isn't the result of bad tuning, but other thresholds weren't explored either.

## What changed in the application

- **Trade Setup page:** a new experimental Trade plan card with stops, targets, sizing, gate result and reasoning. Equity, risk, leverage, stop method and targets are editable. It defaults to the paper account's 0.5% risk.
- **Signal Intelligence Lab:** a new Phase 9 section with these results.
- **Code:** `app/planning/` (stops, targets, sizing, gate, context, card), `app/research/p9_*.py` (replay, study, audit), `app/api/routes_planning.py`.
- **Not changed:** strategies A, B and C, live signals, the scoring, stored history, settings, enforcement mode. No database migration. No real-money feature.

## Reproducing this

```
python -m app.research.fetch --days 403 --minute-days 5 --timeframes 4h 1h 30m 15m
python -m app.research.p9_candidates   # about 5 minutes
python -m app.research.p9_study        # about 1 minute
python -m app.research.p9_audit
```

Results are written to `research_cache/p9_latest.json` and `research_cache/p9_audit.json`.
