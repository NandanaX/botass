"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Copy, Download, FileText, X } from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { Time } from "@/components/time";
import { ANALYSIS_TOGGLE_LABELS, buildAnalysisOverlays, DEFAULT_ANALYSIS_TOGGLES, type AnalysisToggles } from "@/components/analysis-overlays";
import { PageHeader } from "@/components/app-shell";
import { ChartFrame } from "@/components/chart-frame";
import { ChartToggleBar, DEFAULT_TOGGLES, PriceChart, type ChartToggles, type FocusPlan } from "@/components/price-chart";
import { Empty, ErrorBox, TrendText } from "@/components/status";
import { HtfSummary } from "@/components/strategy-d";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/form";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { AnalysisResult, BiasLabel, EventStat, MtfRow, Scenario } from "@/lib/analysis-types";
import { fmtNum, fmtPrice, fmtUsd } from "@/lib/format";
import { TIMEFRAMES } from "@/lib/types";

const BIAS_VARIANT: Record<string, "bull" | "bear" | "warn" | "outline" | "info"> = {
  "BULLISH BIAS": "bull", "BEARISH BIAS": "bear", NEUTRAL: "outline", CONFLICTED: "warn", "INSUFFICIENT DATA": "outline", UNAVAILABLE: "outline",
};

function BiasBadge({ bias }: { bias: BiasLabel | string }) {
  return <Badge variant={BIAS_VARIANT[bias] ?? "outline"}>{bias}</Badge>;
}

function MtfPanel({ rows, selected, onSelect, summary, conflicts }: { rows: MtfRow[]; selected: string; onSelect: (tf: string) => void; summary?: string; conflicts?: string[] }) {
  return (
    <Card className="mb-4">
      <CardHeader className="pb-1">
        <CardTitle>Multi-timeframe analysis</CardTitle>
        <CardDescription>{summary ?? "Top-down: 1W/1D macro trend → 4H structure → 1H setup → 15M entry → 5M refinement. Click a timeframe to inspect it."}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
          {rows.map((r) => (
            <button key={r.timeframe} onClick={() => onSelect(r.timeframe)}
              className={`rounded-md border p-2 text-left text-xs transition-colors hover:bg-muted/50 ${selected === r.timeframe ? "border-primary bg-primary/10" : ""}`}
              title={r.error ?? (r.last_event ? r.last_event.label : undefined)}>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm font-semibold">{r.timeframe.toUpperCase()}</span>
                <BiasBadge bias={r.bias} />
              </div>
              <div className="text-muted-foreground">{r.role}</div>
              <div>Trend <TrendText value={r.trend} /> · {r.structure?.toLowerCase().replace("_", " ")}</div>
              {r.momentum && <div className="text-muted-foreground">Momentum {r.momentum.toLowerCase()}</div>}
              {r.last_event && <div className={r.last_event.direction === "BULLISH" ? "text-bull" : "text-bear"}>{r.last_event.kind === "CHOCH" ? "CHoCH" : "BOS"} {r.last_event.direction.toLowerCase()} @ {fmtPrice(r.last_event.level)}</div>}
              {r.nearest_support && <div className="tabular text-bull">S {fmtPrice(r.nearest_support.lower)}–{fmtPrice(r.nearest_support.upper)}</div>}
              {r.nearest_resistance && <div className="tabular text-bear">R {fmtPrice(r.nearest_resistance.lower)}–{fmtPrice(r.nearest_resistance.upper)}</div>}
              {!!r.patterns?.length && <div className="truncate text-muted-foreground">{r.patterns.join(", ")}</div>}
              <div className="text-[10px] text-muted-foreground">last candle <Time iso={r.candle_open_time} /></div>
            </button>
          ))}
        </div>
        {!!conflicts?.length && <div className="mt-2 space-y-0.5 text-xs text-warn">{conflicts.map((c) => <div key={c}>⚠ {c}</div>)}</div>}
      </CardContent>
    </Card>
  );
}

