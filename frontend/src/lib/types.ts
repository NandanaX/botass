export type Direction = "LONG" | "SHORT";
export const TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d", "1w"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

export interface Ticker {
  symbol: string;
  available: boolean;
  stale: boolean;
  updated_at: string | null;
  source: "websocket" | "rest" | null;
  last_price?: number | null;
  mark_price?: number | null;
  change_24h_pct?: number | null;
  high_24h?: number | null;
  low_24h?: number | null;
  volume_24h?: number | null;
  turnover_24h?: number | null;
  open_interest?: number | null;
  open_interest_value?: number | null;
  funding_rate?: number | null;
  next_funding_time?: string | null;
}

export interface ConnectionState {
  ws_connected: boolean;
  ws_last_message_at: string | null;
  ws_reconnects: number;
  rest_fallback_active: boolean;
  last_error: string | null;
}

export interface ConditionCheck {
  key: string;
  label: string;
  passed: boolean;
  detail: string;
  mandatory?: boolean;
}

export interface ScoreComponent {
  key: string;
  label: string;
  points: number;
  max_points: number;
  evaluated: boolean;
  reason: string;
}

export interface HTFState {
  timeframe: string;
  trend: string;
  candle_close_time: string;
  close: number;
}

export interface Indicators {
  [k: string]: number | null;
}

export type SetupStatus = "QUALIFIED" | "WATCHLIST" | "REJECTED" | "EXPIRED";
export type ZoneType = "SUPPORT" | "RESISTANCE";

export interface Reason {
  code: string;
  message: string;
}

export interface Zone {
  id: string;
  symbol: string | null;
  timeframe: string;
  lower: number;
  upper: number;
  mid: number;
  zone_type: ZoneType;
  origin: "swing_highs" | "swing_lows" | "mixed";
  status: "ACTIVE" | "BROKEN" | "INVALIDATED";
  tests: number;
  rejections: number;
  crossings: number;
  strength: number;
  price_inside: boolean;
  first_test_time: string;
  last_test_time: string;
  explanation: string;
}

export interface PlanTarget {
  label: string;
  r_multiple: number;
  price: number;
  allocation_pct: number;
  blocked: boolean;
  note: string | null;
}

export interface SizingTarget {
  label: string;
  price: number;
  r_multiple: number;
  allocation_pct: number;
  gross_profit: number;
  fees: number;
  net_profit: number;
  net_rr: number;
}

export interface InstrumentRules {
  symbol: string;
  tick_size: number;
  qty_step: number;
  min_qty: number;
  max_qty: number;
  max_market_qty: number | null;
  min_notional: number;
  max_leverage: number;
  maintenance_margin_rate: number | null;
  base_coin: string;
}

export interface Sizing {
  feasible: boolean;
  risk_budget: number;
  quantity_raw: number;
  quantity: number;
  notional: number;
  leverage: number;
  initial_margin: number;
  entry_fill_estimate: number;
  stop_fill_estimate: number;
  loss_per_unit: number;
  price_risk: number;
  estimated_loss: number;
  estimated_entry_fee: number;
  liquidation_price: number | null;
  max_safe_leverage: number | null;
  targets: SizingTarget[];
  weighted_net_profit: number | null;
  issues: Reason[];
  warnings: string[];
  rules: InstrumentRules | null;
}

export interface PlanStop {
  method: string;
  price: number;
  distance: number;
  distance_atr: number;
  structural_level: number | null;
  widened: boolean;
  reasoning: string;
}

export interface TradePlan {
  status: SetupStatus;
  strategy: "breakout" | "pullback";
  zone: Zone | null;
  entry_zone: [number, number] | null;
  entry_note: string;
  confirmation_candle: {
    open_time: string;
    close_time: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    rvol: number | null;
  } | null;
  stop: PlanStop | null;
  targets: PlanTarget[];
  primary_target_index: number;
  risk_reward: number | null;
  net_risk_reward: number | null;
  opposing_zone: (Zone & { boundary: number; room_r: number }) | null;
  room_r: number | null;
  sizing: Sizing | null;
  invalidation: string[];
  warnings: string[];
  partial_exits: boolean;
  reasons?: Reason[];
  zones?: Zone[];
  htf_zones?: Record<string, Zone[]>;
}

export interface Setup {
  direction: Direction;
  strategy: "breakout" | "pullback";
  status: SetupStatus;
  entry_reference: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  stop_distance: number | null;
  risk_reward: number | null;
  atr: number;
  score: number;
  score_components: ScoreComponent[];
  mtf_confirmed: boolean | null;
  mtf_details: HTFState[];
  conditions: ConditionCheck[];
  explanation: string;
  strategy_name: string;
  strategy_version: string;
  reasons: Reason[];
  plan: TradePlan;
}

export interface Swing {
  time: number;
  price: number;
  kind: "high" | "low";
  confirmed_time: number;
}

