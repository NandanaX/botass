// Phase 5 Signal Intelligence Lab. All results are research output: outcomes are
// hypothetical (OHLC replays), counterfactuals are simulations, not trades.
import type { Signal, Zone } from "./types";

export interface RStats {
  n: number;
  insufficient_data?: boolean;
  win_rate_pct?: number;
  win_rate_ci95?: [number, number] | null;
  avg_r?: number;
  avg_r_ci95?: [number, number] | null;
  median_r?: number;
  total_r?: number;
  profit_factor?: number | null;
  max_drawdown_r?: number;
  worst_losing_streak?: number;
}

export interface DayStats { n: number; wins: number; win_rate_pct: number; total_r: number; avg_r: number }

export interface Cause { code: string; status: "confirmed" | "suspected" | "undetermined"; evidence: string }

export interface DecisionCheck {
  step: number;
  check: string;
  status: "pass" | "fail" | "warn" | "info";
  rule_type: "risk_control" | "validated_filter" | "diagnostic";
  detail: string;
}

export interface Decision {
  engine_version?: string;
  mode: "shadow" | "enforce";
  outcome: "QUALIFIED" | "WATCHLIST" | "REJECTED" | "NO_TRADE" | "ERROR";
  reason: string;
  checks: DecisionCheck[];
  study_keys?: string[];
  proposed_plan?: { stop_method: string; stop_loss: number; take_profit: number; note: string } | null;
  proposed_outcome?: { status: string; reason?: string; gross_r: number | null; net_r: number | null; exit_time: string | null; label: string } | null;
}

export interface Forensics {
  signal_id: string;
  symbol: string;
  timeframe: string;
  direction: "LONG" | "SHORT";
  strategy: string;
  strategy_version: string;
  signal_open_time: string;
  decision_time: string;
  entry_reference: number;
  stop_loss: number;
  take_profit: number;
  error?: string;
  reconstruction: { matches: boolean; differences: string[]; candles_used: number; full_history_matches: boolean };
  verification: {
    stored_status: string;
    stored_r: number | null;
    stored_exit_time: string | null;
    replay_gross: { status: string; reason: string; r: number | null; exit_time: string | null; bars_held: number };
    replay_net: { r: number | null; cost_r: number | null; funding_r: number | null; exit_fill: number | null; entry_fill: number | null };
    minute_path: { reason: string; exit_time: string; exit_price: number; bars_held: number } | null;
    minute_agrees?: boolean | null;
    ambiguous_bar_resolution: string | null;
    reproduced: boolean;
  };
  diagnostics: {
    regime: Record<string, unknown> & { regime: string; label: string; alignment: string };
    htf_regimes: Record<string, { regime: string; label: string; alignment: string }>;
    entry_quality: Record<string, unknown> & { class: string; reasons: string[] };
    stop: { distance_atr: number | null; noise_q80_atr: number | null; inside_noise: boolean; method: string | null };
    nearest_support: { timeframe: string; lower: number; upper: number; distance_atr: number | null } | null;
    nearest_resistance: { timeframe: string; lower: number; upper: number; distance_atr: number | null } | null;
    room_r: number | null;
    indicators: Record<string, number | null>;
    before: Record<string, number | null>;
    btc: (Record<string, unknown> & { regime: string; label: string; alignment: string }) | null;
    correlated_signals: { symbol: string; timeframe: string; minutes_apart: number; corr_1h: number | null; same_symbol: boolean }[];
  };
  outcome_evidence: Record<string, unknown> & {
    mfe_r: number | null; mae_r: number | null; target_reached_after_stop: boolean | null; exit_reason: string;
    failed_breakout: boolean | null; opposing_levels?: { level: string; price: number; distance_atr: number; held: boolean }[];
  };
  causes: Cause[];
  chart: { candles: { time: number; open: number; high: number; low: number; close: number; volume: number }[]; signal_time: number; entry_time: number; exit_time: number | null; zones: Zone[] };
}

export interface TodayRow { signal: Signal; forensics: Forensics; counterfactual_decision: Decision | null }

