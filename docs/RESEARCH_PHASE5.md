# Phase 5 research report: signal failures, evaluation correctness and strategy changes

Generated from study `study_2.0.0_77d75b51_*` (strategy `2.0.0+77d75b51`). The data is Bybit USDT perpetuals:
- 9 coins: BTC, ETH, SOL, XRP, DOGE, BNB, LINK, AVAX and SUI;
- 15m, 1h and 4h candles;
- signals from 2025-11-28 to 2026-11-03.

All results are **hypothetical**. They replay signals against historical candles, and none of them are trades. Every R figure is **net of costs**:
- 0.055% taker fee per side;
- 0.02% entry slippage and 0.05% stop slippage;
- Bybit funding history.

Re-run the study from the Signal Intelligence Lab (`/lab`), or from the command line:

```
python -m app.research.fetch --days 400 --minute-days 2
python -m app.research.build
python -m app.research.study
```

## 1. Why did the November 3, 2026 signals fail?

All 9 signals were SHORTs (times are Sri Lanka time). The forensics engine rebuilt each one from the candles that existed when it was generated.

| Time | Signal | Reported | Verified net | Main measured causes |
|---|---|---|---|---|
| 08:30 | DOGE 1h | −1R | −1.19R | 7 correlated same-direction signals within 60 minutes |
| 08:30 | AVAX 15m | −1R | −1.25R | correlated cluster; extended entry (suspected) |
| 09:00 | DOGE 15m | −1R | −1.34R | correlated cluster (DOGE 1h already short) |
| 09:00 | AVAX 15m | −1R | −1.21R | correlated cluster (AVAX already short); late entry: 1.6 ATR beyond EMA20, 0.8 ATR above the 24h low, which held |
| 09:00 | SUI 15m | −1R | −1.18R | late entry, 0.3 ATR above the 24h low (held); correlated cluster |
| 10:15 | LINK 15m | +2R | +1.73R | the only winner |
| 11:30 | SOL 1h | −1R | −1.13R | correlated cluster |
| 11:45 | SOL 15m | −1R | −1.46R | failed breakdown (closed back through the zone); late entry; stop inside normal noise (0.95 vs 1.48 ATR) |
| 17:15 | LINK 15m | −1R | −1.46R | stop inside noise; the target was reached after the stop-out; the 15m support zone held |

**Summary:** one market-wide down-swing produced 7 near-simultaneous shorts on highly correlated coins (1h return correlation 0.83–0.94). Several of them were sold close to the 24-hour low, and that low held. In effect, the day was one losing bet taken 7 times.

**These explanations do not generalise.** Over the full history, "late" entries and entries near the opposing swing performed *slightly better* than the rest (section 3). Section 3 lists which causes hold up across the full history.

## 2. Were any failures caused by software or backtesting bugs?

**No.** No outcome-calculation bug was found. The checks were:
- **Reconstruction.** All 9 signals rebuild exactly: same direction, strategy, entry, stop, target and score. This holds on the scanner's 1000-candle window and on the full history, so neither look-ahead nor window length changes any decision.
- **Outcome replay.** All 9 stored outcomes reproduce exactly. 1-minute replays agree on the exit for all 9, and none had a candle where both the stop and target were touched.
- **Regression tests** (`tests/test_research.py`) cover:
  - entry at the next open, never on the signal candle;
  - a stop touched on the signal candle being ignored;
  - entry gaps beyond the stop (the signal is invalidated);
  - stop-first handling of ambiguous candles, plus resolution with lower-timeframe candles;
  - gap fills at the open;
  - stop slippage;
  - the sign of funding for longs and shorts;
  - net R being below gross R;
  - non-repainting of reconstruction, regimes and noise windows.

