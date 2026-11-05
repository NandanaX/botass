# CryptoSignal AI: Application QA Audit

**Audit date:** 2026-11-04. **Git revision:** `a20b69c`, plus uncommitted work from Phases 7 and 8.

## 1. Executive summary

**Status: suitable for continuous market monitoring and paper trading on this PC, with the risks in section 11.** Passing this audit doesn't guarantee the application is bug-free.

- **Bugs:** 21 confirmed and fixed. None was rated CRITICAL. No data corruption, duplicate signals or double-counted trades were found.
- **Critical bugs remaining:** none known.
- **Tests:** all pass on the final code (section 5).
- **Most important open item:** the strategy configuration changed through the Settings page during the audit, which restarted the forward test. Someone must decide whether to keep it (section 11, item 1).
- **Unplanned real test:** this PC lost its internet connection from about 13:35 to 13:59 UTC, and the app recovered without a restart. That outage also exposed three of the bugs below.

No real-money trading exists. The decision engine stayed in shadow mode, no locked rule or threshold was edited, and no live paper record was changed.

## 2. Environment

| Item | Value |
|---|---|
| OS | Windows 11 Home, 12 logical CPUs, 16 GB RAM |
| Backend | Python 3.12.10, FastAPI 0.115.6, SQLAlchemy 2.0.36, uvicorn 0.34.0 |
| Frontend | Next.js 15.5.28, React 19.1.0, Node 22.22.3 |
| Browser testing | Playwright 1.64 with Chrome |
| Live database | SQLite, about 15 MB. **PostgreSQL was not tested** |
| Live config | 9 coins on 15m, 30m, 1h and 4h |

**Isolation.** Failure tests ran on a separate backend (port 8001) with a copy of the database, a local proxy that cut Bybit and Telegram on demand, and a fake Telegram server. The live database was backed up first and contains no test records (checked at the end).

## 3. Architecture inspected

| Area | What exists |
|---|---|
| Frontend | Next.js App Router, 16 routes, React Query, one shared WebSocket |
| Backend | FastAPI with 57 operations, one process |
| Background tasks | Bybit stream, ticker fallback, scanner, notification outbox, health monitor |
| Data model | 16 application tables, 6 migrations |
| Signal pipeline | Closed candle → `evaluate_at` → decision engine (shadow) → signal → paper variants A, B, C → notifications |
| Delivery | Events commit with their record; an outbox sends Telegram |
| Logging | JSON logs with token redaction, plus an in-memory list of recent problems |

### Audit checklist

| Subsystem | Method | Expected | Result |
|---|---|---|---|
| Routes | Real browser, routes read from the router folder | No errors at desktop or phone width | Pass after fixes |
| API | Every operation; valid and invalid input; concurrency | Correct status codes | Pass; 2 not executed |
| Market data | Stored candles vs fresh Bybit data | Identical, no gaps | Pass |
| Indicators | Independent library and hand-worked values | Match | Pass |
| Signals | Replay vs stored; both directions | Reproduced | Pass |
| Paper trading | Injected failures, kill, duplicate callbacks | No double counting | Pass after fixes |
| Notifications | Failure and recovery, real outage | No loss, no duplicates | Pass after fixes |
| Database | Integrity, orphans, duplicates, query plans | Clean | Pass |
| Performance | Measured live and on a production build | No bottleneck | Pass |
| Recovery | Injected outages, hard kill, real outage | Resumes alone | Pass after fixes |
| Security | Scanners, secret search, cross-site tests | No exposure | Pass after fixes; dependency items open |

## 4. Bugs found and fixed