export interface TodayResponse {
  date: string;
  timezone: string;
  signals: TodayRow[];
  summary: {
    signals: number; resolved: number; pending: number;
    reported_gross: DayStats | null; verified_net: DayStats | null;
    counterfactual_taken: DayStats | null; counterfactual_taken_count: number;
    cause_counts: Record<string, { confirmed: number; suspected: number; undetermined: number }>;
    reproduced_all: boolean | null;
  } | null;
  study?: { available: boolean; keys: string[]; combined_verdict?: string; positive_expectancy?: boolean; generated_at?: string };
}

export interface PeriodReport { dev: RStats; val: RStats; test: RStats; oos: RStats; positive_expectancy: boolean;
  oos_by_symbol?: Record<string, RStats>; oos_by_timeframe?: Record<string, RStats>; monthly_r?: Record<string, number> }

export type Verdict = "VALIDATED" | "PROMISING" | "NOT_VALIDATED" | "INSUFFICIENT_DATA" | "NONE";

export interface HypothesisResult {
  key: string; title: string; hypothesis: string; change: string; params: Record<string, unknown>;
  dev_selection: { grid: Record<string, unknown>[]; chosen: unknown; note?: string | null } | null;
  results: PeriodReport; bootstrap: { p_improvement: number | null; delta_ci95: [number, number] | null };
  verdict: Verdict; notes: string[]; fitted: boolean; risk_test?: { percentile: number | null; note: string } | null;
}

export interface StudyResponse {
  meta: Record<string, unknown> & { split: Record<string, string>; candidates: number; production_candidates: number; filled_trades: number; generated_at: string; strategy_version: string; costs: Record<string, number> };
  baseline: { definition: string; results: PeriodReport };
  hypotheses: HypothesisResult[];
  walk_forward: Record<string, { fold: number; test_from: string; test_to: string; baseline: RStats; variant: RStats }[]>;
  ml: { verdict: Verdict; note: string; folds: (Record<string, unknown> & { fold: number; enabled: boolean; reason?: string; brier_model?: number; brier_climatology?: number; auc?: number | null; baseline?: RStats; filtered?: RStats })[] };
  validated_changes: string[];
  promising_changes: string[];
  combined: { keys: string[]; results: PeriodReport | null; bootstrap: { p_improvement: number | null } | null; verdict: Verdict; notes: string[]; trades: number };
  analyses: {
    score_calibration: { bands_all: Record<string, RStats>; bands_oos: Record<string, RStats>; spearman_score_vs_net_r: number | null; components: Record<string, { median: number; above_median: RStats; at_or_below_median: RStats }> };
    stops: Record<string, unknown> & { atr_multiplier_sensitivity?: Record<string, { dev: number; val: number; test: number; avg_cost_r: number; stop_hit_pct: number }> };
    entry_timing: Record<string, Record<string, RStats>>;
    regimes: Record<string, Record<string, RStats>>;
    strategies: Record<string, PeriodReport>;
    correlation: { by_cluster_prior: Record<string, RStats>; same_hour_same_direction_share_pct: number; portfolio_rejections: Record<string, number>; portfolio_accepted: number };
    failure_categories: { category: string; share_of_losers_pct: number; share_of_winners_pct: number; with_flag: RStats; without_flag: RStats }[];
  };
  status?: { state: string; step: string | null; error: string | null; started_at: string | null; finished_at: string | null };
}

export interface StopStats {
  trades: number; stop_hit_pct: number | null; avg_stop_atr: number | null; avg_mfe_r: number | null; avg_mae_r: number | null;
  avg_cost_r: number | null; noise_stopouts_pct: number | null; gross: RStats; net: RStats; net_oos: RStats;
}

export interface StudyTrade {
  id: number; symbol: string; timeframe: string; strategy: string; direction: "LONG" | "SHORT"; time: string; period: string;
  score: number; entry_class: string; regime: string; net_r: number; reason: string; proposed_takes: boolean | null;
}
