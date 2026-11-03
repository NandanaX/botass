"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, X } from "lucide-react";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/app-shell";
import { Empty, ErrorBox } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, NativeSelect, Switch } from "@/components/ui/form";
import { api } from "@/lib/api";
import { TIMEFRAMES, type RuntimeSettings, type StrategyParams } from "@/lib/types";

type NumKeys<T> = { [K in keyof T]: T[K] extends number ? K : never }[keyof T];

const NUM_FIELDS: { key: NumKeys<StrategyParams>; label: string; step?: string }[] = [
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
  { key: "swing_strength", label: "Pivot confirmation window" },
  { key: "atr_pct_min", label: "Volatility min ATR %", step: "0.01" },
  { key: "atr_pct_max", label: "Volatility max ATR %", step: "0.01" },
];

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Num({ label, value, onChange, step = "1", hint, min, max }: {
  label: string; value: number; onChange: (v: number) => void; step?: string; hint?: string; min?: number; max?: number;
}) {
  return (
    <Field label={label} hint={hint}>
      <Input type="number" step={step} min={min} max={max} value={Number.isFinite(value) ? value : ""} onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))} />
    </Field>
  );
}

function Toggle({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <Field label={label} hint={hint}>
      <div className="flex h-9 items-center"><Switch checked={checked} onCheckedChange={onChange} /></div>
    </Field>
  );
}

