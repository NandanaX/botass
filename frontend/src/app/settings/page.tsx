"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, RotateCcw, Search, Send, X } from "lucide-react";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/app-shell";
import { DisplayTimeCard } from "@/components/display-time";
import { Empty, ErrorBox } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, NativeSelect, Switch } from "@/components/ui/form";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, ApiError } from "@/lib/api";
import { changedPaths, getPath, riskyConfigurations, sameValue, setPath, settingsProblems, TABS, type RiskyConfig, type TabId } from "@/lib/settings-form";
import { TIMEFRAMES, type RuntimeSettings, type StrategyParams } from "@/lib/types";

const NUM_FIELDS: { key: keyof StrategyParams; label: string; step?: string }[] = [
  { key: "ema_fast", label: "EMA fast" },
  { key: "ema_mid", label: "EMA mid" },
  { key: "ema_slow", label: "EMA slow" },
  { key: "sma_long", label: "SMA long" },
  { key: "rsi_period", label: "RSI period" },
  { key: "rsi_long_min", label: "RSI long min", step: "0.1" },
  { key: "rsi_long_max", label: "RSI long max", step: "0.1" },
  { key: "rsi_short_min", label: "RSI short min", step: "0.1" },
  { key: "rsi_short_max", label: "RSI short max", step: "0.1" },
  { key: "macd_fast", label: "MACD fast" },
  { key: "macd_slow", label: "MACD slow" },
  { key: "macd_signal", label: "MACD signal" },
  { key: "breakout_lookback", label: "Range lookback (range mode)" },
  { key: "atr_period", label: "ATR period" },
  { key: "bb_period", label: "Bollinger period" },
  { key: "bb_std", label: "Bollinger std dev", step: "0.1" },
  { key: "volume_ma_period", label: "Volume MA period" },
  { key: "atr_pct_min", label: "Volatility min ATR %", step: "0.01" },
  { key: "atr_pct_max", label: "Volatility max ATR %", step: "0.01" },
];

const PAPER = [["taker_fee_pct", "Taker fee %"], ["maker_fee_pct", "Maker fee %"], ["slippage_pct", "Entry slippage %"], ["stop_slippage_pct", "Stop slippage %"], ["max_quote_age_s", "Max quote age (s)"]] as const;
const PAPER_RISK = [["risk_per_trade_pct", "Risk per trade %"], ["max_open_risk_pct", "Max open risk %"], ["max_correlated_positions", "Max correlated positions"], ["daily_loss_limit_pct", "Daily loss breaker %"], ["max_drawdown_pct", "Max drawdown breaker %"]] as const;
const GATE = [["min_forward_days", "Min forward days"], ["min_closed_trades", "Min completed trades"], ["min_independent_clusters", "Min independent clusters"], ["min_prob_positive", "Min P(avg R > 0)"], ["min_avg_net_r", "Min avg net R"], ["max_best_month_share", "Max best-month share"], ["max_forward_drawdown_r", "Max forward DD (R)"], ["max_estimated_cost_share", "Max estimated-cost share"]] as const;

interface FormContext {
  draft: RuntimeSettings;
  defaults: RuntimeSettings | null;
  query: string;
  warnings: RiskyConfig[];
  set: (path: string, value: unknown) => void;
}

const Form = createContext<FormContext | null>(null);

function useForm(): FormContext {
  const ctx = useContext(Form);
  if (!ctx) throw new Error("settings field outside the form");
  return ctx;
}

const shown = (v: unknown) => (Array.isArray(v) ? v.join(", ") : typeof v === "boolean" ? (v ? "on" : "off") : String(v));

