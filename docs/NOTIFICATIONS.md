# Notification Center (Phase 7)

The Notification Center is a persistent, real-time history of signals, paper-trade results, risk events and system incidents. Real-money trading stays disabled, and nothing in this phase places orders or changes signal logic, strategy versions or the decision engine's mode.

## Components

| Module | Role |
|---|---|
| `app/notifications/events.py` | Event taxonomy, validation, routing (`route`) |
| `app/notifications/messages.py` | Pure builders: domain record → event, Telegram text |
| `app/notifications/service.py` | Idempotent storage, publishing, outbox, incidents, grouping, retention |
| `app/notifications/health.py` | System-health monitor |
| `app/notifications/settings.py` | User preferences (`settings.notifications`) |
| `app/api/routes_notifications.py` | API |
| `frontend/src/components/notifications.tsx` | Header bell, popover, critical banner |
| `frontend/src/components/notification-center.tsx` | Feed, filters, detail view, incident history, preferences |
| `frontend/src/app/notifications/page.tsx` | `/notifications` |

## How an event flows

1. **Same transaction as the record.** A signal or paper-trade event is inserted in the same database transaction as the record it describes, so one can't exist without the other.
2. **Idempotent.** `event_id` is built from the domain record (for example `paper:{trade id}:exit`), never from the title. A unique constraint on `(recipient, event_id)` makes repeated processing a no-op, including after a restart.
3. **Outbox.** If Telegram applies, a `notification_deliveries` row commits with the event. A worker sends it, and retries change only that row, so a retry can't create another notification.
4. **Announce.** After the commit, the WebSocket announces the new row. Clients treat this as a hint and read from the API; the database is the source of truth.

A failure in any of this never interrupts signal generation. Malformed events are rejected and logged. Events that can't be stored because the database is unavailable are buffered in memory (up to 500) and retried.

## Events

| Category | Events | Telegram by default |
|---|---|---|
| Signal | `SIGNAL_QUALIFIED` | Yes |
| | `SIGNAL_DETECTED`, `SIGNAL_REJECTED`, `SIGNAL_EXPIRED`, `SIGNAL_INVALIDATED` | No |
| Trade | `PAPER_TP1_HIT`, `PAPER_TP2_HIT`, `PAPER_TP3_HIT`, `PAPER_STOP_LOSS_HIT`, `PAPER_TRADE_CLOSED` | Yes, for trades the paper portfolio took |
| | `PAPER_ENTRY_FILLED`, `PAPER_ENTRY_MISSED`, `PAPER_ORDER_UNFILLED`, `STRATEGY_EVALUATION` | No (entries can be switched on) |
| Risk | `RISK_DAILY_LOSS_LIMIT`, `RISK_DRAWDOWN_LIMIT` (both CRITICAL) | Yes |
| | `CIRCUIT_BREAKER_RESET` | Yes |
| | `RISK_MAX_OPEN_RISK`, `RISK_CORRELATED_REJECTED`, `RISK_STALE_DATA`, `RISK_MISSING_PRICE` | Only if CRITICAL |
| Market | `MARKET_SIGNAL_GROUP`, `MARKET_PRICE_ALERT` | No (price alerts keep their own existing Telegram message) |
| System | `INCIDENT_OPENED`, `INCIDENT_ESCALATED` | Only if CRITICAL |
| | `INCIDENT_RESOLVED` | If the incident was CRITICAL |
| Validation | `VALIDATION_DECISION`, `VALIDATION_PERIOD_STARTED`, `VALIDATION_READY_FOR_REVIEW` | No |

### Signals: baseline status and shadow verdict

Each signal event keeps two separate values:
- `baseline_status`: what the production rules decided.
- `decision`: the decision engine's verdict, with its own label.

In shadow mode the label reads "Shadow decision: Would reject — signal was not blocked." Only enforce mode, which is off and is never switched on automatically, reads "Enforced decision: …", and enforcement only blocks the Telegram alert.

`SIGNAL_DETECTED` means a candidate entered the watchlist. It has no entry, stop or target yet, and live data shows most candles have several, so it is recorded but muted by default. Turn it on with "Show watchlist candidates" in Preferences. Rejected candidates are shown by default and can be hidden with "Show rejected candidates".

### Paper trades

All trade notifications identify strategy A, B or C and are labelled PAPER TRADING.

**One notification per exit.** A stop or target exit already carries the full result, so no second `PAPER_TRADE_CLOSED` follows it. `PAPER_TRADE_CLOSED` is used only for time-limit exits.

**Take-profit levels.** The paper model exits a trade in full at one target. Partial exits would change the locked strategy versions, so they were not added:

| Strategy | `PAPER_TPn_HIT` meaning |
|---|---|
| B, C | TP1 is the 2R target and closes the trade. TP2 and TP3 never occur. |
| A | The production primary target closes the trade (TP2 by default). A level below it produces a notice: realized P&L $0, position still open at full size. Levels beyond the primary target never occur. |

The message builder reports realized P&L and remaining exposure explicitly, so it already handles real partial exits if the model ever adds them.

**Signal level vs portfolio level.** A filled trade that the risk limits kept out of the paper portfolio is a signal-level record, and its message says so. It stays in-app unless "Only trades the paper portfolio took" is switched off.

