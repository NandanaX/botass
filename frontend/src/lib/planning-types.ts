/** Experimental trade plan (Phase 9). Research only; never used by live signals or strategies A, B and C. */

export type GateStatus = "QUALIFIED" | "WAIT_FOR_RETEST" | "OVEREXTENDED" | "INSUFFICIENT_REWARD" | "CONFLICTING_TIMEFRAMES" | "REJECTED";

export interface StopVariant {
  method: string;
  label: string;
  valid: boolean;
  price: number | null;
  distance: number | null;
  distance_pct: number | null;
  distance_atr: number | null;
  level: number | null;
  justification: string;
  gate_status?: GateStatus;
  room_r?: number | null;
  net_r_at_primary?: number | null;
}

export interface PlanTarget {
  label: string;
  r_multiple: number;
  price: number | null;
  valid: boolean;
  feasibility: "clear" | "near_opposing" | "blocked" | "no_opposing_zone" | "invalid";
  note: string | null;
  distance?: number;
  distance_pct?: number;
  distance_atr?: number;
  gross_r?: number;
  net_r?: number;
}

export interface StructuralLevel {
  kind: string;
  label: string;
  price: number;
  distance: number;
  distance_pct: number;
  distance_atr: number;
  gross_r: number;
  net_r: number;
}

export interface GateCheck {
  key: string;
  label: string;
  passed: boolean;
  detail: string;
}

export interface PlanEvidence {
  status: "Unvalidated";
  note: string;
  gate_support: string | null;
  historical: {
    label: string;
    trades: number | null;
    avg_net_r: number | null;
    avg_net_r_ci95: [number, number] | null;
    win_rate_pct: number | null;
    max_drawdown_r: number | null;
    stop_out_pct: number | null;
  } | null;
}

export interface PlannedSetup {
  direction: "LONG" | "SHORT";
  strategy: string;
  production_status: "QUALIFIED" | "REJECTED";
  research_version: string;
  stop_method: string;
  entry_reference: number;
  entry_zone: [number, number] | null;
  atr: number;
  stop: StopVariant;
  stop_variants: StopVariant[];
  targets: PlanTarget[];
  structural_levels: StructuralLevel[];
  gate: {
    status: GateStatus;
    definition: string;
    reasons: { code: string; message: string }[];
    checks: GateCheck[];
    room_r: number | null;
    net_r_at_primary: number | null;
  };
  invalidation: string[];
  reasoning: {
    technical_evidence: string[];
    entry_quality: string;
    stop_justification: string;
    take_profit_feasibility: string;
    net_risk_reward: string;
    correlation_exposure: string;
    rejection_reasons: { code: string; message: string }[];
  };
  evidence: PlanEvidence;
}

export interface PlanZone {
  lower: number;
  upper: number;
  timeframe: string;
}

export interface TradePlanResponse {
  symbol: string;
  timeframe: string;
  candle_close_time: string;
  ready: boolean;
  not_ready_reason: string | null;
  setups: PlannedSetup[];
  price_decimals: number | null;
  gate_definitions: Record<GateStatus, string>;
  stop_methods: Record<string, string>;
  label: string;
  support: PlanZone | null;
  resistance: PlanZone | null;
  ticker: { available?: boolean; last_price?: number | null };
  defaults: {
    equity: number;
    risk_pct: number;
    leverage: number;
    max_leverage: number;
    entry_fee_pct: number;
    exit_fee_pct: number;
    entry_slippage_pct: number;
    stop_slippage_pct: number;
    maintenance_margin_pct: number;
    max_open_risk_pct: number;
    open_risk_usdt: number;
  };
  open_positions: { symbol: string; direction: string; timeframe: string }[];
}

export interface PositionInput {
  symbol: string;
  direction: "LONG" | "SHORT";
  equity: number;
  risk_pct: number;
  entry: number;
  stop: number;
  leverage: number;
  entry_fee_pct: number;
  exit_fee_pct: number;
  entry_slippage_pct: number;
  stop_slippage_pct: number;
  maintenance_margin_pct: number;
  open_risk_usdt: number;
  max_open_risk_pct: number;
  targets: { label: string; price: number }[];
}

export interface PositionPlan {
  feasible: boolean;
  risk_budget: number;
  risk_pct: number;
  quantity: number;
  quantity_decimals: number | null;
  base_coin: string | null;
  min_order_quantity: number | null;
  notional: number;
  leverage: number;
  initial_margin: number;
  effective_exposure_x: number;
  price_risk: number;
  planned_loss: number;
  planned_loss_pct_of_equity: number;
  estimated_entry_fee: number;
  estimated_stop_fee: number;
  liquidation_price: number | null;
  max_safe_leverage: number | null;
  targets: { label: string; price: number; gross_profit: number; fees: number; net_profit: number; net_rr: number | null }[];
  stop_gap_stress: { gap_pct: number; loss: number; pct_of_equity: number }[];
  issues: { code: string; message: string }[];
  warnings: string[];
  note: string;
}

export interface P9Summary {
  n: number;
  avg_r?: number;
  win_rate_pct?: number;
  profit_factor?: number | null;
  max_drawdown_r?: number;
  worst_losing_streak?: number;
  total_r?: number;
}

export interface P9Row {
  label?: string;
  n: number;
  summary: P9Summary;
  periods: Record<string, P9Summary>;
  later_periods?: P9Summary;
  avg_net_r_ci95?: [number, number];
  prob_avg_positive?: number;
  stop_out_pct?: number;
  avg_stop_atr?: number;
  avg_fee_r?: number;
  vs_original?: { research_verdict: string; p_improvement: number | null } | null;
  vs_full_2R?: { research_verdict: string; p_improvement: number | null } | null;
  vs_baseline?: { research_verdict: string; p_improvement: number | null } | null;
  vs_baseline_A?: { research_verdict: string; p_improvement: number | null } | null;
}

export interface P9Short extends P9Row {
  stop: string;
  exit: string;
  filter: string | null;
  trades_per_day: number;
  survives_all_stress: boolean;
  stress: Record<string, { n: number; avg_r: number | null; prob_positive?: number }>;
  portfolio: { fifo: { taken: number; avg_net_r: number | null; return_pct: number; max_drawdown_pct: number } };
}

export interface Phase9Study {
  meta: {
    label: string;
    evidence: string;
    generated_at: string;
    strategy_version: string;
    data_start: string;
    data_end: string;
    signals: number;
    configurations_tested: number;
    timeframes: string[];
    symbols: string[];
  };
  stops: Record<string, P9Row>;
  exits: Record<string, Record<string, P9Row>>;
  gate: Record<string, unknown>;
  retest: {
    next_open: P9Row;
    same_signals_next_open: P9Row;
    retest: P9Row;
    vs_same_signals: { research_verdict: string; p_improvement: number | null };
    fill_rate_pct: number;
    stress: Record<string, { n: number; avg_r: number | null }>;
  };
  shortlist: Record<string, P9Short>;
}