| # | Severity | Bug | Evidence | Fix |
|---|---|---|---|---|
| 1 | HIGH | An error on one coin aborted the whole scanner cycle, so later coins were never scanned, repeating every cycle | Reproduced on the old code: XRP failed, SUI never ran | Each coin and timeframe is isolated; failures are counted and alerted |
| 2 | HIGH | The first save of *any* setting falsely changed the locked strategy versions (`10000` vs `10000.0` in the hash) | Same settings gave different versions | Version now depends on values only; active versions unchanged |
| 3 | HIGH | One Settings save could end a locked forward test with no warning | Occurred during this audit | The API returns 409 and the page asks for confirmation |
| 4 | HIGH | Telegram alerts were abandoned after 4 tries (about 21 minutes), so a longer outage lost them | Found during the real outage | Retries continue until the alert expires; sends at once on recovery |
| 5 | HIGH | **Run backtest silently did nothing** with default settings | Browser refused value 1 for min 0.01, step 0.1 | Field now accepts any value; every form default is tested |
| 6 | HIGH | After a restart, open paper trades weren't monitored until a candle of their timeframe closed | Code review | Loaded at startup. Measured: stop-loss closed 4 s after a hard kill |
| 7 | HIGH | A Bybit REST outage raised no alert, and health said "ready" | 4-minute outage, no incident | Independent REST probe with one grouped incident |
| 8 | HIGH | A write-locked database reported `database: ok` | 100-second lock unseen | Health check now performs a real write |
| 9 | MEDIUM | Any website could trigger actions on the local backend and read the WebSocket | Cross-site request sent a Telegram test | Requests and WebSocket connections from unknown origins are refused |
| 10 | MEDIUM | A failed trade close forgot the trade until restart | Code review | Trade stays monitored and retries |
| 11 | MEDIUM | Server errors lacked CORS headers, so the page wrongly said "cannot reach the backend" | SUI 4h error | Errors keep their headers and hide internal detail |
| 12 | MEDIUM | PC sleep raised CRITICAL Bybit and scanner alerts | 2 false CRITICAL alerts on Nov 4 | Reported as a pause, with a settling period |
| 13 | MEDIUM | During a network outage, "scanner stopped" was alerted on top of the real cause | Real outage | Suppressed while Bybit is known to be failing |
| 14 | MEDIUM | While REST was down, the scanner reported healthy with no new candles | Timed outage test | Reports DEGRADED when series can't refresh |
| 15 | MEDIUM | The exchange clock offset was measured only at startup | Code review | Refreshed about once a minute, with a drift alert over 5 s |
| 16 | MEDIUM | A stream that kept reconnecting raised no alert | Silent-stream test | Alert after 5 reconnects in 10 minutes |
| 17 | MEDIUM | 3 pages overflowed sideways on a phone (`/backtesting`, `/lab`, `/validation`) | Browser measurement: up to 501 px wider than the screen | Grid items can shrink; tables scroll inside themselves |
| 18 | MEDIUM | Closed forward-test periods were invisible in the monitor | Old trades disappeared after a version change | Period history table; `period=all` in the API |
| 19 | LOW | A forward-test restart was only INFO severity | Review | Now WARNING, with a clear message |
| 20 | LOW | Logs were wiped on every restart | `start.ps1` | Previous run kept as `.prev` |
| 21 | LOW | No record of test-tool dependencies | Review | Added to `requirements-dev.txt` |

**Mistakes in this phase's own new code, caught by the audit and fixed:**
- Telegram showed HEALTHY while it was failing.
- A slow network reply raised a false clock-drift alert on live, which closed by itself within two minutes.
- A slow health check was mistaken for the PC sleeping.
- A missing import crashed `/validation`.

**Earlier fixes covered by regression tests:** clock skew, SHORT target below zero, duplicate signals, A/B/C separation, shadow vs enforcement, paper persistence and notification deduplication.

## 5. Tests executed (final code)

| Suite | Result |
|---|---|
| Backend unit and integration | **314 passed, 0 failed**; 77% line coverage |
| Frontend unit | **39 passed, 0 failed**; about 53% line coverage |
| Browser, isolated production build | **58 passed, 0 failed** (includes changing data) |
| Browser, live dashboard | **52 passed, 6 skipped by design** (data-changing), 0 failed |
| API suite | **122 of 122 cases passed** |
| ruff (`app`, `tests`), eslint, tsc | Clean |
| Production build | Compiled, 17 static pages. Its "copy traced files" warning comes from the temporary build folder's link |

- **Timing of these runs.** The final backend fix came after the browser and API runs. It only changes how clock samples are taken, so it was covered by the backend suite, not by a browser rerun.
- **Coverage gaps.** The lowest are `bybit_ws.py` (29%), `scanner.py` (49%) and `market/service.py` (63%). These were exercised by the live and outage runs, which line coverage doesn't count. Frontend coverage excludes browser tests.

## 6. Routes visited in a real browser

Routes were discovered from the router folder, not from a hand-written list.

| Route | Desktop | Phone (390 px) |
|---|---|---|
| `/`, `/scanner`, `/signals`, `/history`, `/lab`, `/validation`, `/notifications`, `/system-health`, `/risk`, `/settings`, `/backtesting` | Pass | Pass |
| `/analysis`, `/analysis/[symbol]`, `/market/[symbol]`, `/setup`, `/setup/[symbol]` | Pass | Pass |
| `/analysis/SUIUSDT?tf=4h`, `/market/ETHUSDT?tf=1h`, `/setup/SOLUSDT`, `/notifications?tab=preferences`, `/notifications?tab=incidents` | Pass | Not separately |