export interface Evaluation {
  timeframe: string;
  ready: boolean;
  open_time: string;
  close_time: string;
  close: number;
  trend: string;
  ema_alignment: string;
  macd_state: string;
  structure: string;
  indicators: Indicators;
  long_checks: ConditionCheck[];
  short_checks: ConditionCheck[];
  long_passed: number;
  short_passed: number;
  observations: string[];
  setup: Setup | null;
  setups: Setup[];
  zones: Zone[];
  htf_zones: Record<string, Zone[]>;
  rejection: string | null;
  not_ready_reason: string | null;
}

export interface Level {
  price: number;
  kind: "support" | "resistance";
  touches: number;
  lower?: number;
  upper?: number;
  status?: string;
}

export interface AnalysisSnapshot {
  symbol: string;
  timeframe: string;
  candle_open_time: string;
  candle_close_time: string;
  evaluation: Evaluation;
  signal_status: string;
  levels: Level[];
  data_quality: { candles: number; gaps: string[][]; current: boolean };
  updated_at: string;
}

export interface ScannerStatus {
  running: boolean;
  is_leader: boolean;
  cycles: number;
  last_cycle_at: string | null;
  last_cycle_ms: number | null;
  last_error: string | null;
  errors: number;
  evaluations: number;
  signals_created: number;
  market: ConnectionState;
}

export interface Overview {
  timeframe: string;
  markets: { ticker: Ticker; analysis: AnalysisSnapshot | null }[];
  connection: ConnectionState;
  scanner: ScannerStatus;
}

export interface ScannerRow {
  symbol: string;
  timeframe: string;
  ticker: Ticker;
  close: number | null;
  trend: string;
  rsi: number | null;
  macd_state: string;
  macd_hist: number | null;
  ema_alignment: string;
  structure: string;
  long_passed: number;
  short_passed: number;
  signal_status: string;
  setup: { direction: Direction; strategy: string; status: string; score: number | null; reason: string | null } | null;
  nearest_support: { lower: number; upper: number; tests: number; status: string } | null;
  nearest_resistance: { lower: number; upper: number; tests: number; status: string } | null;
  active_signals: { id: string; direction: Direction; score: number }[];
  candle_close_time: string;
  updated_at: string;
  data_quality: { current: boolean; gaps: string[][] } | null;
}

export interface SignalEvaluation {
  status: string;
  methodology: string;
  entry_price?: number;
  exit_price?: number;
  exit_time?: string;
  exit_reason?: string;
  r_multiple?: number;
  note?: string;
  targets?: { targets_hit: string[]; final: string; partial_exits: boolean; weighted_r?: number } | null;
}

export interface Signal {
  id: string;
  exchange: string;
  symbol: string;
  timeframe: string;
  direction: Direction;
  strategy_name: string;
  strategy_version: string;
  created_at: string;
  candle_open_time: string;
  candle_close_time: string;
  entry_reference: number;
  stop_loss: number;
  take_profit: number;
  risk_reward: number;
  atr: number;
  score: number;
  score_breakdown: ScoreComponent[];
  conditions: ConditionCheck[];
  indicators: Indicators;
  explanation: string;
  mtf_confirmed: boolean | null;
  mtf_details: HTFState[];
  strategy_type: string;
  setup_status: SetupStatus;
  plan: TradePlan | null;
  notification_status: string;
  evaluation_status: string;
  evaluation: SignalEvaluation | null;
  evaluated_at: string | null;
}

export interface SignalStats {
  total: number;
  active: number;
  long: number;
  short: number;
  by_evaluation_status: Record<string, number>;
  by_notification_status: Record<string, number>;
  hypothetical: {
    label: string;
    methodology: string;
    resolved: number;
    target_hits: number;
    stop_hits: number;
    hit_rate_pct: number | null;
    avg_r_multiple: number | null;
  };
}

export interface Notification {
  id: number;
  signal_id: string | null;
  kind: string;
  status: string;
  attempts: number;
  error: string | null;
  message: string;
  created_at: string;
  sent_at: string | null;
}

export interface CandleBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface CandlesResponse {
  symbol: string;
  timeframe: string;
  candles: CandleBar[];
  live_candle: (CandleBar & { open_time: string }) | null;
  overlays: Record<string, { time: number; value: number }[]>;
  levels: Level[];
  zones: Zone[];
  htf_zones: Record<string, Zone[]>;
  swings: Swing[];
  signals: Signal[];
  analysis: Evaluation;
  data_quality: { current: boolean; gaps: string[][]; refreshed_at: string | null };
  ticker: Ticker;
}