**Methodology gaps found and fixed:**
1. **Live outcomes were gross.** The live evaluator reported R before costs, so the reported −6R was really **−8.49R net**. Each loss cost an extra 0.13–0.46R, because the stops are tight relative to fees. Live evaluation now also stores `net_r_multiple` and `cost_r` (methodology v2).
2. **Overlapping signals counted as independent trades.** The live scanner sends a new signal while an earlier one on the same coin is still open (AVAX at 08:30 and 09:00; DOGE on both 1h and 15m). The backtester does not allow this. The decision engine's portfolio rules now treat repeats as the same exposure.
3. **The stop was assumed to fill exactly at its price.** Research outcomes now apply stop slippage.

## 3. Which failure causes have strong evidence across history?

This covers 2,048 filled baseline trades. "With / without" is the average net R per trade for trades that had the condition and trades that didn't.

| Condition at the decision | With | Without | Evidence |
|---|---|---|---|
| Stop inside 3-bar noise | −0.26R | −0.16R | **Supported** (consistent with wider stops helping) |
| Unfavourable regime (conflicted, sideways, exhaustion or high-volatility) | −0.26R | −0.18R | weak |
| 4h regime against the trade | −0.31R | −0.21R | weak (n = 110) |
| Late entry (extended or exhausted) | −0.18R | −0.24R | **contradicted** |
| Near the opposing swing (< 1 ATR) | −0.09R | −0.23R | **contradicted** |
| Clustered with other coins' signals | −0.16R | −0.25R | contradicted *as an expectancy cause*; it is still a *risk* cause (losses arrive together) |
| BTC regime against the trade | −0.14R | −0.22R | contradicted |

**The structural cause is cost drag.** The strategy's **gross** expectancy is +0.004R per trade, which is no edge. Costs average 0.22R per trade with the production hybrid stop (about 1.2 ATR), which on 15m candles is a small percentage of price.

## 4–6. Proposed improvements and their results

Each hypothesis changes one thing relative to the live baseline. The rules:
- Thresholds are chosen on the **development** period only (2025-11-28 to 2026-05-17).
- They are then checked on the **validation** period (to 2026-08-10) and the **unseen test** period (to 2026-11-03).
- Verdicts are based on a day-block bootstrap of the out-of-sample difference.
- A fixed rule must also improve the development period, which then acts as a third independent test.

The baseline averages −0.265R per trade in development, −0.131R in validation and −0.183R in test. Out of sample, that is −0.157R per trade (95% CI −0.245 to −0.063), −156R in total, with a 212R maximum drawdown.

| Key | Change | Validation | Test | Verdict |
|---|---|---|---|---|
| **H2E** | ATR stop at **2.5 × ATR**, 2R target (multiplier chosen on development; risk per trade unchanged) | +0.003 | +0.007 | **VALIDATED**: P = 1.00, improves all 3 periods |
| H2A | ATR stop at 1.5 × ATR | −0.085 | −0.135 | PROMISING |
| H4 | Require higher-timeframe EMA agreement (this also drops 4h signals) | +0.025 | −0.151 | PROMISING: P = 0.93 |
| H4e | Same as H4, but keep signals without a higher timeframe | +0.018 | −0.239 | not validated |
| H1a / H1b / H1c | Late-entry, exhaustion and extension filters | −0.17 / −0.13 / −0.19 | −0.31 / −0.19 / −0.29 | **not validated**; they make results worse |
| H1d | Filter entries near the opposing swing | −0.134 | −0.195 | not validated |
| H2B / H2D | Pure structure stop / noise-adaptive stop | −0.23 / −0.21 | −0.28 / −0.27 | not validated (worse) |
| H3 / H3b | Portfolio and correlation limits | −0.227 / −0.218 | −0.039 / −0.039 | not validated for expectancy. Drawdown falls 212R → 46R, but that is only marginally better than random subsets of the same size (percentile 0.11) |
| H4b / H4c / H4d | 4h regime, BTC regime, no clustered signals | — | — | not validated |
| H5 | Regime-aware strategy selection (learned on development) | +0.055 | −0.616 | not validated; overfits |
| H6 | Cost-drag filter (learned on development) | +0.407 | −0.449 | not validated; unstable |
| H7 / S60–S80 | Minimum score | — | — | not validated. It helps validation and test but hurts development: the **score is not stable** |
| H8 | Drop 4h signals | −0.146 | −0.113 | not validated |
| ML | Logistic quality filter (purged walk-forward) | — | — | **not validated**: Brier worse than climatology in every fold, AUC 0.46–0.53 |

