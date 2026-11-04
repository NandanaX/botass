// Phase 4: technical analysis engine types (mirror backend/app/analysis).
import type { Reason, Sizing } from "./types";

export type BiasLabel = "BULLISH BIAS" | "BEARISH BIAS" | "NEUTRAL" | "CONFLICTED" | "INSUFFICIENT DATA" | "UNAVAILABLE";

export interface EvidenceItem {
  category: string;
  group: string;
  key: string;
  direction: number;
  direction_label: "BULLISH" | "BEARISH" | "NEUTRAL";
  strength: number;
  text: string;
  time: string | null;
  value: number | null;
}

export interface EvidenceCategory {
  category: string;
  label: string;
  weight: number;
  evaluable: boolean;
  score: number;
  groups: number;
  items: EvidenceItem[];
}

export interface ZoneRef {
  timeframe: string;
  lower: number;
  upper: number;
  tests: number;
  status: string;
}

export interface ChartPatternResult {
  id: string;
  name: string;
  category: string;
  expected: string;
  status: "DEVELOPING" | "CONFIRMED" | "INVALIDATED" | "EXPIRED";
  breakout_direction: string | null;
  detected_at: string;
  detected_index: number;
  resolved_at: string | null;
  pivots: { time: number; price: number; kind: string }[];
  lines: { t1: number; p1: number; t2: number; p2: number; label: string; dashed?: boolean }[];
  breakout_level: number | null;
  invalidation_level: number | null;
  target: number | null;
  height: number;
  explanation: string;
}

export interface CandlePatternResult {
  name: string;
  direction: "BULLISH" | "BEARISH" | "NEUTRAL";
  start_time: string;
  end_time: string;
  high: number;
  low: number;
  trend_context: string;
  in_context: boolean;
  at_level: boolean;
  volume_confirmed: boolean;
  level_distance_atr: number | null;
  explanation: string;
  unix?: number;
}

export interface StructureEventResult {
  kind: "BOS" | "CHOCH" | "WICK_BREAK";
  direction: "BULLISH" | "BEARISH";
  index: number;
  time: string;
  level: number;
  swing_time: string;
  label: string;
  unix?: number;
}

export interface ScenarioTarget {
  label: string;
  price: number;
  r_multiple: number;
  blocked?: boolean;
  note?: string | null;
}

export interface Scenario {
  name: string;
  direction: "LONG" | "SHORT" | "NONE";
  status: string;
  source?: string;
  trigger?: string | null;
  confirmation?: string | null;
  entry_zone?: number[] | null;
  entry?: number | null;
  stop_loss?: number | null;
  targets?: ScenarioTarget[] | null;
  risk_reward?: number | null;
  net_risk_reward?: number | null;
  sizing?: Sizing | null;
  hypothetical_entry?: number;
  hypothetical_stop?: number;
  hypothetical_targets?: ScenarioTarget[];
  hypothetical_rr?: number;
  note?: string;
  invalidation?: string[] | null;
  expiration?: string;
  reasons?: (Reason | string)[];
  opposing_evidence?: string[];
  watch_reason?: string | null;
  levels?: { support: ZoneRef | null; resistance: ZoneRef | null };
}

export interface Fib {
  direction: string;
  anchor_high: number;
  anchor_low: number;
  high_time: string;
  low_time: string;
  levels: { ratio: number; kind: string; price: number }[];
  explanation: string;
}

export interface Fvg {
  direction: string;
  lower: number;
  upper: number;
  time: number;
  created: string;
  explanation: string;
}