export interface StrategyParams {
  ema_fast: number;
  ema_mid: number;
  ema_slow: number;
  sma_long: number;
  rsi_period: number;
  rsi_long_min: number;
  rsi_long_max: number;
  rsi_short_min: number;
  rsi_short_max: number;
  macd_fast: number;
  macd_slow: number;
  macd_signal: number;
  breakout_lookback: number;
  atr_period: number;
  bb_period: number;
  bb_std: number;
  volume_ma_period: number;
  swing_strength: number;
  atr_pct_min: number;
  atr_pct_max: number;
  mtf: { enabled: boolean; timeframes: string[]; mode: "quality" | "filter" };
  zones: {
    lookback: number;
    cluster_atr_mult: number;
    min_width_atr: number;
    min_tests: number;
    break_buffer_atr: number;
    invalidate_after_crossings: number;
    max_zones_per_side: number;
    htf_timeframes: string[];
  };
  breakout: {
    enabled: boolean;
    level_source: "zone" | "range";
    min_rvol: number;
    min_body_ratio: number;
    max_extension_atr: number;
    watch_distance_atr: number;
  };
  pullback: {
    enabled: boolean;
    touch_tolerance_atr: number;
    max_pierce_atr: number;
    require_close_beyond_zone: boolean;
    require_directional_candle: boolean;
    min_close_position: number;
    rsi_min_long: number;
    rsi_max_short: number;
    require_macd_turn: boolean;
    watch_distance_atr: number;
  };
  stops: {
    method: "atr" | "structure" | "hybrid";
    atr_multiplier: number;
    hybrid_buffer_atr: number;
    min_stop_atr: number;
    max_stop_atr: number;
  };
  targets: {
    r_multiples: number[];
    primary_index: number;
    partial_exits: boolean;
    allocation_pct: number[];
    move_stop_to_breakeven: boolean;
  };
  qualification: { min_rr: number; min_net_rr: number; reject_outside_volatility: boolean };
  weights: {
    trend: number;
    momentum: number;
    breakout: number;
    volume: number;
    higher_timeframe: number;
    volatility: number;
    zone: number;
    room: number;
  };
}

export interface RuntimeSettings {
  watchlist: string[];
  timeframes: string[];
  strategy: StrategyParams;
  alerts: { telegram_enabled: boolean; min_score: number; max_signal_age_candles: number };
  risk: {
    account_balance: number;
    risk_per_trade_pct: number;
    fee_rate_pct: number;
    slippage_pct: number;
    leverage: number;
    max_leverage: number;
    maintenance_margin_pct: number;
  };
  evaluation: { max_bars: number };
  decision: DecisionSettings;
  validation: ValidationSettings;
  analysis: AnalysisSettings;
}

export interface AnalysisSettings {
  structure: { pivot_window: number; equal_tolerance_atr: number; break_buffer_atr: number; range_atr: number };
  candles: { doji_body_pct: number; small_body_pct: number; long_wick_ratio: number; marubozu_wick_pct: number; trend_lookback: number; level_distance_atr: number; outcome_bars: number };
  patterns: { pivot_window: number; lookback_pivots: number; max_age_bars: number; equal_tolerance_atr: number; flat_slope_atr: number; min_height_atr: number; breakout_buffer_atr: number; pole_min_atr: number };
  liquidity: { equal_level_tolerance_atr: number; fvg_min_atr: number; sweep_lookback: number; max_fvgs: number };
  weights: { structure: number; trend: number; momentum: number; levels: number; price_action: number; volume: number; volatility: number; futures: number };
  adx_period: number;
  adx_trend: number;
  supertrend_mult: number;
  stoch_period: number;
  bias_threshold: number;
  min_evidence_categories: number;
}

export interface SettingsResponse {
  settings: RuntimeSettings;
  defaults: RuntimeSettings;
  telegram: { configured: boolean; chat_id_hint: string | null };
  available_timeframes: string[];
  strategy_version: string;
}

export interface BacktestTrade {
  direction: Direction;
  strategy?: string;
  targets?: number[];
  stop_method?: string | null;
  fills?: { time: string; price: number; fraction: number; reason: string }[];
  notional?: number;
  signal_time: string;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  stop_loss: number;
  take_profit: number;
  quantity: number;
  score: number;
  mtf_confirmed: boolean | null;
  exit_reason: string;
  bars_held: number;
  gross_pnl: number;
  fees: number;
  funding: number;
  net_pnl: number;
  r_multiple: number;
  equity_after: number;
}

export interface BacktestMetrics {
  trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  avg_pnl: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  expectancy: number | null;
  avg_r_multiple: number | null;
  profit_factor: number | null;
  max_drawdown: number;
  max_drawdown_pct: number;
  initial_equity: number;
  final_equity: number;
  net_profit: number;
  net_return_pct: number;
  total_fees: number;
  total_funding: number;
  long_trades: number;
  short_trades: number;
  by_direction?: Record<"LONG" | "SHORT", { trades: number; win_rate_pct: number | null; net_pnl: number; avg_r_multiple: number | null; profit_factor: number | null }>;
  exit_reasons: Record<string, number>;
  avg_bars_held: number | null;
  skipped_signals: number;
  candles_evaluated: number;
  buy_and_hold_return_pct: number | null;
  funding_included: boolean;
  rejected_setup_reasons?: Record<string, number>;
  trades_by_strategy?: Record<string, number>;
}

