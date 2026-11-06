# Phase 10: 15-minute multi-timeframe strategy (Strategy D)

**Bottom line: Strategy D did not beat Strategy C, and it is not validated.** Its default loses 0.127R per trade after costs, with a 95% range of −0.21R to −0.05R. It also trades more than C, not less. It runs in forward shadow mode only, with no orders and no Telegram alerts, and its settings were not tuned after these results.

All figures are hypothetical replays of 2025-11-27 to 2026-11-05 on 9 coins. All of that history has been inspected in earlier phases, so it is research data, not an untouched test. 46 configurations were tested. Costs, execution and risk limits are the same for every row, and funding is included.

## What was built

| Part | Where |
|---|---|
| Parameters, immutable and versioned (`D-1.0.0+fb0be3f0`) | `backend/app/strategy_d/params.py` |
| Bias per timeframe, using only closed candles | `backend/app/strategy_d/context.py` |
| Weighted score and alternative rules | `backend/app/strategy_d/confluence.py` |
| 15m entries, plan and decision | `backend/app/strategy_d/engine.py` |
| Forward shadow run, paper account, notifications | `backend/app/strategy_d/service.py` |
| Historical study | `backend/app/research/p10_study.py` |
| Screens | `/strategy-d`, plus a 15m decisions card on Overview and Market Scanner and a higher-timeframe summary on 15m signals and Chart Analysis |

15m is now the display default on Market Scanner, Signal History, Trading Signals and Chart Analysis, and Overview already used it. Other timeframes stay selectable. This default changes no strategy rule. The Intelligence Lab has no timeframe selector, so nothing changed there.

**The rules, fixed before any result was seen:**

- **Entries:** only 15m setups can trade: breakout, breakout retest, trend pullback or structure continuation. Breakouts and pullbacks come from the existing engine, including setups it rejected only for reasons tied to its own stop. D applies its own stop and room checks instead.
- **Bias:** 1D, 4H and 1H each get one bias, so correlated indicators aren't counted repeatedly. RSI and MACD never change it; momentum against an intact trend marks a pullback.
- **Score:** 1D 15, 4H 30, 1H 30, 15m entry 25. A neutral timeframe earns 40% of its weight. This measures rule agreement. It isn't a probability.
- **Qualified:** a confirmed 15m entry, a score of at least 70, 4H not opposed, at least 1R of room to the nearest opposing zone, and a pass from the portfolio limits. A score of 55 to 69 is Watchlist. Anything else is Rejected, with its reason.
- **Plan:** 2.5 ATR stop, with 30% at 1R, 40% at 2R and 30% at 3R.
- **Risk:** the standard limits are unchanged: 0.5% per trade, 2% open risk, correlated positions, daily loss and the drawdown breaker.

## Strategy D against the baselines

| Rules | Trades | Per week | Win rate | Avg net | 95% range | Profit factor | Portfolio return | Max drawdown |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| **Strategy D default** | 2,553 | 52.2 | 35.7% | **−0.127R** | −0.21 to −0.05 | 0.81 | −9.1%, halted | 10.3% |
| Strategy C rules, 15m only | 598 | 12.2 | 40.6% | +0.086R | −0.10 to +0.27 | 1.13 | −4.0% | 10.0% |
| Strategy C rules, all timeframes | 1,636 | 33.4 | 39.1% | +0.049R | −0.09 to +0.19 | 1.08 | −8.9%, halted | 10.2% |
| Strategy A rules, 15m only | 970 | 19.8 | 33.9% | −0.241R | −0.36 to −0.12 | 0.72 | −7.2%, halted | 10.2% |

"Halted" means the 10% drawdown breaker stopped new trades. D took only 73 trades before it tripped. C's range includes zero, so C isn't shown to be profitable either. It is less bad.

**D is less selective than C.** The aim was fewer signals, but D takes about four times as many as C on 15m. D accepts setups the existing engine rejected, and those added trades lower the average.

## Does stronger confirmation help?

Each row uses D's entries, stop and exits, and changes only the higher-timeframe rule.