/** Label row, changed-from-default marker, reset action, and the search filter. */
function SettingField({ label, path, hint, children }: { label: string; path: string; hint?: string; children: React.ReactNode }) {
  const { draft, defaults, query, warnings, set } = useForm();
  if (query && !label.toLowerCase().includes(query)) return null;
  const value = getPath(draft, path);
  const def = defaults ? getPath(defaults, path) : undefined;
  const changed = def !== undefined && !sameValue(value, def);
  const fieldWarnings = warnings.filter((w) => w.path === path);
  return (
    <div className="group" data-field={path} data-changed={changed || undefined}>
      <Field
        label={label}
        hint={hint}
        labelExtra={changed && (
          <>
            <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-primary" role="img" aria-label={`Changed from the default of ${shown(def)}`} title={`Default: ${shown(def)}`} />
            <button
              type="button"
              className="ml-auto inline-flex items-center gap-0.5 rounded text-[11px] text-primary opacity-0 hover:underline focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring group-focus-within:opacity-100 group-hover:opacity-100"
              title={`Reset to default: ${shown(def)}`}
              aria-label={`Reset ${label} to default (${shown(def)})`}
              onClick={() => set(path, structuredClone(def))}
            >
              <RotateCcw className="h-3 w-3" aria-hidden /> Reset to default
            </button>
          </>
        )}
      >
        {children}
      </Field>
      {fieldWarnings.map((w) => (
        <p key={w.text} className="mt-1 flex gap-1 text-[11px] text-warn" data-warning={w.path}>
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden /> {w.text}
        </p>
      ))}
    </div>
  );
}

function Num({ label, path, step = "1", hint, min, max }: { label: string; path: string; step?: string; hint?: string; min?: number; max?: number }) {
  const { draft, set } = useForm();
  const value = getPath(draft, path) as number;
  return (
    <SettingField label={label} path={path} hint={hint}>
      <Input type="number" step={step} min={min} max={max} value={Number.isFinite(value) ? value : ""} onChange={(e) => set(path, e.target.value === "" ? 0 : Number(e.target.value))} />
    </SettingField>
  );
}

function Toggle({ label, path, hint }: { label: string; path: string; hint?: string }) {
  const { draft, set } = useForm();
  return (
    <SettingField label={label} path={path} hint={hint}>
      <div className="flex h-9 items-center"><Switch checked={getPath(draft, path) as boolean} onCheckedChange={(v) => set(path, v)} /></div>
    </SettingField>
  );
}

function Select({ label, path, asNumber, children }: { label: string; path: string; asNumber?: boolean; children: React.ReactNode }) {
  const { draft, set } = useForm();
  return (
    <SettingField label={label} path={path}>
      <NativeSelect value={getPath(draft, path) as string | number} onChange={(e) => set(path, asNumber ? Number(e.target.value) : e.target.value)}>{children}</NativeSelect>
    </SettingField>
  );
}

function TimeframeChecks({ label, path }: { label: string; path: string }) {
  const { draft, set } = useForm();
  const list = getPath(draft, path) as string[];
  return (
    <SettingField label={label} path={path}>
      <div className="flex flex-wrap gap-3 text-sm" role="group" aria-label={label}>
        {TIMEFRAMES.map((tf) => (
          <label key={tf} className="flex items-center gap-1.5">
            <input type="checkbox" className="accent-primary" checked={list.includes(tf)} onChange={() => set(path, list.includes(tf) ? list.filter((t) => t !== tf) : [...list, tf])} />
            {tf}
          </label>
        ))}
      </div>
    </SettingField>
  );
}

/** Hidden by CSS when a search leaves it without any field. */
const WHEN_FIELDS = "[&:not(:has([data-field]))]:hidden";

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card className={WHEN_FIELDS}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Group({ title, cols = "sm:grid-cols-3", children, className = "" }: { title?: string; cols?: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`${WHEN_FIELDS} ${className}`}>
      {title && <div className="mb-2 text-xs font-semibold">{title}</div>}
      <div className={`grid grid-cols-2 gap-3 ${cols}`}>{children}</div>
    </div>
  );
}

