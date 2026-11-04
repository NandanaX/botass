# Phase 6 research report: baseline vs 2.5 ATR vs 2.5 ATR + HTF

**Bottom line: no strategy is validated.** Historical evidence is weak, regime-dependent and fragile under realistic portfolio limits. Forward paper trading started on 2026-11-04 and has no completed trades yet.

## What is historical and what is forward

| Evidence | Source | Status |
|---|---|---|
| **HISTORICAL** | Re-simulation of 2,048 production signals (9 coins, 15m/1h/4h, 2025-11-28 → 2026-11-03) from cached Bybit candles and funding | Every period has now been inspected, including the Phase 5 "unseen test". **None of it is an untouched test any more.** 80 configurations have been evaluated, so apparent significance is inflated. |
| **FORWARD** | Live paper trading of A, B and C from **2026-11-04 01:32 UTC**, filled at live Bybit bid/ask with fees, slippage and funding | Started. The first record is an open LINK 1h SHORT paper trade (see Known issues). **There are no completed forward trades, so there is no forward evidence yet.** |

All historical figures below are net of costs:
- taker fee 0.055% per side;
- 0.02% entry slippage and 0.05% stop slippage;
- Bybit funding.

A "cluster" counts same-direction trades within 60 minutes as one observation.

## 1. Historical comparison (every signal taken)

| | A: baseline | B: 2.5 ATR | C: 2.5 ATR + HTF |
|---|---|---|---|
| Trades (per day) | 2,048 (6.0) | 2,040 (6.0) | 1,154 (3.4) |
| Independent clusters | 1,146 | 1,145 | 586 |
| Gross average R | +0.004 | +0.052 | +0.160 |
| Fees per trade | 0.146R | 0.067R | 0.065R |
| **Average net R** | **−0.213** | **−0.049** | **+0.063** |
| 95% CI (cluster bootstrap) | −0.30 … −0.12 | −0.14 … +0.04 | **−0.08 … +0.20** |
| P(average > 0) | 0.00 | 0.15 | 0.80 |
| Profit factor | 0.74 | 0.93 | 1.10 |
| Stop-out rate | 66% | 61% | 58% |
| Average holding time | 8.6 bars | 31 bars | 31 bars |
| Dev / val / test average R | −0.26 / −0.13 / −0.18 | −0.10 / +0.00 / +0.01 | +0.01 / +0.29 / −0.04 |
| Months positive | 2 / 13 | 5 / 12 | 6 / 12 |
| Best month's share of positive R | 85% | 50% | 49% |
| Max drawdown, every signal taken | 445R | 202R | 84R |

## 2. Robustness of the stop multiplier

Average net R by stop width (all on the same signals, fixed risk):

| Stop | 1.5 × ATR | 2.0 × ATR | 2.5 × ATR | 3.0 × ATR | 3.5 × ATR |
|---|---|---|---|---|---|
| B | −0.157 | −0.076 | −0.049 | −0.062 | −0.052 |
| C | −0.082 | +0.019 | +0.063 | +0.070 | +0.076 |

- **The value 2.5 was not cherry-picked.** C is positive and flat from 2.0 to 3.5 ATR. It beats A in 68 of 70 symbol, timeframe and period cells, and B beats A in 66 of 75.
- **The improvement over A is robust;** Holm-adjusted p ≈ 0 for 2.0–3.5 ATR.
- **Positive expectancy is not.** No configuration has a cluster-bootstrap interval above zero.
- **C depends on the market regime:**
  - strong or weak bear: +0.31 / +0.21R;
  - sideways: +0.18R;
  - strong bull: −0.51R;
  - low-volatility: −0.33R;
  - high-volatility: −0.18R.

  The historical year was mostly falling, so C's edge may simply be "shorting a downtrend".
- **By coin (C):** 6 of 9 coins are positive; BNB, XRP, BTC and ETH are negative.

