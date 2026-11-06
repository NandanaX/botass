export type DBias = "BULLISH" | "BEARISH" | "SIDEWAYS" | "CONFLICTED" | "INSUFFICIENT_DATA";
export type DDecisionKind = "QUALIFIED" | "WATCHLIST" | "REJECTED";

export interface DTimeframeState {
  timeframe: string; bias: DBias; trend: number; structure: number; momentum: number; adx: number | null; rsi: number | null;
  last_break: string | null; pullback: boolean; why: string;
}

export interface DLatest {
  symbol: string; decision_time: string; biases: Record<string, DBias>; decision: DDecisionKind | "NO_TRADE"; direction: "LONG" | "SHORT" | null; reason: string;
}

export interface DOverview {
  strategy_id: "D"; name: string; version: string; mode: string; evidence: string; label: string; primary_timeframe: string; context_timeframes: string[];
  params: { qualify_score: number; watch_score: number; weights: Record<string, number>; stop_method: string; target_r: number[]; target_weights: number[]; min_room_r: number; cooldown_bars: number };
  telegram_enabled: boolean; decisions: Record<string, number>; reasons: Record<string, number>; taken: number; open: number; closed: number; closed_needed: number; enough_data: boolean;
  win_rate_pct: number | null; avg_net_r: number | null; total_net_r: number | null; profit_factor: number | null;
  by_direction: Record<"LONG" | "SHORT", { closed: number; avg_net_r: number | null }>;
  account: { equity: number; drawdown_pct: number; open_positions: number; open_risk_pct: number | null };
  status: { candles_checked: number; setups_seen: number; last_candle_at: string | null; last_error: string | null };
  latest: DLatest[]; notes: string[];
}

export interface DTarget { label: string; price: number; r: number; weight: number; move_pct: number }

export interface DDecision {
  id: string; symbol: string; timeframe: string; direction: "LONG" | "SHORT"; entry_type: string; strategy_version: string; signal_time: string; decision_at: string;
  decision: DDecisionKind; reason_code: string; reason: string; score: number; status: string; taken: boolean; exit_reason: string | null; gross_r: number | null; net_r: number | null;
  risk_amount: number | null; quantity: number | null;
  data: {
    entry_label: string; conflicts: string[]; states: Record<string, DTimeframeState>; room_r: number | null; extension_atr: number; rvol: number | null;
    confluence: { score: number; out_of: number; note: string; parts: { timeframe: string; weight: number; bias: string; alignment: string; points: number }[] };
    plan: { entry_reference: number; stop_loss: number; stop_move_pct: number; targets: DTarget[]; gross_r_all_targets: number; net_r_all_targets: number; net_r_at_stop: number; plan_note: string } | null;
  };
}

export interface DContext {
  symbol: string; available: boolean; reason?: string; decision_time?: string; price?: number; version?: string; label?: string; direction?: "LONG" | "SHORT";
  states?: Record<string, DTimeframeState>; levels?: Record<string, { support: number | null; resistance: number | null }>;
  alignment?: { score: number; out_of: number; note: string }; conflicts?: string[];
  d_decisions?: { direction: string; decision: DDecisionKind; reason: string; score: number; entry_type: string }[];
}

export interface DSummary { n?: number; avg_r?: number | null; win_rate_pct?: number | null; profit_factor?: number | null; total_r?: number | null }

export interface DStudyRow {
  label?: string; signals?: number; filled?: number; trades_per_week?: number; n: number; summary: DSummary; avg_net_r_ci95?: [number, number] | null;
  periods?: Record<string, DSummary>; later_periods?: DSummary; stop_out_pct?: number; avg_gross_r?: number;
  groups?: { by_direction?: Record<string, DSummary>; by_regime?: Record<string, DSummary> };
  portfolio?: { taken: number; avg_net_r: number | null; return_pct: number; max_drawdown_pct: number; rejected: Record<string, number> } | null;
}

export interface DStudy {
  meta: { version: string; start: string; end: string; days: number; evidence: string; label: string; configurations_tested: number };
  strategy_d: DStudyRow; baselines: Record<string, DStudyRow>; rules: Record<string, DStudyRow>; stops: Record<string, DStudyRow>;
}