| Rule | Trades | Avg net | 95% range | Earlier period | Middle | Latest |
|---|---:|---:|---|---:|---:|---:|
| No filter | 4,001 | −0.153R | −0.21 to −0.09 | −0.21R | −0.14R | −0.05R |
| D default | 2,553 | −0.127R | −0.21 to −0.05 | −0.20R | −0.01R | −0.09R |
| Strategy C's check | 1,698 | −0.075R | −0.17 to +0.02 | −0.17R | +0.11R | −0.06R |
| 4H, 1H may pull back | 1,743 | −0.071R | −0.17 to +0.03 | −0.20R | +0.18R | −0.05R |
| 4H and 1H, 1D context | 1,509 | −0.055R | −0.16 to +0.05 | −0.21R | +0.21R | −0.01R |
| Strict 1D, 4H and 1H | 793 | +0.035R | −0.12 to +0.18 | −0.08R | +0.35R | **−0.38R** |

Stricter rules raise the average, but not reliably. The strict rule is the only positive one, and it has the worst latest period and a range that includes zero. None passes the study's comparison with C. Its portfolio still halts at 10.1% drawdown after 155 trades.

## Other questions

| Question | Result |
|---|---|
| Best stop | 3.0 ATR −0.079R, 2.5 ATR −0.127R, 2.0 ATR −0.190R, structural −0.223R. Wider stops again lose less because costs per trade shrink. |
| Partial exits | No material difference: −0.127R against −0.131R for a single 2R exit. |
| Skip extended entries (over 2 ATR from EMA20) | −0.094R, the only filter marked `PROMISING` against D's default. Still negative, and not applied. |
| Require volume | −0.111R. Not supported as an improvement. |
| 5m refinement | −0.123R. No real effect, so it stays off. |
| LONG and SHORT | LONG −0.168R, SHORT −0.104R, with the same rules. Most of the year was falling: 1D was bullish on only 5% of candidates. |
| Entry types | Breakout −0.104R, pullback −0.142R, retest −0.165R, continuation −0.172R. All negative. |
| Regimes | Worst in exhaustion (−0.34R) and strong trends (−0.28R to −0.29R). Weak bear is about flat (+0.002R). Slices are small and were not used to choose anything. |
| Before costs | D averages +0.032R, so there is little edge for costs to take from. |
| Worse execution | Negative in every stress case, reaching −0.425R in the combined one. C on 15m goes negative with 4x slippage, delayed stop fills or the combined case. |

## Portfolio limits

| | Standard limits | Limits removed |
|---|---|---|
| Strategy D | 73 trades, −9.1%, drawdown 10.3%, halted | 1,353 trades, −64.3%, drawdown 65.8% |
| Strategy C rules, 15m | 223 trades, −4.0%, drawdown 10.0% | 389 trades, −1.8%, drawdown 21.1% |

The limits contain D's losses, but they don't make it profitable. The order of correlated trades follows decision time only, so no future information chooses between them.

## Limitations

- **Selection effects:** this history has been studied before, so even the better rows may be overstated.
- **Execution:** the replay and the forward run enter at the next candle's open. Neither uses real orders.
- **Forward costs:** the forward run doesn't charge funding, because candles don't carry it. The replay does.
- **Drawdown breaker:** D's paper account can halt at 10% drawdown, and there is no reset for it yet. On the replay's figures that could happen soon, which would stop new paper trades until a new version or a reset is added.
- **Timeframe correlation:** the four timeframes are not independent, so a high score is not four pieces of evidence.
- **Coverage:** live decisions need 15m in the scanned timeframes, and missed candles aren't backfilled.

## Recommendation

Keep D in shadow mode and don't promote it or send its alerts to Telegram. On this evidence Strategy C's rules remain the better baseline, although neither is validated. If you want a second version of D, it should keep the existing engine's rejections and require stricter alignment. That would be a new version, tested forward on new data.

**Isolation:** A, B and C's rules, records and versions are unchanged. D has its own table, `strategy_d_decisions`, and its own paper account. Its daily candles load separately, so they can't reach the existing engine.