function Watchlist() {
  const { draft, set } = useForm();
  const [next, setNext] = useState("");
  const add = () => {
    const sym = next.trim().toUpperCase();
    if (sym && !draft.watchlist.includes(sym)) set("watchlist", [...draft.watchlist, sym]);
    setNext("");
  };
  return (
    <SettingField label="Watchlist" path="watchlist">
      <div className="flex flex-wrap gap-1.5">
        {draft.watchlist.map((sym) => (
          <Badge key={sym} variant="info" className="py-1 text-xs">
            {sym}
            <button type="button" aria-label={`Remove ${sym}`} onClick={() => set("watchlist", draft.watchlist.filter((y) => y !== sym))}>
              <X className="h-3 w-3" />
            </button>
          </Badge>
        ))}
      </div>
      <div className="flex gap-2">
        <Input aria-label="Add a symbol to the watchlist" placeholder="e.g. XRPUSDT" value={next} onChange={(e) => setNext(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        <Button variant="secondary" type="button" onClick={add}>Add</Button>
      </div>
    </SettingField>
  );
}

function Panels({ tab, telegram, draft }: { tab: TabId | "all"; telegram: { configured: boolean; chat_id_hint: string | null } | undefined; draft: RuntimeSettings }) {
  const on = (t: TabId) => tab === "all" || tab === t;
  const test = useMutation({ mutationFn: api.telegramTest });
  const { query } = useForm();
  const st = draft.strategy;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {on("markets") && (
        <>
          {!query && <DisplayTimeCard />}
          <Section title="Markets" description="Bybit USDT perpetual symbols. New symbols are validated against Bybit when saved.">
            <div className="space-y-4">
              <Watchlist />
              <TimeframeChecks label="Scanned timeframes (signals are generated on these)" path="timeframes" />
            </div>
          </Section>
          <Section title="Telegram alerts" description="Only QUALIFIED setups are stored as signals and alerted. Bot token and chat ID stay on the server.">
            <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
              Status:
              {telegram?.configured ? (
                <Badge variant="bull">configured (chat {telegram.chat_id_hint})</Badge>
              ) : (
                <Badge variant="warn" className="max-w-full whitespace-normal wrap-anywhere">not configured, set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID</Badge>
              )}
            </div>
            <Group cols="sm:grid-cols-2">
              <Toggle label="Send alerts" path="alerts.telegram_enabled" />
              <Num label="Minimum signal score" hint="Signals below this are stored but not alerted." min={0} max={100} path="alerts.min_score" />
              <Num label="Max signal age (candles)" hint="Older signals (e.g. after downtime) are not alerted." min={1} max={10} path="alerts.max_signal_age_candles" />
            </Group>
            <div className="mt-3 flex items-center gap-3">
              <Button variant="secondary" onClick={() => test.mutate()} disabled={test.isPending || !telegram?.configured}>
                <Send /> {test.isPending ? "Sending…" : "Send test notification"}
              </Button>
              {test.isSuccess && <span className="text-sm text-bull">Delivered.</span>}
            </div>
            <div className="mt-2"><ErrorBox error={test.error} /></div>
          </Section>
        </>
      )}

      {on("strategy") && (
        <>
          <Section title="Support / resistance zones" description="Zones cluster confirmed swing pivots within an ATR tolerance. A pivot is only used after the confirmation window has closed.">
            <Group>
              <Num label="Pivot confirmation window" hint="Bars on each side of a swing" min={1} max={20} path="strategy.swing_strength" />
              <Num label="Clustering (× ATR)" hint="Detection sensitivity: max zone width" step="0.05" path="strategy.zones.cluster_atr_mult" />
              <Num label="Minimum tests" min={1} max={10} path="strategy.zones.min_tests" />
              <Num label="Lookback (bars)" path="strategy.zones.lookback" />
              <Num label="Min zone width (× ATR)" step="0.05" path="strategy.zones.min_width_atr" />
              <Num label="Break buffer (× ATR)" step="0.05" path="strategy.zones.break_buffer_atr" />
              <Num label="Invalidate after crossings" min={1} path="strategy.zones.invalidate_after_crossings" />
              <Num label="Zones per side" min={1} max={20} path="strategy.zones.max_zones_per_side" />
            </Group>
            <div className="mt-3"><TimeframeChecks label="Higher-timeframe zones (major / intermediate structure)" path="strategy.zones.htf_timeframes" /></div>
          </Section>
          <Section title="Entry strategies" description="Both strategies need an EMA-aligned trend and only use zones known before the trigger candle closed.">
            <Group title="A · Breakout">
              <Toggle label="Breakout enabled" path="strategy.breakout.enabled" />
              <Select label="Level" path="strategy.breakout.level_source">
                <option value="zone">S/R zone</option>
                <option value="range">N-bar range (Phase 1)</option>
              </Select>
              <Num label="Min relative volume" step="0.1" hint="0 disables" path="strategy.breakout.min_rvol" />
              <Num label="Min body ratio" step="0.05" path="strategy.breakout.min_body_ratio" />
              <Num label="Max extension (× ATR)" step="0.1" hint="0 disables" path="strategy.breakout.max_extension_atr" />
              <Num label="Watchlist distance (× ATR)" step="0.1" path="strategy.breakout.watch_distance_atr" />
            </Group>
            <Group title="B · Pullback / retest" className="mt-4">
              <Toggle label="Pullback enabled" path="strategy.pullback.enabled" />
              <Num label="Touch tolerance (× ATR)" step="0.05" path="strategy.pullback.touch_tolerance_atr" />
              <Num label="Max pierce (× ATR)" step="0.05" path="strategy.pullback.max_pierce_atr" />
              <Num label="Min close position" step="0.05" hint="0.6 = close in top 40% (LONG)" path="strategy.pullback.min_close_position" />
              <Num label="RSI min (LONG)" step="0.5" path="strategy.pullback.rsi_min_long" />
              <Num label="RSI max (SHORT)" step="0.5" path="strategy.pullback.rsi_max_short" />
              <Toggle label="Close beyond zone" path="strategy.pullback.require_close_beyond_zone" />
              <Toggle label="Directional candle" path="strategy.pullback.require_directional_candle" />
              <Toggle label="MACD turn" path="strategy.pullback.require_macd_turn" />
            </Group>
          </Section>
          <Section title="Multi-timeframe trend confirmation" description="Higher-timeframe EMA trend must agree. Only closed higher-timeframe candles are used.">
            <Group cols="sm:grid-cols-2">
              <Toggle label="Multi-timeframe enabled" path="strategy.mtf.enabled" />
              <Select label="Multi-timeframe mode" path="strategy.mtf.mode">
                <option value="quality">Flag quality (keep all signals)</option>
                <option value="filter">Filter (reject disagreeing setups)</option>
              </Select>
            </Group>
            <div className="mt-3"><TimeframeChecks label="Confirmation timeframes" path="strategy.mtf.timeframes" /></div>
          </Section>
          <Section title="Score weights" description="Points per component, rescaled to 0-100 over evaluated components. The score measures rule agreement, not a win probability.">
            <Group>
              {(Object.keys(st.weights) as (keyof StrategyParams["weights"])[]).map((k) => (
                <Num key={k} label={k.replace("_", " ")} min={0} path={`strategy.weights.${k}`} />
              ))}
            </Group>
          </Section>
        </>
      )}

      {on("risk") && (
        <>
          <Section title="Stop-loss, targets & qualification" description="Structural stops are widened to the minimum distance, never tightened. Wider than the maximum rejects the setup.">
            <Group>
              <Select label="Stop method" path="strategy.stops.method">
                <option value="hybrid">Hybrid (structure − ATR buffer)</option>
                <option value="structure">Market structure</option>
                <option value="atr">ATR</option>
              </Select>
              <Num label="ATR stop multiplier" step="0.1" path="strategy.stops.atr_multiplier" />
              <Num label="Hybrid buffer (× ATR)" step="0.05" path="strategy.stops.hybrid_buffer_atr" />
              <Num label="Min stop (× ATR)" step="0.1" path="strategy.stops.min_stop_atr" />
              <Num label="Max stop (× ATR)" step="0.1" path="strategy.stops.max_stop_atr" />
              <Select label="Primary target" path="strategy.targets.primary_index" asNumber>
                {st.targets.r_multiples.map((r, k) => <option key={k} value={k}>TP{k + 1} ({r}R)</option>)}
              </Select>
              {st.targets.r_multiples.map((_, k) => <Num key={`r${k}`} label={`TP${k + 1} R multiple`} step="0.1" path={`strategy.targets.r_multiples.${k}`} />)}
              {st.targets.allocation_pct.map((_, k) => <Num key={`a${k}`} label={`TP${k + 1} close %`} step="5" path={`strategy.targets.allocation_pct.${k}`} />)}
              <Toggle label="Partial exits" hint="Scale out at TP1-TP3" path="strategy.targets.partial_exits" />
              <Toggle label="Breakeven after TP1" path="strategy.targets.move_stop_to_breakeven" />
              <Num label="Min room to opposing zone (R)" step="0.1" path="strategy.qualification.min_rr" />
              <Num label="Min net R:R (primary)" step="0.1" hint="After fees & slippage" path="strategy.qualification.min_net_rr" />
              <Toggle label="Reject outside volatility band" path="strategy.qualification.reject_outside_volatility" />
            </Group>
          </Section>
          <Section title="Account & risk" description="Used to size every trade plan. Estimates only; nothing is executed.">
            <Group>
              <Num label="Account balance (USDT)" step="any" path="risk.account_balance" />
              <Num label="Risk per trade (%)" step="0.1" max={10} path="risk.risk_per_trade_pct" />
              <Num label="Preferred leverage" min={1} path="risk.leverage" />
              <Num label="Maximum leverage" min={1} path="risk.max_leverage" />
              <Num label="Fee per side (%)" step="0.001" path="risk.fee_rate_pct" />
              <Num label="Slippage (%)" step="0.01" path="risk.slippage_pct" />
              <Num label="Maint. margin fallback (%)" step="0.1" hint="Used if Bybit tiers are unavailable" path="risk.maintenance_margin_pct" />
              <Num label="Signal expiry (candles)" hint="Outcome evaluation stops after this many candles." min={1} path="evaluation.max_bars" />
            </Group>
          </Section>
        </>
      )}

      {on("indicators") && (
        <Section title="Indicator parameters">
          <Group>
            {NUM_FIELDS.map((fld) => <Num key={fld.key} label={fld.label} step={fld.step} path={`strategy.${fld.key}`} />)}
          </Group>
        </Section>
      )}

      {on("decision") && (
        <Section title="Decision engine (Signal Intelligence Lab)" description="Runs on every QUALIFIED signal before Telegram. Shadow: the verdict is stored and shown in the alert, nothing is blocked. Enforce: alerts are sent only when the verdict is QUALIFIED. Only changes VALIDATED by the latest study are applied as filters; portfolio limits are risk controls.">
          <Group>
            <Select label="Mode" path="decision.mode">
              <option value="shadow">Shadow (record only)</option>
              <option value="enforce">Enforce (block non-qualified alerts)</option>
            </Select>
            <Toggle label="Apply validated study filters" path="decision.use_study_filters" />
            <Toggle label="One position per coin" path="decision.portfolio.one_per_symbol" />
            <Num label="Max open positions" min={0} path="decision.portfolio.max_positions" />
            <Num label="Max same direction" min={0} path="decision.portfolio.max_same_direction" />
            <Num label="Max correlated positions" min={0} path="decision.portfolio.max_correlated" />
            <Num label="Correlation threshold" step="0.05" path="decision.portfolio.corr_threshold" />
            <Num label="Max daily loss (R)" step="0.5" path="decision.portfolio.max_daily_loss_r" />
            <Num label="Max consecutive losses" min={0} path="decision.portfolio.max_consecutive_losses" />
          </Group>
        </Section>
      )}

      {on("validation") && (
        <Section title="Forward validation (Strategy Validation Monitor)" description="Paper-trading costs, paper-account risk limits and promotion-gate thresholds. Changing paper costs or risk limits starts a NEW evaluation period for all three variants (results of a period always come from one fixed configuration). Gate thresholds only change the readiness assessment.">
          <Group title="Paper costs and risk limits" cols="sm:grid-cols-3">
            {PAPER.map(([k, l]) => <Num key={k} label={l} step="0.005" path={`validation.paper.${k}`} />)}
            {PAPER_RISK.map(([k, l]) => <Num key={k} label={`Paper ${l.charAt(0).toLowerCase()}${l.slice(1)}`} step="0.1" path={`validation.risk.${k}`} />)}
          </Group>
          <Group title="Promotion gate (READY FOR REVIEW requires all)" cols="sm:grid-cols-3" className="mt-4">
            {GATE.map(([k, l]) => <Num key={k} label={l} step="0.01" path={`validation.gate.${k}`} />)}
          </Group>
        </Section>
      )}

      {on("analysis") && (
        <Section title="Technical analysis engine" description="Chart Analysis page: evidence weights, structure and pattern tolerances. Correlated indicators are grouped automatically; these weights apply per category.">
          <Group title="Evidence category weights">
            {Object.keys(draft.analysis.weights).map((k) => <Num key={k} label={k.replace("_", " ")} min={0} path={`analysis.weights.${k}`} />)}
          </Group>
          <Group title="Structure, patterns and bias" className="mt-4">
            <Num label="Structure pivot window" min={1} max={20} path="analysis.structure.pivot_window" />
            <Num label="BOS/CHoCH close buffer (× ATR)" step="0.01" path="analysis.structure.break_buffer_atr" />
            <Num label="Equal high/low tolerance (× ATR)" step="0.05" path="analysis.structure.equal_tolerance_atr" />
            <Num label="Pattern pivot window" min={2} max={30} path="analysis.patterns.pivot_window" />
            <Num label="Pattern tolerance (× ATR)" step="0.05" path="analysis.patterns.equal_tolerance_atr" />
            <Num label="Min pattern height (× ATR)" step="0.1" path="analysis.patterns.min_height_atr" />
            <Num label="ADX trending above" path="analysis.adx_trend" />
            <Num label="Bias threshold (net score)" step="0.05" path="analysis.bias_threshold" />
            <Num label="FVG minimum (× ATR)" step="0.05" path="analysis.liquidity.fvg_min_atr" />
          </Group>
        </Section>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [s, setS] = useState<RuntimeSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const [tab, setTab] = useState<TabId>("markets");
  const [search, setSearch] = useState("");
  const [matches, setMatches] = useState<number | null>(null);
  const panels = useRef<HTMLDivElement>(null);
  const query = search.trim().toLowerCase();

  useEffect(() => {
    if (q.data && !s) setS(structuredClone(q.data.settings));
  }, [q.data, s]);

  // Open the tab named in the address, so links and reloads land in the same place.
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("tab");
    if (TABS.some((t) => t.id === wanted)) setTab(wanted as TabId);
  }, []);

  const save = useMutation({
    mutationFn: ({ v, confirm }: { v: RuntimeSettings; confirm?: boolean }) => api.saveSettings(v, confirm),
    onSuccess: (res) => {
      setS(structuredClone(res.settings));
      qc.setQueryData(["settings"], res);
      qc.invalidateQueries({ queryKey: ["scanner"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      qc.invalidateQueries({ queryKey: ["setup"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    },
  });

  // How many fields a search left; the field components themselves decide whether they match.
  useEffect(() => {
    setMatches(query ? (panels.current?.querySelectorAll("[data-field]").length ?? 0) : null);
  }, [query, s, tab]);

  if (!s) return q.error ? <ErrorBox error={q.error} /> : <Empty>Loading settings…</Empty>;

  const set = (path: string, value: unknown) => setS((prev) => setPath(prev!, path, value));
  const pickTab = (next: TabId) => {
    setTab(next);
    window.history.replaceState(null, "", `/settings?tab=${next}`);
  };
  const issues = settingsProblems(s);
  const warnings = riskyConfigurations(s);
  const original = q.data!.settings;
  const unsaved = changedPaths(original, s).length;
  const discard = () => {
    save.reset();
    setS(structuredClone(original));
  };
  const restartWarning = save.error instanceof ApiError && save.error.status === 409 ? save.error.message : null;

  return (
    <div className="pb-24">
      <PageHeader
        title="Settings"
        description={`Strategy version ${q.data?.strategy_version ?? ""}. Changing strategy parameters changes the version, so new signals get new identifiers.`}
        actions={
          <>
            {saved && <Badge variant="bull">Saved</Badge>}
            <div className="relative w-64">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden />
              <Input type="search" aria-label="Search settings by label" placeholder="Search settings…" className="pl-8" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
          </>
        }
      />
      {issues.length > 0 && (
        <div role="alert" className="mb-3 space-y-0.5 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          {issues.map((i) => <div key={i}>{i}</div>)}
        </div>
      )}
      {warnings.length > 0 && !query && (
        <details className="mb-3 rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-xs" data-testid="risky-summary">
          <summary className="cursor-pointer font-medium text-warn">{warnings.length} setting{warnings.length === 1 ? "" : "s"} to double-check</summary>
          <ul className="mt-1 space-y-0.5">
            {warnings.map((w) => (
              <li key={w.text}>
                <button type="button" className="text-left hover:underline" onClick={() => pickTab(w.tab)}>{w.text}</button>
                <span className="text-muted-foreground"> · {TABS.find((t) => t.id === w.tab)?.label}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Tabs value={tab} onValueChange={(v) => pickTab(v as TabId)}>
          <TabsList aria-label="Settings sections" className="h-auto flex-wrap">
            {TABS.map((t) => <TabsTrigger key={t.id} value={t.id} disabled={!!query}>{t.label}</TabsTrigger>)}
          </TabsList>
        </Tabs>
        {query && <span className="text-xs text-muted-foreground" role="status">{matches ?? 0} match{matches === 1 ? "" : "es"} across all tabs</span>}
      </div>
      <Form.Provider value={{ draft: s, defaults: q.data?.defaults ?? null, query, warnings, set }}>
        <div ref={panels}>
          <Panels tab={query ? "all" : tab} telegram={q.data?.telegram} draft={s} />
        </div>
      </Form.Provider>
      {query && matches === 0 && <Empty>No setting has a label containing &ldquo;{search.trim()}&rdquo;.</Empty>}

      {(unsaved > 0 || restartWarning || save.isError) && (
        <div className="fixed bottom-0 left-0 right-0 z-30 border-t bg-popover px-4 py-3 shadow-lg md:left-56 md:px-6" data-testid="save-bar">
          {restartWarning && (
            <div role="alert" className="mb-2 text-sm">
              <p className="font-semibold text-warn">Saving would restart the forward test</p>
              <p className="mt-0.5">{restartWarning} Results so far are kept in their own period, but the evidence needed for validation starts again. Nothing has been saved yet.</p>
            </div>
          )}
          {!restartWarning && <div className="mb-2 empty:hidden"><ErrorBox error={save.error} /></div>}
          <div className="flex flex-wrap items-center justify-end gap-2">
            <span className="mr-auto text-sm font-medium" role="status">
              {unsaved} unsaved change{unsaved === 1 ? "" : "s"}
              {issues.length > 0 && <span className="ml-2 font-normal text-warn">Fix the errors above to save.</span>}
            </span>
            {restartWarning ? (
              <>
                <Button variant="outline" size="sm" onClick={() => save.reset()}>Keep editing</Button>
                <Button variant="outline" size="sm" onClick={discard}>Discard my changes</Button>
                <Button size="sm" disabled={save.isPending} onClick={() => save.mutate({ v: s, confirm: true })}>Save and restart the forward test</Button>
              </>
            ) : (
              <>
                <Button variant="outline" size="sm" onClick={discard}>Discard</Button>
                <Button size="sm" onClick={() => save.mutate({ v: s })} disabled={save.isPending || issues.length > 0 || unsaved === 0}>{save.isPending ? "Saving…" : "Save"}</Button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