export interface AnalysisResult {
  symbol: string;
  timeframe: string;
  candle_open_time: string;
  candle_close_time: string;
  close: number;
  trend: string;
  trend_detail: { adx: number | null; trending: boolean; ma_alignment: string; supertrend: number; plus_di: number | null; minus_di: number | null };
  structure: string;
  structure_detail: {
    explanation: string;
    trend: string;
    swings: { index: number; price: number; kind: string; label: string; time: string }[];
    events: StructureEventResult[];
    equal_highs: boolean;
    equal_lows: boolean;
  };
  momentum: string;
  momentum_detail: { rsi: number | null; macd_hist: number | null; stoch_k: number | null; stochrsi_k: number | null; stretch: string | null };
  volatility: string;
  volatility_detail: { atr: number | null; atr_pct: number | null; bb_width_percentile: number | null; trend: string | null };
  volume_detail: { rvol: number | null; spike: boolean; obv_divergence: string | null; vwap: number | null; above_vwap: boolean | null };
  nearest_support: ZoneRef | null;
  nearest_resistance: ZoneRef | null;
  candlestick_patterns: CandlePatternResult[];
  chart_patterns: ChartPatternResult[];
  divergences: { kind: string; text: string; time: string }[];
  bias: BiasLabel;
  alignment_score: number;
  net_score: number;
  bullish_evidence: EvidenceItem[];
  bearish_evidence: EvidenceItem[];
  conflicts: string[];
  evidence: { categories: EvidenceCategory[]; alignment_note: string };
  levels: { kind: string; price: number; role: string; distance_atr: number | null; label: string }[];
  trendlines: { kind: string; label: string; value_now: number; broken: boolean }[];
  flips: { note: string; timeframe: string; lower: number; upper: number }[];
  fibonacci: Fib | null;
  equal_levels: { kind: string; price: number; touches: number; explanation: string }[];
  liquidity_events: { kind: string; direction: string; price: number; time: string; explanation: string }[];
  fair_value_gaps: Fvg[];
  futures: {
    available: boolean;
    items: { key: string; direction: string; text: string }[];
    sources: Record<string, { status: string; note?: string; error?: string }>;
    summary: string;
  } | null;
  scenarios: Scenario[];
  setups: { direction: string; strategy: string; status: string; reasons: Reason[] }[];
  data_source: { exchange: string; note: string; analysis_version: string; strategy_version: string };
  data_quality: { current: boolean; candles: number };
  warning?: string;
}

export interface MtfRow {
  timeframe: string;
  role: string;
  bias: BiasLabel;
  alignment_score?: number;
  trend: string;
  structure: string;
  momentum?: string;
  volatility?: string;
  close?: number;
  candle_open_time: string | null;
  nearest_support?: ZoneRef | null;
  nearest_resistance?: ZoneRef | null;
  last_event?: StructureEventResult | null;
  patterns?: string[];
  candles?: string[];
  error?: string;
}

export interface MtfResponse {
  symbol: string;
  timeframes: MtfRow[];
  summary: string;
  conflicts: string[];
}

export interface OverlaysResponse {
  symbol: string;
  timeframe: string;
  structure_events: StructureEventResult[];
  swings: { unix: number; price: number; kind: string; label: string; time: string }[];
  chart_patterns: ChartPatternResult[];
  candlestick_patterns: CandlePatternResult[];
  trendlines: { kind: string; t1: number; p1: number; t2: number; p2: number; value_now: number; broken: boolean; label: string }[];
  fibonacci: Fib | null;
  fair_value_gaps: Fvg[];
  equal_levels: { kind: string; price: number; touches: number; explanation: string }[];
  moving_averages: Record<string, { time: number; value: number }[]>;
}

export interface ReportResponse {
  symbol: string;
  timeframe: string;
  generated_at: string;
  generated_from_candle: string;
  data_source: Record<string, string>;
  sections: { title: string; lines: string[] }[];
  text: string;
  method: string;
}

export interface EventStat {
  n: number;
  insufficient_data: boolean;
  note?: string;
  hit_rate?: number;
  mean_fwd_r?: number;
  median_fwd_r?: number;
}

export interface ValidationResponse {
  symbol: string;
  event_study: {
    timeframe: string;
    candles: number;
    horizon_bars: number;
    split_time: string;
    min_events: number;
    baseline: { in_sample: EventStat; out_of_sample: EventStat; note: string };
    events: { group: string; name: string; in_sample: EventStat; out_of_sample: EventStat; by_regime: Record<string, EventStat> }[];
    method: string;
  };
  ablation?: {
    folds: number;
    note: string;
    variants: {
      variant: string;
      label: string;
      total_trades?: number;
      mean_fold_return_pct?: number;
      worst_fold_return_pct?: number;
      baseline_better_in_folds?: string;
      verdict?: string;
      error?: string;
    }[];
  };
}