**Failed: none. Untested routes: none.** Each load checked for runtime errors, console errors and failed requests.

**Workflows tested:** menu navigation, bell and popover, tabs and filters, notification detail and links, mark as read, risk calculator (valid and rejected), scanner and history filters, chart drawing, price alert create and cancel, settings confirmation and discard, Telegram test button, backtest from the form, 404 page, backend-unreachable message and empty lists.

**Not tested:**
- Column sorting (no specific test).
- Firefox and Safari.
- Accessibility.
- Every individual button on the Lab and Validation pages.

A person has not reviewed the visual layout; phone tests measure overflow, not appearance.

## 7. API endpoints tested

**55 of 57 operations** were executed against the isolated backend, with 65 invalid-input cases (bad types, missing fields, out-of-range values, SQL-like text, wrong methods).

**Not executed:** `POST /api/research/study` and `POST /api/validation/historical`, because they overwrite shared research results. The app has no rate limiting of its own; the only limiter is the outbound one for Bybit.

**Concurrency, no errors:**

| Concurrent clients | Requests | Median | 95th percentile |
|---|---|---|---|
| 10 | 40 | 167 ms | 637 ms |
| 50 | 200 | 870 ms | 3,371 ms |

## 8. Accuracy checks

**Market data.** 7,200 stored candles (9 coins, 4 timeframes) compared with fresh Bybit data: **0 mismatches**, no gaps, duplicates, ordering errors or misaligned timestamps. Clock difference from Bybit: 0.1–0.3 s. All live quotes had bid ≤ ask.

**Indicators.** Compared with the independent `ta` library on 3 coin and timeframe pairs (1,500 bars each): SMA, EMA, RSI, MACD, ATR, Bollinger Bands and ADX match. Differences are rounding, except EMA200, which still carries a negligible seed difference (at most 6e-5 of the median value).

**Look-ahead.** Indicators and decisions stay identical when later candles are added. Forming candles are excluded.

**Support, resistance, structure, patterns and Fibonacci** are verified by synthetic tests with known answers. No independent library was used for these, so they have a lower level of assurance.

**Signals.**
- A replay reproduced all 9 November 3 signals. Later live signals were replayed under the configuration of that time.
- The earlier mirror test on 28,305 real decisions matched 99.965% with zones reflected.
- Known asymmetry: zone clustering (see `docs/DIRECTION_AUDIT.md`). It wasn't changed, because that would alter the locked strategy.

**Database (live).** Integrity check OK; no foreign-key violations, orphans, duplicate signals, duplicate events, or inconsistent paper trades. Stored net R recomputes exactly on the sample checked. All migrations add tables only.

## 9. Performance measurements

| Measure | Result |
|---|---|
| Indicators for 1,000 candles | 15 ms median |
| One candle evaluation | 5 ms median |
| Full technical analysis | 15 ms median |
| Scan cycle, no new candle | About 15–20 ms |
| Scan cycle at the hourly close (36 series) | 8.6–12.7 s |
| Signal stored after candle close | 17.9 s median, as designed (scan interval and grace) |
| Telegram after signal stored | 0.97 s median, 1.62 s max (n=14) |
| Typical API call, live | 5–60 ms median |
| Slowest first call | `/api/research/today`, about 4–12 s (cached afterwards) |
| Health pings while that runs | 6 ms median, 72 ms worst: the event loop isn't blocked |
| Page load, production build | 105–150 ms to load, under about 1 s to settle |
| Backend memory | About 160 MB, flat over 36 minutes |
| Backend CPU | About 2.7% of one core |
| Browser idle on a live chart | 1 socket, no reconnects, heap 7.4–8.7 MB over 5 minutes |

**The longest signal delay was 906 s:** a 1h candle processed when the PC woke from sleep at 01:14 UTC. That's sleep, not a slow scanner.

**No optimisation was needed,** so none was done. **Measured limits:** the leak check is short (36 minutes per process, restarted several times), so it can't rule out a slow leak. Chart rendering and React re-renders weren't measured separately. An early run showing about 2 s for every call was discarded: it was an IPv6 fallback in the test client, not the app.

## 10. Recovery tests