export interface BacktestRun {
  id: string;
  name: string;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  created_at: string;
  finished_at: string | null;
  config: Record<string, unknown> & { symbol: string; timeframe: string; start: string; end: string };
  metrics: BacktestMetrics | null;
  assumptions: string[] | null;
  error: string | null;
  trades?: BacktestTrade[] | null;
  equity_curve?: { time: string; equity: number }[] | null;
}

export interface RiskResult {
  max_loss: number;
  quantity: number;
  notional: number;
  implied_leverage: number;
  stop_distance: number;
  stop_distance_pct: number;
  estimated_entry_fee: number;
  estimated_loss_at_stop_gross: number;
  estimated_loss_at_stop_net: number;
  risk_reward: number | null;
  potential_gross_profit: number | null;
  potential_net_profit: number | null;
  estimated_fees_at_target: number | null;
  quantity_raw: number;
  leverage: number;
  initial_margin: number;
  net_risk_reward: number | null;
  liquidation_price: number | null;
  max_safe_leverage: number | null;
  targets: { label: string; price: number; r_multiple: number; gross_profit: number; fees: number; net_profit: number; net_rr: number | null }[];
  feasible: boolean;
  issues: Reason[];
  warnings: string[];
  instrument: InstrumentRules | null;
  note: string;
}

export interface PriceAlert {
  id: number;
  symbol: string;
  condition: "above" | "below";
  price: number;
  note: string | null;
  status: "ACTIVE" | "TRIGGERED" | "CANCELLED";
  price_at_creation: number | null;
  created_at: string;
  triggered_at: string | null;
  trigger_price: number | null;
  notification_status: string | null;
}

export interface DiagnosticRule {
  key: string;
  group: string;
  label: string;
  state: "pass" | "fail" | "na";
  value: string;
  threshold: string;
  advisory: boolean;
}

export interface StrategyDiagnostics {
  strategy: "breakout" | "pullback";
  label: string;
  direction: Direction;
  enabled: boolean;
  status: string;
  rules: DiagnosticRule[];
}

export interface DecisionDiagnostics {
  ready: boolean;
  reason: string | null;
  trend_direction: Direction | null;
  strategies: StrategyDiagnostics[];
  triggers: { strategy: string | null; direction: Direction | null; text: string }[];
}

export interface SetupResponse {
  diagnostics: DecisionDiagnostics;
  symbol: string;
  timeframe: string;
  market: {
    ticker: Ticker;
    candle_open_time: string;
    candle_close_time: string;
    close: number;
    trend: string;
    structure: string;
    ema_alignment: string;
    macd_state: string;
    ready: boolean;
    not_ready_reason: string | null;
    indicators: Indicators;
    observations: string[];
    data_current: boolean;
    refreshed_at: string | null;
  };
  structure: {
    nearest_support: Zone | null;
    nearest_resistance: Zone | null;
    htf: Record<string, { support: Zone | null; resistance: Zone | null }>;
    zones: Zone[];
    htf_zones: Record<string, Zone[]>;
    swings: Swing[];
  };
  setups: Setup[];
  primary_setup: Setup | null;
  decision: "QUALIFIED" | "WATCHLIST" | "REJECTED" | "NO_SETUP";
  decision_note: string | null;
  sizing_inputs: {
    account_balance: number;
    risk_pct: number;
    fee_rate_pct: number;
    slippage_pct: number;
    leverage: number;
    maintenance_margin_pct: number;
  };
  instrument: InstrumentRules | null;
  active_signals: Signal[];
  disclaimer: string;
}

export interface Explanation {
  provider: string;
  summary: string;
  conditions: string[];
  invalidation: string[];
  risks: string[];
  disclaimer: string;
}

export interface PortfolioLimits {
  one_per_symbol: boolean;
  max_positions: number;
  max_total_risk_r: number;
  max_same_direction: number;
  max_correlated: number;
  corr_threshold: number;
  corr_window_hours: number;
  max_daily_loss_r: number;
  max_consecutive_losses: number;
  cooldown_hours: number;
}

export interface DecisionSettings {
  mode: "shadow" | "enforce";
  use_study_filters: boolean;
  portfolio: PortfolioLimits;
  regime: Record<string, number>;
  entry_quality: Record<string, number>;
}

export interface ValidationSettings {
  enabled: boolean;
  paper: Record<string, number>;
  risk: Record<string, number | boolean>;
  gate: Record<string, number>;
}