Walk-forward checks agree. The extension filter (H1c) helped in only 1 of 4 folds, and regime selection (H5) lost in 2 of the 3 folds with enough data.

**Supporting analyses** (all in the Lab):
- **ATR stop sensitivity is monotonic** in every period: 1.0 / 1.5 / 2.0 / 2.5 × ATR give −0.29 / −0.20 / −0.14 / −0.10R in development, and cost per trade falls from 0.25R to 0.10R.
- **The score has a weak, unstable relationship with outcomes.** Spearman correlation is 0.11. The 81–100 band averages −0.01R out of sample but −0.18R over all periods.
- **Production strategies, out of sample:** breakout −0.026R, pullback −0.221R.
- **Research families are all negative out of sample:** breakout-retest −0.36R, momentum continuation −0.31R, range reversal −0.66R.

## 7–9. New strategy versus the original

The combination **H2E + H4** (2.5 ATR stop, 2R target, higher-timeframe confirmation) is the only one VALIDATED as an *improvement*. It improves all three periods, with bootstrap P = 0.99 and a difference CI of +0.07 to +0.46R.

| Period | Original | Proposed |
|---|---|---|
| Development | −0.265R (n = 1054) | +0.014R (n = 578) |
| Validation | −0.131R (n = 488) | +0.289R (n = 270) |
| Unseen test | −0.183R (n = 506) | **−0.045R** (n = 306) |
| Out of sample | −0.157R; −156R total; DD 212R; PF 0.80 | +0.112R; +64R total; DD 72R; PF 1.18 |

- **Profitability after realistic costs:** better, but **not demonstrated**. The out-of-sample 95% CI is −0.003 to +0.23R. The unseen test period is still negative. Results are lumpy: one month (June 2026) contributes +99R. 4 of 9 coins are negative out of sample.
- **Maximum drawdown:** out-of-sample drawdown falls from 212R to 72R. Part of that comes from trading fewer signals. The worst losing streak is still 30 trades.

## 10. Are results strong enough for shadow-mode testing?

**For shadow mode, yes. For real money, no.** The decision engine runs in shadow mode on every new QUALIFIED signal:
- It applies the validated combination's filter (H4).
- It proposes the 2.5 ATR stop as a shadow plan.
- It applies portfolio risk controls: one position per coin, correlated exposure, daily loss and losing streaks.

It stores its verdict with the signal and shows it in the Telegram alert, but it blocks nothing unless you set Settings → Decision engine → *Enforce*. On November 3 it would have taken 1 trade (−1.09R net) instead of 9 (−8.49R net). That is one day and proves nothing on its own. Keep the baseline running, compare the two for several weeks, and re-run the study before enforcing anything.

## Limitations

- **Coverage.** One year of history and 9 large-cap coins, in a market that fell over the period (shorts did better). The results may not hold in other regimes.
- **Candle-based fills.** Outcomes come from OHLC candles; 1-minute data is only used for recent signals. No order book, latency or partial fills are modelled. Liquidation is checked at planning time but not simulated.
- **Funding.** Historical Bybit funding is used in research; live net R excludes funding.
- **Multiple comparisons.** About 25 variants were tested. Bootstrap probabilities are not corrected for this, which is one reason only a change that improves *every* period counts as validated.
- **Grid edge.** The chosen ATR multiplier (2.5) is at the edge of the tested grid. Wider stops were not tested, and holding times grow with them.
- **Correlated trades.** Overlapping baseline trades are correlated; the day-block bootstrap only partly accounts for this.
- **Survivorship.** Coins were chosen in 2026 (survivorship bias). Instrument rules come from today's Bybit settings.