| Scenario | Result |
|---|---|
| **Real internet loss, about 24 minutes (live)** | Detected, alerted, recovered alone; no candle gaps; waiting Telegram alerts then delivered |
| Bybit REST 503 across a candle close | Degraded, then DOWN and an incident; candle processed after recovery |
| WebSocket dropped | One incident; reconnects; REST fallback keeps prices fresh |
| WebSocket connected but silent | Detected and reconnected |
| Database write-locked across a candle close | DOWN within seconds; candle processed after the lock cleared; one incident |
| Telegram returning 500 | Paper trading continued, one incident, alert delivered on recovery, no duplicates |
| Hard kill and restart with an open trade | Trade closed 4 s after restart; no duplicate signals, events or trades |
| Duplicate callbacks and simultaneous closes | Same records; one result counted |
| Frontend refresh and reconnect | Recovers missed notifications from the database |
| PC sleep (unit test, and 2 real sleeps before the fix) | Reported as a pause; missed candles recorded as missed, not as live signals |

**Not tested:**
- A real change of the system clock (simulated by a fake server time).
- Graceful shutdown on Windows: `stop.ps1` force-kills, so only the hard kill was exercised.
- PostgreSQL connection recovery.
- A LONG signal or LONG paper trade live. None has occurred, so that path is verified only by tests.
- Take-profit and entry notifications live. A real stop-loss notification has been delivered.
- Runs longer than a few hours.

## 11. Remaining problems and operational risks

| # | Severity | Item | Action |
|---|---|---|---|
| 1 | **HIGH (decision)** | Saves at 18:09 Sri Lanka time added 15m and 30m to the higher-timeframe and zone lists. The forward test restarted (0 trades so far), and Strategy C's rule is now stricter than the version researched, so **the historical results no longer describe the live configuration** | Decide: keep, or restore the defaults. A restore starts another new period; the earlier 15 records stay in their own period either way |
| 2 | **HIGH** | The PC sleeps after 5 minutes idle when plugged in. Signals during sleep are never sent, which also counts against the promotion gate | Set sleep to Never, or use an always-on machine |
| 3 | MEDIUM | No automatic database backup; one exists only from this audit | Add a scheduled backup |
| 4 | MEDIUM | A crash while logged in isn't restarted, and autostart needs a login | Add a watchdog if needed |
| 5 | MEDIUM | `docker-compose.yml` publishes port 8000 and has a default database password. Not used in this setup | Change both before any shared deployment |
| 6 | MEDIUM | No API login, safe only on localhost | Add authentication before remote access |
| 7 | LOW | Telegram can repeat a message if the app dies just after sending | Known, accepted |
| 8 | LOW | Windows Python logs a harmless connection-reset error when a connection is cut | None needed |
| 9 | LOW | A full outage still slows scan cycles while requests time out | Acceptable; REST incident is shown |

## 12. Security findings

| Check | Result |
|---|---|
| Telegram token and chat ID in logs, settings, repository history | **Not found** (counts only; values not printed) |
| `.env`, databases, logs in Git | Ignored; none staged |
| Cross-site actions and WebSocket | **Fixed** |
| CORS | Limited to configured origins |
| Input validation | 65 invalid-input cases handled correctly; SQL-like text harmless |
| `pip-audit` (Python) | No known vulnerabilities |
| `npm audit` | **Open:** `postcss` inside Next.js (rated high) and `minimatch` in lint tools (moderate). The fix needs a breaking Next.js upgrade, and the `postcss` issues need attacker-supplied CSS, which this app doesn't process |
| `bandit` | 18 flagged: local research cache pickles (trusted local files) and one non-security SHA1 ID. No fix needed |
| Telegram bot token | The earlier recommendation to revoke and replace it still stands |

## 13. System Health and monitoring

**New page: `/system-health`,** backed by `GET /api/system/health`. It covers frontend, backend, database, Bybit REST, Bybit WebSocket, market data, scanner, signal engine, paper worker, Telegram, notifications and the monitor itself.

Status comes from heartbeats and data freshness: it was DOWN for a scanner with an old heartbeat while the process ran, and DEGRADED for a connected but silent stream. UNKNOWN means not verifiable.

**Automated alerts (one incident per problem, with recovery):** stale or missing prices, stopped or failing scanner, Bybit REST failure, unstable or silent stream, database not accepting writes, Telegram failure and backlog, stalled notification worker, crashed background task, clock drift and pauses. Monitoring runs as its own task, so its failure can't stop the scanner.

## 14. Recommended improvements

1. Resolve items 1 and 2 in section 11.
2. Add scheduled database backups.
3. Commit the uncommitted Phase 7 and 8 work.
4. Test PostgreSQL before relying on it.
5. Plan the Next.js upgrade to clear the `postcss` advisory.
6. Add an automatic restart if the backend crashes.
7. Run a multi-day soak test to rule out slow leaks.
8. Review the phone layouts by eye.
