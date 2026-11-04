// Phase 6 Strategy Validation Monitor. FORWARD = live paper trading on signals detected
// after the evaluation period started; HISTORICAL = re-simulation of past signals.
import type { RStats } from "./research-types";

export interface ClusterBoot {
  n_trades: number; n_clusters: number; avg_net_r?: number; avg_net_r_ci95?: [number, number];
  prob_avg_positive?: number; win_rate_pct?: number; win_rate_ci95?: [number, number];
  profit_factor?: number | null; profit_factor_ci95?: [number, number];
}
export interface DrawdownDist {
  n_clusters: number; historical_max_dd_r?: number; dd_quantiles_r?: Record<string, number>;
  prob_dd_beyond?: Record<string, number>; assumption?: string;
}
export interface Consistency {
  months: Record<string, { total_r: number; n: number; avg_r?: number }>;
  weeks: Record<string, { total_r: number; n: number }>;
  positive_months?: number; months_total?: number; best_month_share_of_positive_r?: number | null;
  thirds?: { from: string; n: number; avg_net_r: number | null; total_r: number }[]; positive_thirds?: number;
}
export interface StatBlock { summary: RStats; bootstrap: ClusterBoot; drawdown: DrawdownDist | null; consistency: Consistency }

export interface GateCheck { key: string; criterion: string; passed: boolean; detail: string }

export interface PaperTrade {
  id: string; variant_key: string; symbol: string; timeframe: string; direction: "LONG" | "SHORT"; strategy: string | null;
  decision: "ACCEPTED" | "REJECTED" | "MISSED"; decision_reason: string | null; portfolio_taken: boolean;
  portfolio_reason: string | null; status: string; detected_at: string; candle_close_time: string;
  reference_entry: number | null; fill_price: number | null; stop_loss: number | null; take_profit: number | null;
  quantity: number | null; net_r: number | null; net_pnl: number | null; exit_reason: string | null; exit_time: string | null;
  cost_flags: Record<string, string>;
}

export interface VariantForward {
  variant: { id: string; key: string; name: string; version: string; period_start: string; config: Record<string, unknown> };
  elapsed_days: number;
  counts: { decisions: Record<string, number>; status: Record<string, number>; portfolio_taken: number; portfolio_rejections: Record<string, number> };
  strategy: StatBlock;
  portfolio: StatBlock & { equity_curve: { time: string; equity: number }[] };
  costs: { n: number; avg_fees_r?: number; avg_spread_r?: number | null; avg_slippage_r?: number | null; avg_funding_r?: number | null; estimated_share?: number; flags?: Record<string, number> };
  risk: { exposure: Record<string, unknown> & { equity: number; drawdown_pct: number; open_positions: number; open_risk_pct: number | null; net_directional_notional: number };
    breakers: Record<string, { tripped: boolean; [k: string]: unknown }> };
  promotion: { readiness: "READY_FOR_REVIEW" | "NOT_READY" | "INSUFFICIENT_FORWARD_DATA"; checks: GateCheck[]; manual_approval_required: boolean; note: string };
  recent: PaperTrade[];
}

export interface ValidationOverview {
  generated_at: string; label: string; variants: Record<"A" | "B" | "C", VariantForward>;
  tests: { finished_at: string; passed: number; failed: number; full_suite: boolean } | null;
  gate: Record<string, number>; risk_limits: Record<string, number | boolean>; paper_costs: Record<string, number>;
  periods: { id: string; key: string; name: string; period_start: string; period_end: string | null; active: boolean }[];
  decisions: { variant_id: string; decision: string; readiness: string; note: string; reviewer: string; created_at: string }[];
}

export interface HistVariant {
  summary: RStats; periods: Record<string, { n: number; avg_r: number | null }>; trades_per_day: number;
  stop_out_pct: number; avg_holding_bars: number; avg_fee_r: number; gross_avg_r: number;
  by_symbol: Record<string, { n: number; avg_r: number | null }>; by_timeframe: Record<string, { n: number; avg_r: number | null }>;
  by_regime: Record<string, { n: number; avg_r: number | null }>; bootstrap: ClusterBoot;
}
export interface StressRow { n?: number; avg_r: number | null; ci95?: [number, number] | null; prob_positive?: number; total_r?: number; p05?: number; p95?: number }
export interface PortfolioReplay { taken: number; rejected: Record<string, number>; avg_net_r: number | null; return_pct: number; max_drawdown_pct: number; equity_curve: number[] }

export interface HistoricalRobustness {
  meta: { label: string; generated_at: string; days: number; signals: number; configurations_evaluated: number; data_start: string; data_end: string; base_costs: Record<string, number | boolean | string> };
  variants: Record<string, HistVariant>;
  p_improvement_vs_A: Record<string, number>; holm_adjusted_p: Record<string, number>;
  stability: Record<"B" | "C", { avg_r_by_multiplier: Record<string, number>; cells_better_than_A: number; cells_total: number; plateau_2_to_3_all_above_A: boolean; plateau_2_to_3_all_positive: boolean; monotonic_increasing: boolean }>;
  drawdown: Record<string, DrawdownDist>;
  consistency: Record<string, Consistency>;
  stress: Record<string, Record<string, StressRow> & { _cost_curve: { cost_multiplier: number; avg_r: number | null; round_trip_cost_pct: number }[]; _break_even_cost_multiplier: number | string | null }>;
  portfolio: Record<string, PortfolioReplay>;
  portfolio_no_drawdown_breaker: Record<string, PortfolioReplay>;
  status?: { state: string; error: string | null };
}