/** Client-side checks mirroring the backend validation, so problems show before saving. */
function problems(s: RuntimeSettings): string[] {
  const out: string[] = [];
  const st = s.strategy;
  const alloc = st.targets.allocation_pct.reduce((a, b) => a + b, 0);
  if (Math.abs(alloc - 100) > 1e-6) out.push(`Target allocations sum to ${alloc}%, they must sum to 100%.`);
  const r = st.targets.r_multiples;
  if (r.some((x, k) => x <= 0 || (k > 0 && x <= r[k - 1]))) out.push("Take-profit R multiples must be positive and increasing.");
  if (s.risk.leverage > s.risk.max_leverage) out.push("Leverage is above the maximum leverage.");
  if (s.risk.risk_per_trade_pct > 10) out.push("Risk per trade is limited to 10%.");
  if (s.risk.risk_per_trade_pct > 5 && s.risk.leverage > 10) out.push("Unsafe combination: risk above 5% with leverage above 10x.");
  if (!st.breakout.enabled && !st.pullback.enabled) out.push("Enable at least one entry strategy.");
  if (st.stops.min_stop_atr >= st.stops.max_stop_atr) out.push("Minimum stop distance must be below the maximum.");
  return out;
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [s, setS] = useState<RuntimeSettings | null>(null);
  const [newSymbol, setNewSymbol] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (q.data && !s) setS(structuredClone(q.data.settings));
  }, [q.data, s]);

  const save = useMutation({
    mutationFn: (v: RuntimeSettings) => api.saveSettings(v),
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
  const test = useMutation({ mutationFn: api.telegramTest });

  if (!s) return q.error ? <ErrorBox error={q.error} /> : <Empty>Loading settings…</Empty>;
  const st = s.strategy;
  const upd = (fn: (x: RuntimeSettings) => void) =>
    setS((prev) => {
      const next = structuredClone(prev!);
      fn(next);
      return next;
    });
  const addSymbol = () => {
    const sym = newSymbol.trim().toUpperCase();
    if (sym && !s.watchlist.includes(sym)) upd((x) => void x.watchlist.push(sym));
    setNewSymbol("");
  };
  const toggleIn = (list: string[], tf: string) => (list.includes(tf) ? list.filter((t) => t !== tf) : [...list, tf]);
  const issues = problems(s);

  return (
    <>
      <PageHeader
        title="Settings"
        description={`Strategy version ${q.data?.strategy_version ?? ""}. Changing strategy parameters changes the version, so new signals get new identifiers.`}
        actions={
          <>
            {saved && <Badge variant="bull">Saved</Badge>}
            <Button variant="outline" onClick={() => setS(structuredClone(q.data!.settings))}>Discard changes</Button>
            <Button onClick={() => save.mutate(s)} disabled={save.isPending || issues.length > 0}>{save.isPending ? "Saving…" : "Save settings"}</Button>
          </>
        }
      />
      <ErrorBox error={save.error} />
      {issues.length > 0 && (
        <div className="mb-3 space-y-0.5 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          {issues.map((i) => <div key={i}>{i}</div>)}
        </div>
      )}
      <div className="mt-3 grid gap-4 lg:grid-cols-2">
        <Section title="Markets" description="Bybit USDT perpetual symbols. New symbols are validated against Bybit when saved.">
          <div className="mb-3 flex flex-wrap gap-1.5">
            {s.watchlist.map((sym) => (
              <Badge key={sym} variant="info" className="py-1 text-xs">
                {sym}
                <button aria-label={`Remove ${sym}`} onClick={() => upd((x) => void (x.watchlist = x.watchlist.filter((y) => y !== sym)))}>
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
          </div>
          <div className="flex gap-2">
            <Input placeholder="e.g. XRPUSDT" value={newSymbol} onChange={(e) => setNewSymbol(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addSymbol()} />
            <Button variant="secondary" onClick={addSymbol}>Add</Button>
          </div>
          <div className="mt-4">
            <div className="mb-2 text-xs font-medium text-muted-foreground">Scanned timeframes (signals are generated on these)</div>
            <div className="flex flex-wrap gap-3 text-sm">
              {TIMEFRAMES.map((tf) => (
                <label key={tf} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={s.timeframes.includes(tf)} onChange={() => upd((x) => void (x.timeframes = toggleIn(x.timeframes, tf)))} />
                  {tf}
                </label>
              ))}
            </div>
          </div>
        </Section>

        <Section title="Telegram alerts" description="Only QUALIFIED setups are stored as signals and alerted. Bot token and chat ID stay on the server.">
          <div className="mb-3 flex items-center gap-2 text-sm">
            Status:
            {q.data?.telegram.configured ? (
              <Badge variant="bull">configured (chat {q.data.telegram.chat_id_hint})</Badge>
            ) : (
              <Badge variant="warn">not configured, set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID</Badge>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Toggle label="Send alerts" checked={s.alerts.telegram_enabled} onChange={(v) => upd((x) => void (x.alerts.telegram_enabled = v))} />
            <Num label="Minimum signal score" hint="Signals below this are stored but not alerted." min={0} max={100} value={s.alerts.min_score} onChange={(v) => upd((x) => void (x.alerts.min_score = v))} />
            <Num label="Max signal age (candles)" hint="Older signals (e.g. after downtime) are not alerted." min={1} max={10} value={s.alerts.max_signal_age_candles} onChange={(v) => upd((x) => void (x.alerts.max_signal_age_candles = v))} />
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Button variant="secondary" onClick={() => test.mutate()} disabled={test.isPending || !q.data?.telegram.configured}>
              <Send /> {test.isPending ? "Sending…" : "Send test notification"}
            </Button>
            {test.isSuccess && <span className="text-sm text-bull">Delivered.</span>}
          </div>
          <div className="mt-2"><ErrorBox error={test.error} /></div>
        </Section>

        <Section title="Support / resistance zones" description="Zones cluster confirmed swing pivots within an ATR tolerance. A pivot is only used after the confirmation window has closed.">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Num label="Pivot confirmation window" hint="Bars on each side of a swing" min={1} max={20} value={st.swing_strength} onChange={(v) => upd((x) => void (x.strategy.swing_strength = v))} />
            <Num label="Clustering (× ATR)" hint="Detection sensitivity: max zone width" step="0.05" value={st.zones.cluster_atr_mult} onChange={(v) => upd((x) => void (x.strategy.zones.cluster_atr_mult = v))} />
            <Num label="Minimum tests" min={1} max={10} value={st.zones.min_tests} onChange={(v) => upd((x) => void (x.strategy.zones.min_tests = v))} />
            <Num label="Lookback (bars)" value={st.zones.lookback} onChange={(v) => upd((x) => void (x.strategy.zones.lookback = v))} />
            <Num label="Min zone width (× ATR)" step="0.05" value={st.zones.min_width_atr} onChange={(v) => upd((x) => void (x.strategy.zones.min_width_atr = v))} />
            <Num label="Break buffer (× ATR)" step="0.05" value={st.zones.break_buffer_atr} onChange={(v) => upd((x) => void (x.strategy.zones.break_buffer_atr = v))} />
            <Num label="Invalidate after crossings" min={1} value={st.zones.invalidate_after_crossings} onChange={(v) => upd((x) => void (x.strategy.zones.invalidate_after_crossings = v))} />
            <Num label="Zones per side" min={1} max={20} value={st.zones.max_zones_per_side} onChange={(v) => upd((x) => void (x.strategy.zones.max_zones_per_side = v))} />
          </div>
          <div className="mt-3 text-xs font-medium text-muted-foreground">Higher-timeframe zones (major / intermediate structure)</div>
          <div className="mt-1 flex flex-wrap gap-3 text-sm">
            {TIMEFRAMES.map((tf) => (
              <label key={tf} className="flex items-center gap-1.5">
                <input type="checkbox" checked={st.zones.htf_timeframes.includes(tf)} onChange={() => upd((x) => void (x.strategy.zones.htf_timeframes = toggleIn(x.strategy.zones.htf_timeframes, tf)))} />
                {tf}
              </label>
            ))}
          </div>
        </Section>

        <Section title="Entry strategies" description="Both strategies need an EMA-aligned trend and only use zones known before the trigger candle closed.">
          <div className="mb-2 text-xs font-semibold">A · Breakout</div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Toggle label="Enabled" checked={st.breakout.enabled} onChange={(v) => upd((x) => void (x.strategy.breakout.enabled = v))} />
            <Field label="Level">
              <NativeSelect value={st.breakout.level_source} onChange={(e) => upd((x) => void (x.strategy.breakout.level_source = e.target.value as "zone" | "range"))}>
                <option value="zone">S/R zone</option>
                <option value="range">N-bar range (Phase 1)</option>
              </NativeSelect>
            </Field>
            <Num label="Min relative volume" step="0.1" hint="0 disables" value={st.breakout.min_rvol} onChange={(v) => upd((x) => void (x.strategy.breakout.min_rvol = v))} />
            <Num label="Min body ratio" step="0.05" value={st.breakout.min_body_ratio} onChange={(v) => upd((x) => void (x.strategy.breakout.min_body_ratio = v))} />
            <Num label="Max extension (× ATR)" step="0.1" hint="0 disables" value={st.breakout.max_extension_atr} onChange={(v) => upd((x) => void (x.strategy.breakout.max_extension_atr = v))} />
            <Num label="Watchlist distance (× ATR)" step="0.1" value={st.breakout.watch_distance_atr} onChange={(v) => upd((x) => void (x.strategy.breakout.watch_distance_atr = v))} />
          </div>
          <div className="mb-2 mt-4 text-xs font-semibold">B · Pullback / retest</div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Toggle label="Enabled" checked={st.pullback.enabled} onChange={(v) => upd((x) => void (x.strategy.pullback.enabled = v))} />
            <Num label="Touch tolerance (× ATR)" step="0.05" value={st.pullback.touch_tolerance_atr} onChange={(v) => upd((x) => void (x.strategy.pullback.touch_tolerance_atr = v))} />
            <Num label="Max pierce (× ATR)" step="0.05" value={st.pullback.max_pierce_atr} onChange={(v) => upd((x) => void (x.strategy.pullback.max_pierce_atr = v))} />
            <Num label="Min close position" step="0.05" hint="0.6 = close in top 40% (LONG)" value={st.pullback.min_close_position} onChange={(v) => upd((x) => void (x.strategy.pullback.min_close_position = v))} />
            <Num label="RSI min (LONG)" step="0.5" value={st.pullback.rsi_min_long} onChange={(v) => upd((x) => void (x.strategy.pullback.rsi_min_long = v))} />
            <Num label="RSI max (SHORT)" step="0.5" value={st.pullback.rsi_max_short} onChange={(v) => upd((x) => void (x.strategy.pullback.rsi_max_short = v))} />
            <Toggle label="Close beyond zone" checked={st.pullback.require_close_beyond_zone} onChange={(v) => upd((x) => void (x.strategy.pullback.require_close_beyond_zone = v))} />
            <Toggle label="Directional candle" checked={st.pullback.require_directional_candle} onChange={(v) => upd((x) => void (x.strategy.pullback.require_directional_candle = v))} />
            <Toggle label="MACD turn" checked={st.pullback.require_macd_turn} onChange={(v) => upd((x) => void (x.strategy.pullback.require_macd_turn = v))} />
          </div>
        </Section>

        <Section title="Stop-loss, targets & qualification" description="Structural stops are widened to the minimum distance, never tightened. Wider than the maximum rejects the setup.">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Field label="Stop method">
              <NativeSelect value={st.stops.method} onChange={(e) => upd((x) => void (x.strategy.stops.method = e.target.value as "atr" | "structure" | "hybrid"))}>
                <option value="hybrid">Hybrid (structure − ATR buffer)</option>
                <option value="structure">Market structure</option>
                <option value="atr">ATR</option>
              </NativeSelect>
            </Field>
            <Num label="ATR stop multiplier" step="0.1" value={st.stops.atr_multiplier} onChange={(v) => upd((x) => void (x.strategy.stops.atr_multiplier = v))} />
            <Num label="Hybrid buffer (× ATR)" step="0.05" value={st.stops.hybrid_buffer_atr} onChange={(v) => upd((x) => void (x.strategy.stops.hybrid_buffer_atr = v))} />
            <Num label="Min stop (× ATR)" step="0.1" value={st.stops.min_stop_atr} onChange={(v) => upd((x) => void (x.strategy.stops.min_stop_atr = v))} />
            <Num label="Max stop (× ATR)" step="0.1" value={st.stops.max_stop_atr} onChange={(v) => upd((x) => void (x.strategy.stops.max_stop_atr = v))} />
            <Field label="Primary target">
              <NativeSelect value={st.targets.primary_index} onChange={(e) => upd((x) => void (x.strategy.targets.primary_index = Number(e.target.value)))}>
                {st.targets.r_multiples.map((r, k) => <option key={k} value={k}>TP{k + 1} ({r}R)</option>)}
              </NativeSelect>
            </Field>
            {st.targets.r_multiples.map((r, k) => (
              <Num key={`r${k}`} label={`TP${k + 1} R multiple`} step="0.1" value={r} onChange={(v) => upd((x) => void (x.strategy.targets.r_multiples[k] = v))} />
            ))}
            {st.targets.allocation_pct.map((a, k) => (
              <Num key={`a${k}`} label={`TP${k + 1} close %`} step="5" value={a} onChange={(v) => upd((x) => void (x.strategy.targets.allocation_pct[k] = v))} />
            ))}
            <Toggle label="Partial exits" hint="Scale out at TP1-TP3" checked={st.targets.partial_exits} onChange={(v) => upd((x) => void (x.strategy.targets.partial_exits = v))} />
            <Toggle label="Breakeven after TP1" checked={st.targets.move_stop_to_breakeven} onChange={(v) => upd((x) => void (x.strategy.targets.move_stop_to_breakeven = v))} />
            <Num label="Min room to opposing zone (R)" step="0.1" value={st.qualification.min_rr} onChange={(v) => upd((x) => void (x.strategy.qualification.min_rr = v))} />
            <Num label="Min net R:R (primary)" step="0.1" hint="After fees & slippage" value={st.qualification.min_net_rr} onChange={(v) => upd((x) => void (x.strategy.qualification.min_net_rr = v))} />
            <Toggle label="Reject outside volatility band" checked={st.qualification.reject_outside_volatility} onChange={(v) => upd((x) => void (x.strategy.qualification.reject_outside_volatility = v))} />
          </div>
        </Section>

        <Section title="Account & risk" description="Used to size every trade plan. Estimates only; nothing is executed.">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Num label="Account balance (USDT)" step="any" value={s.risk.account_balance} onChange={(v) => upd((x) => void (x.risk.account_balance = v))} />
            <Num label="Risk per trade (%)" step="0.1" max={10} value={s.risk.risk_per_trade_pct} onChange={(v) => upd((x) => void (x.risk.risk_per_trade_pct = v))} />
            <Num label="Preferred leverage" step="1" min={1} value={s.risk.leverage} onChange={(v) => upd((x) => void (x.risk.leverage = v))} />
            <Num label="Maximum leverage" step="1" min={1} value={s.risk.max_leverage} onChange={(v) => upd((x) => void (x.risk.max_leverage = v))} />
            <Num label="Fee per side (%)" step="0.001" value={s.risk.fee_rate_pct} onChange={(v) => upd((x) => void (x.risk.fee_rate_pct = v))} />
            <Num label="Slippage (%)" step="0.01" value={s.risk.slippage_pct} onChange={(v) => upd((x) => void (x.risk.slippage_pct = v))} />
            <Num label="Maint. margin fallback (%)" step="0.1" hint="Used if Bybit tiers are unavailable" value={s.risk.maintenance_margin_pct} onChange={(v) => upd((x) => void (x.risk.maintenance_margin_pct = v))} />
            <Num label="Signal expiry (candles)" hint="Outcome evaluation stops after this many candles." min={1} value={s.evaluation.max_bars} onChange={(v) => upd((x) => void (x.evaluation.max_bars = v))} />
          </div>
        </Section>

        <Section title="Indicator parameters">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {NUM_FIELDS.filter((f) => f.key !== "swing_strength").map((fld) => (
              <Num key={fld.key} label={fld.label} step={fld.step} value={st[fld.key]} onChange={(v) => upd((x) => void (x.strategy[fld.key] = v))} />
            ))}
          </div>
        </Section>

        <div className="space-y-4">
          <Section title="Multi-timeframe trend confirmation" description="Higher-timeframe EMA trend must agree. Only closed higher-timeframe candles are used.">
            <div className="grid grid-cols-2 gap-3">
              <Toggle label="Enabled" checked={st.mtf.enabled} onChange={(v) => upd((x) => void (x.strategy.mtf.enabled = v))} />
              <Field label="Mode">
                <NativeSelect value={st.mtf.mode} onChange={(e) => upd((x) => void (x.strategy.mtf.mode = e.target.value as "quality" | "filter"))}>
                  <option value="quality">Flag quality (keep all signals)</option>
                  <option value="filter">Filter (reject disagreeing setups)</option>
                </NativeSelect>
              </Field>
            </div>
            <div className="mt-3 flex flex-wrap gap-3 text-sm">
              {TIMEFRAMES.map((tf) => (
                <label key={tf} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={st.mtf.timeframes.includes(tf)} onChange={() => upd((x) => void (x.strategy.mtf.timeframes = toggleIn(x.strategy.mtf.timeframes, tf)))} />
                  {tf}
                </label>
              ))}
            </div>
          </Section>
          <Section title="Score weights" description="Points per component, rescaled to 0-100 over evaluated components. The score measures rule agreement, not a win probability.">
            <div className="grid grid-cols-3 gap-3">
              {(Object.keys(st.weights) as (keyof StrategyParams["weights"])[]).map((k) => (
                <Num key={k} label={k.replace("_", " ")} min={0} value={st.weights[k]} onChange={(v) => upd((x) => void (x.strategy.weights[k] = v))} />
              ))}
            </div>
          </Section>
        </div>
      </div>
    </>
  );
}