**Records before this phase.** Trades recorded earlier have no entry notification, because none was generated at the time and none was invented afterwards. Trades that were still open get their exit notification when they close.

### Risk

Daily-loss and drawdown events each announce a paper circuit breaker, with the strategy named in the title:
- The daily-loss breaker fires once per UTC day.
- The drawdown breaker fires once per reset, and a new trip after a reset is a new event.

These two events are also the "circuit breaker activated" notification, so one trip produces one alert. Every message states that only the paper portfolio is affected.

### Incidents

| Incident | Grace period | Severity |
|---|---|---|
| Bybit market data: every watched coin stale | 3 minutes | CRITICAL |
| Bybit WebSocket disconnected | 60 seconds | WARNING |
| One coin stale or missing a price | 2 minutes | WARNING |
| Scanner: no completed cycle or stopped | max(3 minutes, 5 cycles) | CRITICAL |
| Paper-trading worker error | None | CRITICAL |
| Telegram delivery failing | None | WARNING, in-app only |
| Database failure | Recorded after recovery, when it can be written | CRITICAL |

An incident produces one notification when it opens and one when it recovers. Retries only update its entry in the incident history. Open incidents are reloaded after a restart, so a later recovery still closes them.

## Grouping

Grouping is applied when reading, so individual records always stay available, and turning it off shows them immediately.

- **Same signal across A, B and C.** Shown as one row ("Strategy A: Accepted / Strategy B: Accepted / Strategy C: Rejected"). Trade results are never merged. Filtering by category, strategy or severity shows the individual records.
- **Correlated signals.** Three or more same-direction qualified signals within 60 minutes, on coins correlated at or above the risk engine's threshold (0.7), are summarised. Coins with low or unknown correlation are never grouped, and each signal keeps its Telegram alert so it isn't delayed.

## Preferences

Stored in `settings.notifications`; edit them under Preferences on `/notifications`.

| Setting | Default |
|---|---|
| `in_app_enabled`, `min_severity` | On, INFO |
| `categories` (in-app, Telegram, minimum severity) | All on, INFO |
| `muted_events` | `SIGNAL_DETECTED` |
| `telegram_events` (per-event overrides) | Empty, so the table above applies |
| `telegram_portfolio_only` | On |
| `strategies`, `symbols`, `timeframes` | Empty, meaning all |
| `sound` | Off |
| `grouping` | On, 3 signals, 60 minutes |
| `retention_days` | 180 |
| `timezone` | `Asia/Colombo` |

- `alerts.telegram_enabled` in Settings remains the master switch for Telegram, and `alerts.min_score` still applies to signal alerts.
- Muting hides an event from the feed and the unread count. It is still recorded, and "include muted" in the API returns it.
- Retention never deletes CRITICAL events or incident history.
- Changing these settings doesn't change the strategy versions, so forward evaluation periods continue.

## Database migration

`0006_notification_center` adds three tables and changes no existing ones:
- `notification_events`
- `notification_deliveries`
- `notification_incidents`

The existing `notifications` table stays as the Telegram delivery log. Downgrading deletes the notification history, but not signals or paper trades.

```
cd backend && python -m alembic upgrade head
```

## API

The prefix is `/api/notification-center`.

| Method | Path | Description |
|---|---|---|
| GET | `/events` | Newest first. Filters: `category`, `symbol`, `strategy`, `timeframe`, `severity`, `read`, `date_from`, `date_to`, `after_id`, `include_muted`, `limit`, `offset` |
| GET | `/events/{id}` | One event with links, deliveries, group members, the trade and the incident |
| POST | `/events/{id}/read` | Body `{read: true\|false}` |
| POST | `/read-all` | Optional body `{category}` |
| GET | `/unread-count` | `{total, critical}` |
| GET | `/facets` | Values for the filter controls |
| GET | `/incidents` | Incident history; optional `status` |
| GET, PUT | `/preferences` | Read or update preferences |

`GET /api/validation/trades/{id}` was added for chart links.

**WebSocket (`/ws`):**
- `notification`: a new event plus the unread count.
- `notification_read`: read-state changes, which keep browser tabs in step.
- `hello` now includes the unread count. Clients re-read the lists on every reconnect, and `after_id` returns anything newer than a known event.

## Security

- **Scoping.** The application is single-user with no login, so `current_recipient` resolves to the local user. All queries are scoped by it, and another recipient's event answers 404. Adding authentication means replacing that one dependency and scoping the WebSocket.
- **Secrets.** Payload keys that look like credentials are removed, and delivery errors are redacted. Telegram messages contain only the event's own text.

## Known limitations

- **At-least-once Telegram delivery.** If the process dies after Telegram accepted a message but before recording it, the retry can send it again. Events themselves never duplicate.
- **No API authentication,** as in the rest of the app. Keep the backend on localhost. The WebSocket reaches every connected client.
- **Exits detected from candles.** After downtime, an exit is reconstructed from candles and labelled in the trade's cost flags. Take-profit notices below the target are only produced from live prices, so they can be missed during downtime.
- **Single process.** Announcements use the in-process hub. Several backend instances would need the shared hub described in `ARCHITECTURE.md`.
- **Buffered events are lost if the process stops** while the database is also unavailable.
- **Daily-loss recovery isn't announced.** That breaker clears by itself at the next UTC day.