function EvidencePanel({ a }: { a: AnalysisResult }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">Evidence breakdown <BiasBadge bias={a.bias} /></CardTitle>
        <CardDescription>
          Alignment {a.alignment_score}/100 (net {a.net_score >= 0 ? "+" : ""}{a.net_score.toFixed(2)}). {a.evidence.alignment_note} Correlated indicators are grouped and counted once.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-1.5 text-xs">
        {a.evidence.categories.map((c) => (
          <div key={c.category}>
            <button className="flex w-full items-center gap-2" onClick={() => setOpen(open === c.category ? null : c.category)}>
              <span className="w-36 text-left">{c.label} <span className="text-muted-foreground">({c.weight})</span></span>
              <div className="relative h-2 flex-1 rounded bg-muted">
                <div className="absolute left-1/2 top-0 h-2 w-px bg-border" />
                {c.evaluable && (
                  <div className={`absolute top-0 h-2 rounded ${c.score >= 0 ? "bg-bull" : "bg-bear"}`}
                    style={c.score >= 0 ? { left: "50%", width: `${c.score * 50}%` } : { right: "50%", width: `${-c.score * 50}%` }} />
                )}
              </div>
              <span className="tabular w-12 text-right">{c.evaluable ? (c.score >= 0 ? "+" : "") + c.score.toFixed(2) : "n/a"}</span>
            </button>
            {open === c.category && (
              <ul className="mb-2 ml-2 mt-1 space-y-0.5 border-l pl-2">
                {c.items.length === 0 && <li className="text-muted-foreground">No evidence in this category.</li>}
                {c.items.map((e, k) => (
                  <li key={k} className={e.direction > 0 ? "text-bull" : e.direction < 0 ? "text-bear" : "text-muted-foreground"}>
                    {e.direction > 0 ? "▲" : e.direction < 0 ? "▼" : "•"} {e.text} <span className="text-muted-foreground">[{e.group}, {Math.round(e.strength * 100)}%]</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
        {a.conflicts.map((c) => <div key={c} className="text-warn">⚠ Conflict: {c}</div>)}
      </CardContent>
    </Card>
  );
}

function ScenarioCard({ s }: { s: Scenario }) {
  const qualified = s.status === "QUALIFIED";
  const variant = qualified ? "bull" : s.status === "REJECTED" ? "warn" : s.status === "ACTIVE" ? "info" : "outline";
  const tps = s.targets ?? s.hypothetical_targets ?? [];
  return (
    <div className={`rounded-md border p-3 text-xs ${qualified ? "border-bull/50" : ""}`}>
      <div className="mb-1 flex items-center justify-between">
        <span className="font-semibold">{s.name}</span>
        <Badge variant={variant}>{s.status}</Badge>
      </div>
      {qualified && <div className="mb-1 text-bull">✅ Qualified by the strategy engine: stored as a signal and alerted (watchlist coins, scanned timeframes).</div>}
      {s.status === "DEVELOPING" && <div className="mb-1 text-muted-foreground">Not an active signal. Levels below are hypothetical, for planning.</div>}
      {s.status === "REJECTED" && <div className="mb-1 text-warn">Triggered but rejected by mandatory rules; not a signal.</div>}
      {s.status === "NOT APPLICABLE" && s.note && <div className="mb-1 text-muted-foreground">{s.note}</div>}
      {s.trigger && <div><span className="text-muted-foreground">Trigger: </span>{s.trigger}</div>}
      {s.confirmation && <div><span className="text-muted-foreground">Confirmation: </span>{s.confirmation}</div>}
      {s.direction !== "NONE" && (
        <div className={`mt-1 grid grid-cols-2 gap-x-3 ${qualified ? "" : "opacity-85"}`}>
          {s.entry_zone && <div>Entry zone <span className="tabular">{fmtPrice(s.entry_zone[0])}–{fmtPrice(s.entry_zone[1])}</span></div>}
          <div>Entry <span className="tabular">{fmtPrice(s.entry ?? s.hypothetical_entry)}</span></div>
          <div>Stop <span className="tabular text-bear">{fmtPrice(s.stop_loss ?? s.hypothetical_stop)}</span></div>
          <div>R:R <span className="tabular">{s.risk_reward != null ? `1:${fmtNum(s.risk_reward)}` : s.hypothetical_rr ? `1:${s.hypothetical_rr}` : "—"}</span></div>
          {tps.map((t) => <div key={t.label} className={t.blocked ? "text-warn" : "text-bull"}>{t.label} <span className="tabular">{fmtPrice(t.price)}</span> ({t.r_multiple}R)</div>)}
        </div>
      )}
      {qualified && s.sizing && <div className="mt-1">Size {s.sizing.quantity.toLocaleString("en-US", { maximumFractionDigits: 8 })} · notional {fmtUsd(s.sizing.notional)} · risk ≈ {fmtUsd(s.sizing.estimated_loss)}</div>}
      {s.watch_reason && <div className="mt-1 text-muted-foreground">Strategy: {s.watch_reason}</div>}
      {!!s.reasons?.length && (
        <ul className="mt-1 list-disc pl-4 text-muted-foreground">
          {s.reasons.map((r, k) => <li key={k}>{typeof r === "string" ? r : r.message}</li>)}
        </ul>
      )}
      {!!s.opposing_evidence?.length && <div className="mt-1 text-muted-foreground">Opposing: {s.opposing_evidence.slice(0, 3).join(" | ")}</div>}
      {!!s.invalidation?.length && <div className="mt-1 text-muted-foreground">Invalidation: {s.invalidation.join(" ")}</div>}
      {s.expiration && <div className="text-muted-foreground">Expiry: {s.expiration}</div>}
    </div>
  );
}

function Stat({ v }: { v: EventStat }) {
  if (v.insufficient_data) return <span className="text-muted-foreground" title={v.note}>n={v.n} · insufficient</span>;
  return <span className="tabular">n={v.n} · hit {(v.hit_rate! * 100).toFixed(0)}% · {v.mean_fwd_r! >= 0 ? "+" : ""}{v.mean_fwd_r!.toFixed(2)}R</span>;
}

function ValidationPanel({ symbol, tf }: { symbol: string; tf: string }) {
  const [days, setDays] = useState(180);
  const [abl, setAbl] = useState(false);
  const run = useMutation({ mutationFn: () => api.validation(symbol, tf, days, abl) });
  const es = run.data?.event_study;
  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>Historical validation ({tf})</CardTitle>
        <CardDescription>Does each pattern or structure event actually precede moves on {symbol}? Chronological in-sample / out-of-sample split; fewer than 30 events shows “insufficient”.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1">Days <input type="number" min={7} max={1500} value={days} onChange={(e) => setDays(Number(e.target.value))} className="h-7 w-20 rounded border bg-background px-2" /></label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={abl} onChange={(e) => setAbl(e.target.checked)} /> Include walk-forward ablation (slower)</label>
          <Button size="sm" onClick={() => run.mutate()} disabled={run.isPending}>{run.isPending ? "Running…" : "Run validation"}</Button>
        </div>
        <ErrorBox error={run.error} />
        {es && (
          <>
            <p className="text-muted-foreground">{es.method} Split at <Time iso={es.split_time} /> · {es.candles} candles.</p>
            <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-0.5">
              <span className="font-semibold">Event</span><span className="font-semibold">In-sample</span><span className="font-semibold">Out-of-sample (unseen)</span>
              <span className="text-muted-foreground">Baseline: every bar, both directions</span><Stat v={es.baseline.in_sample} /><Stat v={es.baseline.out_of_sample} />
              {es.events.map((e) => (
                <span key={e.group + e.name} className="contents">
                  <span>{e.name} <span className="text-muted-foreground">({e.group})</span></span>
                  <Stat v={e.in_sample} />
                  <Stat v={e.out_of_sample} />
                </span>
              ))}
            </div>
          </>
        )}
        {run.data?.ablation && (
          <div>
            <div className="mb-1 font-semibold">Ablation ({run.data.ablation.folds} walk-forward folds)</div>
            <div className="grid grid-cols-[1fr_auto_auto_1.5fr] gap-x-3 gap-y-0.5">
              {run.data.ablation.variants.map((v) => (
                <span key={v.variant} className="contents">
                  <span>{v.label}</span>
                  <span className="tabular">{v.total_trades ?? "—"} trades</span>
                  <span className="tabular">{v.mean_fold_return_pct != null ? `${v.mean_fold_return_pct >= 0 ? "+" : ""}${v.mean_fold_return_pct}%/fold` : v.error}</span>
                  <span className="text-muted-foreground">{v.verdict ?? (v.variant === "baseline" ? "reference" : "")}</span>
                </span>
              ))}
            </div>
            <p className="mt-1 text-muted-foreground">{run.data.ablation.note}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ReportModal({ symbol, tf, onClose }: { symbol: string; tf: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["report", symbol, tf], queryFn: () => api.report(symbol, tf) });
  const [copied, setCopied] = useState(false);
  const download = () => {
    if (!q.data) return;
    const blob = new Blob([q.data.text], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${symbol}-${tf}-technical-analysis.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-auto bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-3xl rounded-lg border bg-card p-4" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Technical analysis · {symbol} {tf}</h2>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={!q.data} onClick={() => { navigator.clipboard.writeText(q.data!.text); setCopied(true); }}>{copied ? <Check /> : <Copy />} Copy</Button>
            <Button size="sm" variant="outline" disabled={!q.data} onClick={download}><Download /> .txt</Button>
            <Button size="sm" variant="outline" onClick={onClose} aria-label="Close"><X /></Button>
          </div>
        </div>
        <ErrorBox error={q.error} />
        {q.isLoading && <Empty>Generating from the latest confirmed candles…</Empty>}
        {q.data && (
          <div className="space-y-3 text-sm">
            <p className="text-xs text-muted-foreground">
              Generated <Time iso={q.data.generated_at} seconds /> from the candle <Time iso={q.data.generated_from_candle} /> · {q.data.data_source.exchange} {q.data.data_source.market} · {q.data.method}
            </p>
            {q.data.sections.map((sec) => (
              <div key={sec.title}>
                <h3 className="mb-1 font-semibold">{sec.title}</h3>
                <ul className="list-disc space-y-0.5 pl-5 text-xs">{sec.lines.map((l, k) => <li key={k}>{l}</li>)}</ul>
              </div>
            ))}
            <p className="text-xs text-muted-foreground">Research analysis only. Not financial advice; nothing is executed.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function AnalysisInner() {
  const params = useParams<{ symbol: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const symbol = (params.symbol || "BTCUSDT").toUpperCase();
  const tf = search.get("tf") && TIMEFRAMES.includes(search.get("tf") as never) ? search.get("tf")! : "15m";
  const [toggles, setToggles] = useState<ChartToggles>({ ...DEFAULT_TOGGLES, signals: false });
  const [at, setAt] = useState<AnalysisToggles>(DEFAULT_ANALYSIS_TOGGLES);
  const [report, setReport] = useState(false);
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const mtf = useQuery({ queryKey: ["mtf", symbol], queryFn: () => api.mtf(symbol), refetchInterval: 60_000 });
  const analysis = useQuery({ queryKey: ["analysis", symbol, tf], queryFn: () => api.analysis(symbol, tf), refetchInterval: 60_000 });
  const overlays = useQuery({ queryKey: ["overlays", symbol, tf], queryFn: () => api.overlays(symbol, tf, 400), refetchInterval: 60_000 });
  const candles = useQuery({ queryKey: ["candles", symbol, tf], queryFn: () => api.candles(symbol, tf, 400), refetchInterval: 60_000 });
  const ov = useMemo(() => buildAnalysisOverlays(overlays.data, at), [overlays.data, at]);
  const a = analysis.data;
  const watchlist = settings.data?.settings.watchlist ?? [];
  const go = (sym: string, t: string) => router.push(`/analysis/${sym}?tf=${t}`);
  const plan = useMemo<FocusPlan | null>(() => {
    if (!a || !at.plan) return null;
    const s = a.scenarios.find((x) => x.status === "QUALIFIED") ?? null;
    if (!s) return null;
    return {
      direction: s.direction as "LONG" | "SHORT", label: `${s.direction} qualified`, entry: s.entry ?? null, stop: s.stop_loss ?? null,
      targets: (s.targets ?? []).map((t) => ({ label: t.label, price: t.price, blocked: !!t.blocked })),
      confirmationTime: null, invalidation: null, entryZone: (s.entry_zone as [number, number]) ?? null,
    };
  }, [a, at.plan]);

  return (
    <>
      <PageHeader
        title={`Chart analysis · ${symbol}`}
        description="Explainable technical analysis from confirmed Bybit candles: structure, zones, patterns, momentum, volume and futures context. Every item is traceable to a candle and a rule."
        actions={
          <>
            <NativeSelect value={symbol} onChange={(e) => go(e.target.value, tf)} className="h-8 w-32" aria-label="Symbol">
              {[...new Set([symbol, ...watchlist])].map((s) => <option key={s}>{s}</option>)}
            </NativeSelect>
            <Tabs value={tf} onValueChange={(v) => go(symbol, v)}>
              <TabsList>{TIMEFRAMES.map((t) => <TabsTrigger key={t} value={t}>{t}</TabsTrigger>)}</TabsList>
            </Tabs>
            <Button onClick={() => setReport(true)}><FileText /> Generate Technical Analysis</Button>
          </>
        }
      />
      {report && <ReportModal symbol={symbol} tf={tf} onClose={() => setReport(false)} />}
      <div className="mb-3"><HtfSummary symbol={symbol} /></div>
      {mtf.data ? <MtfPanel rows={mtf.data.timeframes} selected={tf} onSelect={(t) => go(symbol, t)} summary={mtf.data.summary} conflicts={mtf.data.conflicts} /> : <ErrorBox error={mtf.error} />}
      <ErrorBox error={analysis.error ?? candles.error} />
      {a?.warning && <div className="mb-3 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">{a.warning}</div>}

      <div className="grid items-start gap-4 xl:grid-cols-[1fr_400px]">
        <div className="space-y-4">
          <ChartFrame
            toolbar={
              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="text-sm font-semibold">{symbol} · {tf}</span>
                  <ChartToggleBar toggles={toggles} onChange={setToggles} />
                </div>
                <div className="flex flex-wrap gap-3 text-xs">
                  {(Object.keys(ANALYSIS_TOGGLE_LABELS) as (keyof AnalysisToggles)[]).map((k) => (
                    <label key={k} className="flex cursor-pointer items-center gap-1.5 text-muted-foreground">
                      <input type="checkbox" className="accent-primary" checked={at[k]} onChange={(e) => setAt({ ...at, [k]: e.target.checked })} />
                      {ANALYSIS_TOGGLE_LABELS[k]}
                    </label>
                  ))}
                </div>
              </div>
            }
          >
            {(full) =>
              candles.data ? (
                <PriceChart data={candles.data} toggles={{ ...toggles, signals: at.plan || toggles.signals }} focusPlan={plan}
                  height={540} fill={full} shapes={ov.shapes} extraMarkers={ov.markers} extraLines={ov.lines} />
              ) : (
                <div className={`flex ${full ? "h-full" : "h-[540px]"} items-center justify-center text-sm text-muted-foreground`}>{candles.isLoading ? "Loading candles…" : "No chart data"}</div>
              )
            }
          </ChartFrame>

          {a && (
            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader><CardTitle>Structure &amp; patterns</CardTitle><CardDescription>{a.structure_detail.explanation}</CardDescription></CardHeader>
                <CardContent className="space-y-2 text-xs">
                  <div>
                    <div className="mb-0.5 font-semibold">Structure events</div>
                    {a.structure_detail.events.slice(-6).reverse().map((e) => (
                      <div key={e.index + e.kind} className={e.kind === "WICK_BREAK" ? "text-muted-foreground" : e.direction === "BULLISH" ? "text-bull" : "text-bear"} title={e.label}>
                        {e.kind === "CHOCH" ? "CHoCH" : e.kind === "BOS" ? "BOS" : "Wick (temporary)"} {e.direction.toLowerCase()} @ {fmtPrice(e.level)} · <Time iso={e.time} />
                      </div>
                    ))}
                    {a.structure_detail.events.length === 0 && <div className="text-muted-foreground">No confirmed structural break yet.</div>}
                  </div>
                  <div>
                    <div className="mb-0.5 font-semibold">Chart patterns</div>
                    {a.chart_patterns.length ? a.chart_patterns.map((p) => (
                      <div key={p.id} title={p.explanation}>
                        <Badge variant={p.status === "CONFIRMED" ? (p.breakout_direction === "BULLISH" ? "bull" : "bear") : p.status === "DEVELOPING" ? "info" : "outline"}>{p.status.toLowerCase()}</Badge>{" "}
                        {p.name}{p.target != null && p.status !== "INVALIDATED" ? ` · target ${fmtPrice(p.target)} (theoretical)` : ""}
                      </div>
                    )) : <div className="text-muted-foreground">No pattern meets the geometric rules; none is forced.</div>}
                  </div>
                  <div>
                    <div className="mb-0.5 font-semibold">Candlesticks (last 3 candles)</div>
                    {a.candlestick_patterns.length ? a.candlestick_patterns.map((p) => (
                      <div key={p.name + p.end_time} title={p.explanation} className={p.direction === "BULLISH" ? "text-bull" : p.direction === "BEARISH" ? "text-bear" : "text-muted-foreground"}>
                        {p.name} · {p.in_context ? "in context" : "out of context"}{p.at_level ? " · at level" : ""}{p.volume_confirmed ? " · volume ✓" : ""}
                      </div>
                    )) : <div className="text-muted-foreground">None.</div>}
                    <div className="text-[11px] text-muted-foreground">Patterns never trigger trades by themselves.</div>
                  </div>
                  {!!a.divergences.length && <div>{a.divergences.map((d) => <div key={d.kind} className="text-warn">{d.text}</div>)}</div>}
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle>Levels, volume &amp; futures</CardTitle></CardHeader>
                <CardContent className="space-y-2 text-xs">
                  <div>Support <span className="tabular text-bull">{a.nearest_support ? `${fmtPrice(a.nearest_support.lower)}–${fmtPrice(a.nearest_support.upper)} (${a.nearest_support.timeframe})` : "—"}</span></div>
                  <div>Resistance <span className="tabular text-bear">{a.nearest_resistance ? `${fmtPrice(a.nearest_resistance.lower)}–${fmtPrice(a.nearest_resistance.upper)} (${a.nearest_resistance.timeframe})` : "—"}</span></div>
                  {a.levels.slice(0, 6).map((l) => <div key={l.kind} className="text-muted-foreground">{l.label}: <span className="tabular text-foreground">{fmtPrice(l.price)}</span> ({l.role.toLowerCase()}{l.distance_atr != null ? `, ${l.distance_atr} ATR` : ""})</div>)}
                  {a.trendlines.map((t) => <div key={t.kind} className="text-muted-foreground">{t.label}</div>)}
                  {a.flips.map((f) => <div key={`${f.timeframe}-${f.lower}-${f.upper}`} className="text-muted-foreground">{f.timeframe} {fmtPrice(f.lower)}–{fmtPrice(f.upper)}: {f.note}</div>)}
                  {a.fibonacci && <div className="text-muted-foreground">{a.fibonacci.explanation}</div>}
                  <div className="border-t pt-1">Volume {fmtNum(a.volume_detail.rvol)}x{a.volume_detail.spike ? " (spike)" : ""}{a.volume_detail.vwap ? ` · ${a.volume_detail.above_vwap ? "above" : "below"} VWAP ${fmtPrice(a.volume_detail.vwap)}` : ""}{a.volume_detail.obv_divergence ? ` · OBV ${a.volume_detail.obv_divergence.toLowerCase()}` : ""}</div>
                  {a.futures ? (
                    <div className="border-t pt-1">
                      {a.futures.items.map((i) => <div key={i.key}>{i.text}</div>)}
                      <div className="mt-1 text-muted-foreground">
                        {Object.entries(a.futures.sources).map(([k, v]) => <div key={k}>{k.replace(/_/g, " ")}: {v.status}{v.note ? ` (${v.note})` : ""}</div>)}
                      </div>
                    </div>
                  ) : <div className="text-muted-foreground">Futures data unavailable.</div>}
                </CardContent>
              </Card>
            </div>
          )}
        </div>

        <div className="space-y-4">
          {a ? (
            <>
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center justify-between">{tf} assessment <BiasBadge bias={a.bias} /></CardTitle>
                  <CardDescription>Last confirmed candle <Time iso={a.candle_open_time} /> · close {fmtPrice(a.close)} · {a.data_source.exchange}</CardDescription>
                </CardHeader>
                <CardContent className="grid grid-cols-2 gap-y-1 text-xs">
                  <span className="text-muted-foreground">Trend</span><span className="text-right"><TrendText value={a.trend} /></span>
                  <span className="text-muted-foreground">Structure</span><span className="text-right">{a.structure.toLowerCase().replace("_", " ")}</span>
                  <span className="text-muted-foreground">Momentum</span><span className="text-right">{a.momentum.toLowerCase()}</span>
                  <span className="text-muted-foreground">Volatility</span><span className="text-right">{a.volatility.toLowerCase()}</span>
                  <span className="text-muted-foreground">ADX / MA alignment</span><span className="text-right">{fmtNum(a.trend_detail.adx, 1)} / {a.trend_detail.ma_alignment.toLowerCase()}</span>
                  <span className="text-muted-foreground">RSI / Stoch RSI</span><span className="text-right">{fmtNum(a.momentum_detail.rsi, 1)} / {fmtNum(a.momentum_detail.stochrsi_k, 0)}</span>
                  <span className="col-span-2 mt-1 text-[11px] text-muted-foreground">Analysis v{a.data_source.analysis_version} · strategy v{a.data_source.strategy_version}</span>
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle>Scenarios</CardTitle><CardDescription>Only the strategy engine can mark a scenario QUALIFIED; nothing is forced.</CardDescription></CardHeader>
                <CardContent className="space-y-2">{a.scenarios.map((s) => <ScenarioCard key={s.name} s={s} />)}</CardContent>
              </Card>
              <EvidencePanel a={a} />
            </>
          ) : (
            <Card><CardContent className="p-4"><Empty>{analysis.isLoading ? "Analysing confirmed candles…" : "No analysis."}</Empty></CardContent></Card>
          )}
        </div>
      </div>
      <ValidationPanel symbol={symbol} tf={tf} />
    </>
  );
}

export default function AnalysisPage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <AnalysisInner />
    </Suspense>
  );
}