## 3. Stress tests (average net R per trade)

| Scenario | A | B | C |
|---|---|---|---|
| Base | −0.213 | −0.049 | +0.063 |
| Fees × 2 | −0.358 | −0.116 | −0.003 |
| Slippage × 4 | −0.424 | −0.147 | −0.030 |
| Spread +0.10% | −0.323 | −0.100 | +0.014 |
| Entry delayed 1 / 2 bars | −0.596 / −0.313 | −0.050 / −0.096 | +0.075 / +0.015 |
| Stop executed at next open | −0.351 | −0.116 | −0.007 |
| Volatility slippage (0.1 × ATR%) | −0.365 | −0.116 | −0.003 |
| Correlated reversal (each cluster takes its worst outcome) | −0.672 | −0.399 | **−0.315** |
| Combined adverse | −2.114 | −0.257 | −0.114 |
| 25% of signals lost (missing data) | −0.214 | −0.049 | +0.061 (5th percentile +0.03) |
| **Break-even cost multiplier** | 0.02 | 0.51 | **1.65** |

C survives about 1.65 × the assumed costs. It does **not** survive doubled fees, a delayed stop, volatility-scaled slippage, or the correlated-reversal assumption.

## 4. Portfolio level

This historical replay applies the forward risk limits: 0.5% risk per trade, one position per coin, at most one correlated same-direction position, 2% daily loss and a 10% drawdown breaker.

| | Trades taken | Average net R | Return | Max drawdown |
|---|---|---|---|---|
| A | 60, then halted by the drawdown breaker | −0.29 | −8.4% | 10.3% |
| B | 41, then halted | −0.55 | −10.7% | 11.4% |
| C | 252 | **−0.008** | −1.8% | 10.0% |
| A, no drawdown breaker | 745 | −0.21 | −54.8% | 56.1% |
| B, no drawdown breaker | 363 | −0.15 | −24.8% | 34.8% |

**C's positive per-signal expectancy disappears under realistic portfolio limits.** The limits keep the first signal of each correlated burst and skip the rest. Historically, the later signals in a burst were the better ones. A portfolio that takes every overlapping signal would have far higher risk:
- median drawdown 90R, and a 94% chance of a drawdown beyond 50R;
- 50R is 25% of equity at 0.5% risk per trade.

## 5. Answers

1. **Does any strategy show repeatable positive net expectancy under realistic costs and acceptable portfolio risk?** **No.**
   - A and B are negative.
   - C is weakly positive per signal (+0.063R, CI −0.08 … +0.20). It is concentrated in falling markets, fails several stress tests, and is about breakeven once portfolio risk limits apply.
2. **Is any strategy validated?** No. All three are `INSUFFICIENT_FORWARD_DATA` at the promotion gate.
3. **Recommendation:**
   - Keep A as the reproducible baseline, and keep real money and enforcement disabled.
   - Let the forward tracker collect at least 60 days and 40 independent clusters.
   - Judge C only on that forward evidence, with special attention to rising-market periods, which history barely covers.

## Known issues

- **The first forward signal was blocked from the paper portfolios by a clock-skew bug,** now fixed with a regression test. LINK 1h SHORT, detected 2026-11-04 01:32 UTC, was blocked as "no fresh quote" by a clock-skew bug. The cause: quote age compared the local clock with the exchange-synchronised clock, and this PC runs about 15 s ahead of Bybit. The fill also came from the last price rather than bid/ask.

  The records were kept unchanged and annotated rather than rewritten after the fact, so this signal counts at signal level but not at portfolio level.
- **Signals at startup can be late.** A signal detected at startup can be up to one candle old. Its detection delay is recorded, and anything later than one candle is MISSED.
- **Funding is partly estimated.** Live funding is estimated until a trade closes; then Bybit's funding history replaces it when available.
- **Historical portfolio replays are simplified.** They measure equity on closed trades only, without intra-trade marks.
