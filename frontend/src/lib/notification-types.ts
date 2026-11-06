// Phase 7 Notification Center. The database is the source of truth; WebSocket messages only announce.
export type NotificationSeverity = "INFO" | "SUCCESS" | "WARNING" | "CRITICAL";
export type NotificationCategory = "signal" | "trade" | "risk" | "market" | "system" | "validation";

export interface UnreadCount { total: number; critical: number }

export interface NotificationLink { label: string; href: string }

export interface DecisionView {
  outcome: string; mode: string; enforced: boolean; label: string; reason: string | null;
  checks: { key?: string; name?: string; passed?: boolean; detail?: string }[];
}

export interface NotificationPayload {
  label?: string; direction?: string; strategy?: string | null; signal_time?: string; candle_open_time?: string;
  entry_reference?: number | null; stop_loss?: number | null; take_profit?: number | null;
  baseline_status?: string; reasoning?: string | null; score?: number | null; decision?: DecisionView | null;
  reference_entry?: number | null; fill_price?: number | null; quantity?: number | null; exit_price?: number | null;
  exit_reason?: string | null; net_pnl?: number | null; net_r?: number | null; entry_fee?: number | null;
  exit_fee?: number | null; slippage_cost?: number | null; spread_cost?: number | null; funding_cost?: number | null;
  duration?: string; portfolio_taken?: boolean; portfolio_reason?: string | null; cost_flags?: Record<string, string>;
  realized_pnl?: number; remaining_quantity?: number | null; closes_trade?: boolean; tp_index?: number | null;
  breaker?: string; circuit_breaker?: boolean; threshold?: Record<string, number | boolean | null>;
  affected_positions?: { symbol: string; timeframe: string; direction: string; risk_amount: number }[];
  risk_code?: string; symbols?: string[]; strategies?: Record<string, string>; incident_key?: string;
}

export interface NotificationItem {
  id: number; event_id: string; event_type: string; category: NotificationCategory; severity: NotificationSeverity;
  title: string; message: string; payload: NotificationPayload; symbol: string | null; timeframe: string | null;
  strategy_id: "A" | "B" | "C" | null; strategy_version: string | null; signal_id: string | null;
  paper_trade_id: string | null; incident_id: number | null; group_key: string | null; is_group_summary: boolean;
  muted: boolean; delivery_status: string; occurred_at: string; created_at: string; read_at: string | null;
  links: NotificationLink[];
}

export interface NotificationDeliveryRow {
  id: number; channel: string; status: string; attempts: number; last_error: string | null;
  next_attempt_at: string; created_at: string; sent_at: string | null;
}

export interface Incident {
  id: number; key: string; status: "OPEN" | "RESOLVED"; category: string; severity: NotificationSeverity; title: string;
  message: string; occurrences: number; started_at: string; last_seen_at: string; resolved_at: string | null;
}

export interface NotificationDetail extends NotificationItem {
  deliveries: NotificationDeliveryRow[]; members: NotificationItem[]; signal_exists: boolean; incident?: Incident | null;
  trade?: Record<string, unknown>;
}

export interface NotificationList { total: number; items: NotificationItem[]; unread: UnreadCount }

export interface CategoryPrefs { in_app: boolean; telegram: boolean; min_severity: NotificationSeverity }

export interface NotificationPrefs {
  in_app_enabled: boolean; min_severity: NotificationSeverity; categories: Record<NotificationCategory, CategoryPrefs>;
  muted_events: string[]; telegram_events: Record<string, boolean>; telegram_portfolio_only: boolean; strategy_d_telegram: boolean;
  strategies: string[]; symbols: string[]; timeframes: string[]; sound: boolean;
  grouping: { enabled: boolean; correlated_min_signals: number; correlated_window_minutes: number };
  retention_days: number; timezone: string;
}

export interface NotificationPrefsResponse { preferences: NotificationPrefs; telegram_configured: boolean; telegram_enabled: boolean }

export interface NotificationFilters {
  category?: string; symbol?: string; strategy?: string; timeframe?: string; severity?: string; read?: string;
  date_from?: string; date_to?: string; include_muted?: boolean; limit?: number; offset?: number; after_id?: number;
}
