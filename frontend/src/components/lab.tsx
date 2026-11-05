"use client";

import { useQuery } from "@tanstack/react-query";
import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import { useMemo } from "react";
import { Time as TimeText } from "@/components/time";
import { DEFAULT_TOGGLES, PriceChart, type FocusPlan } from "@/components/price-chart";
import { DirectionBadge, Empty, ErrorBox, EvaluationBadge, StrategyLabel } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/form";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { CHART_TZ, fmtPrice, fmtClock } from "@/lib/format";
import type {
  Decision,
  Forensics,
  HypothesisResult,
  PeriodReport,
  RStats,
  StopStats,
  StudyResponse,
  TodayRow,
  Verdict,
} from "@/lib/research-types";
import type { CandlesResponse } from "@/lib/types";

// ---------------------------------------------------------------------------------- helpers

export const slTime = (iso: string | null | undefined, withDate = false) => fmtClock(iso, withDate);

export const todaySL = () => new Intl.DateTimeFormat("en-CA", { timeZone: CHART_TZ }).format(new Date());

function Rv({ v, digits = 2 }: { v: number | null | undefined; digits?: number }) {
  if (v === null || v === undefined || !Number.isFinite(v)) return <span className="text-muted-foreground">—</span>;
  return <span className={v > 0 ? "text-bull" : v < 0 ? "text-bear" : ""}>{v > 0 ? "+" : ""}{v.toFixed(digits)}R</span>;
}

const VERDICT: Record<string, "bull" | "info" | "outline" | "warn" | "bear"> = {
  VALIDATED: "bull", PROMISING: "info", NOT_VALIDATED: "outline", INSUFFICIENT_DATA: "warn", NONE: "outline",
};

function VerdictBadge({ v }: { v: Verdict | string }) {
  return <Badge variant={VERDICT[v] ?? "outline"}>{v.replace("_", " ")}</Badge>;
}

const CAUSE_VARIANT = { confirmed: "bear", suspected: "warn", undetermined: "outline" } as const;

const OUTCOME_VARIANT: Record<string, "bull" | "bear" | "warn" | "outline" | "info"> = {
  QUALIFIED: "bull", REJECTED: "bear", NO_TRADE: "warn", WATCHLIST: "info", ERROR: "outline",
};

function StatCells({ s }: { s: RStats | undefined }) {
  if (!s || !s.n) return <><TD className="text-right text-muted-foreground">0</TD><TD /><TD /><TD /><TD /></>;
  return (
    <>
      <TD className="text-right">{s.n}{s.insufficient_data && <span className="text-warn" title="fewer than 30 trades: no conclusion"> *</span>}</TD>
      <TD className="text-right">{s.win_rate_pct?.toFixed(1)}%</TD>
      <TD className="text-right"><Rv v={s.avg_r} /></TD>
      <TD className="text-right">{s.profit_factor?.toFixed(2) ?? "—"}</TD>
      <TD className="text-right text-muted-foreground">{s.avg_r_ci95 ? `${s.avg_r_ci95[0].toFixed(2)} … ${s.avg_r_ci95[1].toFixed(2)}` : "—"}</TD>
    </>
  );
}

function GroupTable({ title, groups, label = "Group" }: { title?: string; groups: Record<string, RStats> | undefined; label?: string }) {
  if (!groups) return null;
  return (
    <div>
      {title && <div className="mb-1 text-xs font-medium text-muted-foreground">{title}</div>}
      <Table>
        <THead><TR><TH>{label}</TH><TH className="text-right">Trades</TH><TH className="text-right">Win rate</TH><TH className="text-right">Avg net R</TH><TH className="text-right">PF</TH><TH className="text-right">95% CI avg R</TH></TR></THead>
        <TBody>
          {Object.entries(groups).map(([k, s]) => (
            <TR key={k}><TD>{k}</TD><StatCells s={s} /></TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

function Section({ id, title, description, children }: { id: string; title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card className="mb-4" id={id}>
      <CardHeader><CardTitle>{title}</CardTitle>{description && <CardDescription>{description}</CardDescription>}</CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Tile({ label, children, sub }: { label: string; children: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-md border p-3">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular">{children}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------------- A + B

function DecisionBadge({ d }: { d: Decision | null }) {
  if (!d) return <span className="text-muted-foreground">—</span>;
  return <Badge variant={OUTCOME_VARIANT[d.outcome] ?? "outline"} title={d.reason}>{d.outcome.replace("_", " ")}</Badge>;
}

export function TodaySection({ day, setDay, onSelect, selected }: { day: string; setDay: (d: string) => void; onSelect: (r: TodayRow) => void; selected: string | null }) {
  const q = useQuery({ queryKey: ["research-today", day], queryFn: () => api.researchToday(day), staleTime: 60_000 });
  const s = q.data?.summary;
  return (
    <Section id="today" title="A · Signal performance for the day"
      description="Live signals of the selected Sri Lanka calendar day, re-verified independently. 'Reported' is the stored outcome (gross, OHLC). 'Verified net' replays each signal with fees, slippage and funding. 'Proposed engine' replays the same day through the shadow decision engine (counterfactual simulation, not trades).">
      <div className="mb-3 flex items-center gap-2 text-xs">
        <span className="text-muted-foreground">Date (Sri Lanka)</span>
        <Input aria-label="Date" type="date" value={day} onChange={(e) => setDay(e.target.value)} className="h-8 w-40" />
        {q.isFetching && <span className="text-muted-foreground">Reconstructing signals… (first load fetches 1-minute data)</span>}
      </div>
      {q.error ? <ErrorBox error={q.error} /> : !q.data ? <Empty>Loading…</Empty> : !q.data.signals.length ? <Empty>No signals on {day}.</Empty> : (
        <>
          {s && (
            <div className="mb-4 grid grid-cols-2 gap-2 lg:grid-cols-4">
              <Tile label="Signals" sub={`${s.resolved} resolved · ${s.pending} pending`}>{s.signals}</Tile>
              <Tile label="Reported (gross)" sub={s.reported_gross ? `${s.reported_gross.wins}/${s.reported_gross.n} wins · ${s.reported_gross.win_rate_pct}%` : undefined}>
                <Rv v={s.reported_gross?.total_r} />
              </Tile>
              <Tile label="Verified net of costs" sub={s.reproduced_all ? "stored outcomes reproduced exactly" : "see per-signal verification"}>
                <Rv v={s.verified_net?.total_r} />
              </Tile>
              <Tile label="Proposed engine (counterfactual)" sub={`${s.counterfactual_taken_count} of ${s.signals} taken`}>
                <Rv v={s.counterfactual_taken?.total_r ?? 0} />
              </Tile>
            </div>
          )}
          <Table>
            <THead><TR><TH>Time (SL)</TH><TH>Signal</TH><TH className="text-right">Score</TH><TH>Outcome</TH><TH className="text-right">Gross</TH><TH className="text-right">Net</TH><TH>1m check</TH><TH>Causes (confirmed / suspected)</TH><TH>Proposed engine</TH></TR></THead>
            <TBody>
              {q.data.signals.map((r) => {
                const f = r.forensics;
                const v = f?.verification;
                const conf = (f?.causes ?? []).filter((c) => c.status === "confirmed" && c.code !== "not_a_loss");
                const susp = (f?.causes ?? []).filter((c) => c.status === "suspected");
                return (
                  <TR key={r.signal.id} onClick={() => onSelect(r)} className={`cursor-pointer ${selected === r.signal.id ? "bg-primary/10" : ""}`}>
                    <TD><TimeText iso={r.signal.candle_close_time} text={slTime(r.signal.candle_close_time)} /></TD>
                    <TD><span className="font-medium">{r.signal.symbol.replace("USDT", "")}</span> {r.signal.timeframe} <DirectionBadge direction={r.signal.direction} /> <StrategyLabel strategy={r.signal.strategy_type} /></TD>
                    <TD className="text-right">{r.signal.score}</TD>
                    <TD><EvaluationBadge status={r.signal.evaluation_status} /></TD>
                    <TD className="text-right"><Rv v={v?.replay_gross?.r} /></TD>
                    <TD className="text-right"><Rv v={v?.replay_net?.r} /></TD>
                    <TD>{f?.error ? <span className="text-warn">error</span> : v?.minute_agrees === true ? <span className="text-bull">agrees</span> : v?.minute_agrees === false ? <span className="text-bear">differs</span> : <span className="text-muted-foreground">n/a</span>}</TD>
                    <TD className="max-w-[340px]">
                      <div className="flex flex-wrap gap-1">
                        {conf.map((c) => <Badge key={c.code} variant="bear" title={c.evidence}>{c.code.replaceAll("_", " ")}</Badge>)}
                        {susp.map((c) => <Badge key={c.code} variant="warn" title={c.evidence}>{c.code.replaceAll("_", " ")}?</Badge>)}
                      </div>
                    </TD>
                    <TD title={r.counterfactual_decision?.reason}>
                      <DecisionBadge d={r.counterfactual_decision} />
                      {r.counterfactual_decision?.proposed_outcome?.net_r != null && <span className="ml-1 text-xs"><Rv v={r.counterfactual_decision.proposed_outcome.net_r} /></span>}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
          <p className="mt-2 text-xs text-muted-foreground">Click a row for its forensic report. Red = confirmed cause (measured and consistent with how the trade failed), amber = suspected (condition present, not provable from one trade). Hover a badge for the evidence.</p>
        </>
      )}
    </Section>
  );
}

function ForensicChart({ f, decision }: { f: Forensics; decision: Decision | null }) {
  const data = useMemo(() => ({
    symbol: f.symbol, timeframe: f.timeframe, candles: f.chart.candles, live_candle: null, overlays: {}, levels: [],
    zones: f.chart.zones, htf_zones: {}, swings: [], signals: [], analysis: {},
    data_quality: { current: true, gaps: [], refreshed_at: null }, ticker: null,
  }) as unknown as CandlesResponse, [f]);
  const plan: FocusPlan = useMemo(() => ({
    direction: f.direction, label: "signal", entry: f.entry_reference, stop: f.stop_loss,
    targets: [{ label: "TP (primary)", price: f.take_profit, blocked: false }],
    confirmationTime: f.chart.signal_time, invalidation: null, entryZone: null,
  }), [f]);
  const markers = useMemo(() => {
    const m: SeriesMarker<Time>[] = [{ time: f.chart.entry_time as UTCTimestamp, position: f.direction === "LONG" ? "belowBar" : "aboveBar", color: "#3b82f6", shape: "circle", text: "entry (next open)" }];
    if (f.chart.exit_time) m.push({ time: f.chart.exit_time as UTCTimestamp, position: "aboveBar", color: "#f5a524", shape: "square", text: `exit: ${f.outcome_evidence.exit_reason}` });
    return m;
  }, [f]);
  const lines = useMemo(() => {
    const p = decision?.proposed_plan;
    const c = f.chart.candles;
    if (!p || !c.length) return undefined;
    const span = (v: number) => [{ time: c[0].time, value: v }, { time: c[c.length - 1].time, value: v }];
    return [{ name: "Proposed SL", color: "#ea394399", data: span(p.stop_loss) }, { name: "Proposed TP", color: "#16c78499", data: span(p.take_profit) }];
  }, [decision, f]);
  return <PriceChart data={data} toggles={{ ...DEFAULT_TOGGLES, emas: false, htfZones: false }} focusPlan={plan} extraMarkers={markers} extraLines={lines} height={420} live={false} />;
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3 border-b border-border/40 py-1 text-xs"><span className="text-muted-foreground">{k}</span><span className="text-right tabular">{v}</span></div>;
}

export function ForensicPanel({ row }: { row: TodayRow }) {
  const f = row.forensics;
  const d = row.counterfactual_decision;
  if (!f || f.error) return <Section id="forensic" title="B · Signal forensics"><ErrorBox error={new Error(f?.error ?? "no forensic data")} /></Section>;
  const dg = f.diagnostics;
  const v = f.verification;
  const n = (x: unknown, dig = 2) => (typeof x === "number" && Number.isFinite(x) ? x.toFixed(dig) : "—");
  return (
    <Section id="forensic" title={`B · Forensics: ${f.symbol} ${f.timeframe} ${f.direction} at ${slTime(f.decision_time, true)}`}
      description="Rebuilt from the candles that existed at the signal's close; outcome evidence comes from later candles and is never used for the decision.">
      <ForensicChart f={f} decision={d} />
      <p className="mt-1 text-xs text-muted-foreground">Gold circle = signal candle; blue = hypothetical entry at the next open; square = exit. Dotted lines = the proposed engine&apos;s shadow stop/target (counterfactual).</p>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <div>
          <div className="mb-1 text-xs font-semibold">Verification</div>
          <KV k="Rebuilt decision matches stored" v={f.reconstruction.matches ? <span className="text-bull">yes</span> : <span className="text-bear">no: {f.reconstruction.differences.join("; ")}</span>} />
          <KV k="Same on full history" v={f.reconstruction.full_history_matches ? "yes" : "no"} />
          <KV k="Stored result" v={<>{v.stored_status} <Rv v={v.stored_r} /></>} />
          <KV k="Independent replay (gross)" v={<>{v.replay_gross.reason} <Rv v={v.replay_gross.r} /> · {v.replay_gross.bars_held} bars</>} />
          <KV k="Net of fees/slippage/funding" v={<><Rv v={v.replay_net.r} /> (cost {n(v.replay_net.cost_r)}R)</>} />
          <KV k="1-minute replay" v={v.minute_path ? `${v.minute_path.reason} at ${slTime(v.minute_path.exit_time)}` : "not available"} />
          <KV k="Reproduced" v={v.reproduced ? <span className="text-bull">exactly</span> : <span className="text-warn">no</span>} />
        </div>
        <div>
          <div className="mb-1 text-xs font-semibold">At the decision close</div>
          <KV k="Regime" v={`${dg.regime.label} (${dg.regime.alignment.toLowerCase()})`} />
          {Object.entries(dg.htf_regimes).map(([tf, r]) => <KV key={tf} k={`${tf} regime`} v={`${r.label} (${r.alignment.toLowerCase()})`} />)}
          {dg.btc && <KV k="BTC regime" v={`${dg.btc.label} (${dg.btc.alignment.toLowerCase()})`} />}
          <KV k="Entry quality" v={<Badge variant={["EXTENDED", "EXHAUSTION_RISK", "INVALID"].includes(dg.entry_quality.class) ? "warn" : "outline"}>{dg.entry_quality.class}</Badge>} />
          <KV k="Extension from EMA-fast" v={`${n(dg.entry_quality.ext_fast_atr)} ATR`} />
          <KV k="Distance to opposing swing" v={`${n(dg.entry_quality.swing_room_atr)} ATR`} />
          <KV k="24h move / from 24h extreme" v={`${n(dg.before.change_24h_pct)}% · ${n(f.direction === "SHORT" ? dg.before.distance_from_24h_low_atr : dg.before.distance_from_24h_high_atr)} ATR`} />
          <KV k="Stop distance vs noise (q80)" v={`${n(dg.stop.distance_atr)} vs ${n(dg.stop.noise_q80_atr)} ATR`} />
          <KV k="RSI · RVOL" v={`${n(dg.indicators.rsi, 1)} · ${n(dg.indicators.rvol)}x`} />
          <KV k="Correlated same-direction signals (±60m)" v={dg.correlated_signals.length ? dg.correlated_signals.map((c) => `${c.symbol.replace("USDT", "")} ${c.timeframe}${c.corr_1h != null ? ` (${c.corr_1h.toFixed(2)})` : ""}`).join(", ") : "none"} />
        </div>
        <div>
          <div className="mb-1 text-xs font-semibold">Outcome evidence (after the signal)</div>
          <KV k="Max favourable / adverse" v={`${n(f.outcome_evidence.mfe_r)}R / ${n(f.outcome_evidence.mae_r)}R`} />
          <KV k="Target reached after the stop" v={f.outcome_evidence.target_reached_after_stop == null ? "—" : f.outcome_evidence.target_reached_after_stop ? "yes" : "no"} />
          <KV k="Failed breakout" v={f.outcome_evidence.failed_breakout == null ? "n/a" : f.outcome_evidence.failed_breakout ? "yes" : "no"} />
          {(f.outcome_evidence.opposing_levels ?? []).map((l) => <KV key={l.level} k={l.level} v={`${fmtPrice(l.price)} · ${l.distance_atr} ATR · ${l.held ? "held" : "broken"}`} />)}
          <div className="mb-1 mt-3 text-xs font-semibold">Failure analysis</div>
          <div className="space-y-1">
            {f.causes.map((c) => (
              <div key={c.code} className="text-xs"><Badge variant={CAUSE_VARIANT[c.status]}>{c.status}</Badge> <span className="font-medium">{c.code.replaceAll("_", " ")}</span> <span className="text-muted-foreground">: {c.evidence}</span></div>
            ))}
          </div>
        </div>
      </div>
      {d && (
        <div className="mt-4">
          <div className="mb-1 text-xs font-semibold">Proposed decision engine ({d.mode}, counterfactual): <DecisionBadge d={d} /> <span className="font-normal text-muted-foreground">{d.reason}</span></div>
          <Table>
            <THead><TR><TH>#</TH><TH>Check</TH><TH>Result</TH><TH>Rule type</TH><TH>Detail</TH></TR></THead>
            <TBody>
              {d.checks.map((c) => (
                <TR key={c.step}>
                  <TD>{c.step}</TD><TD className="whitespace-nowrap">{c.check}</TD>
                  <TD><Badge variant={c.status === "pass" ? "bull" : c.status === "fail" ? "bear" : c.status === "warn" ? "warn" : "outline"}>{c.status}</Badge></TD>
                  <TD className="whitespace-nowrap text-muted-foreground">{c.rule_type.replace("_", " ")}</TD>
                  <TD className="text-xs">{c.detail}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
          {d.proposed_plan && <p className="mt-1 text-xs text-muted-foreground">Proposed plan: {d.proposed_plan.stop_method}, SL {fmtPrice(d.proposed_plan.stop_loss)}, TP {fmtPrice(d.proposed_plan.take_profit)}{d.proposed_outcome ? ` → ${d.proposed_outcome.reason ?? d.proposed_outcome.status} ${d.proposed_outcome.net_r != null ? `${d.proposed_outcome.net_r.toFixed(2)}R net` : ""}` : ""} ({d.proposed_plan.note}).</p>}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------------- C..J

function PeriodRow({ label, rep, base }: { label: React.ReactNode; rep: PeriodReport | null | undefined; base?: PeriodReport }) {
  if (!rep) return null;
  return (
    <TR>
      <TD>{label}</TD>
      {(["dev", "val", "test", "oos"] as const).map((p) => (
        <TD key={p} className="text-right" title={base ? `baseline ${base[p]?.avg_r?.toFixed(3)}R` : undefined}>
          <Rv v={rep[p]?.avg_r} /> <span className="text-muted-foreground">({rep[p]?.n ?? 0})</span>
        </TD>
      ))}
      <TD className="text-right"><Rv v={rep.oos?.total_r} digits={1} /></TD>
      <TD className="text-right">{rep.oos?.max_drawdown_r?.toFixed(1) ?? "—"}R</TD>
      <TD className="text-right">{rep.oos?.profit_factor?.toFixed(2) ?? "—"}</TD>
    </TR>
  );
}

function PeriodHead({ first = "Variant" }: { first?: string }) {
  return <THead><TR><TH>{first}</TH><TH className="text-right">Dev avg R (n)</TH><TH className="text-right">Validation</TH><TH className="text-right">Unseen test</TH><TH className="text-right">Out-of-sample</TH><TH className="text-right">OOS total</TH><TH className="text-right">OOS max DD</TH><TH className="text-right">OOS PF</TH></TR></THead>;
}

function Improvements({ s }: { s: StudyResponse }) {
  const tq = useQuery({ queryKey: ["study-trades"], queryFn: () => api.studyTrades(150) });
  const base = s.baseline.results;
  const order = (h: HypothesisResult) => ({ VALIDATED: 0, PROMISING: 1, NOT_VALIDATED: 2, INSUFFICIENT_DATA: 3, NONE: 4 }[h.verdict] ?? 5);
  const hyps = [...s.hypotheses].sort((a, b) => order(a) - order(b) || a.key.localeCompare(b.key));
  const c = s.combined;
  return (
    <Section id="improvements" title="J · Candidate strategy improvements (experiment framework)"
      description={`One change at a time against the live baseline, net of costs. Dev = ${s.meta.split.dev}; validation = ${s.meta.split.val}; unseen test = ${s.meta.split.test}. A change is VALIDATED only if expectancy improves in validation and test (and in dev for fixed rules) with day-block bootstrap P(improvement) ≥ 0.95.`}>
      <div className="mb-3 rounded-md border border-primary/30 bg-primary/5 p-3 text-xs">
        <div className="mb-1 font-semibold">Proposed combination for shadow mode: {c.keys.join(" + ") || "none"} <VerdictBadge v={c.verdict} /></div>
        <Table><PeriodHead first="Strategy" /><TBody>
          <PeriodRow label="Baseline (live rules)" rep={base} />
          <PeriodRow label={`Proposed (${c.keys.join(" + ")})`} rep={c.results} base={base} />
        </TBody></Table>
        <div className="mt-1 text-muted-foreground">{c.notes.join(" · ")}. Positive expectancy demonstrated: <b className={c.results?.positive_expectancy ? "text-bull" : "text-bear"}>{c.results?.positive_expectancy ? "yes" : "no"}</b> (OOS 95% CI of avg net R {c.results?.oos.avg_r_ci95 ? `${c.results.oos.avg_r_ci95[0].toFixed(3)} … ${c.results.oos.avg_r_ci95[1].toFixed(3)}` : "—"}). Shadow mode only; nothing is promoted automatically.</div>
      </div>
      <Table>
        <THead><TR><TH>Key</TH><TH>Change</TH><TH>Verdict</TH><TH className="text-right">Val</TH><TH className="text-right">Test</TH><TH className="text-right">OOS n</TH><TH className="text-right">OOS total</TH><TH className="text-right">OOS DD</TH><TH className="text-right">P(impr.)</TH></TR></THead>
        <TBody>
          <TR className="bg-muted/30"><TD>—</TD><TD>Baseline: {s.baseline.definition}</TD><TD /><TD className="text-right"><Rv v={base.val.avg_r} /></TD><TD className="text-right"><Rv v={base.test.avg_r} /></TD><TD className="text-right">{base.oos.n}</TD><TD className="text-right"><Rv v={base.oos.total_r} digits={1} /></TD><TD className="text-right">{base.oos.max_drawdown_r?.toFixed(1)}R</TD><TD /></TR>
          {hyps.map((h) => (
            <TR key={h.key}>
              <TD className="font-medium">{h.key}</TD>
              <TD className="max-w-[420px] text-xs" title={`${h.hypothesis}\n${h.notes.join("\n")}`}><div className="font-medium">{h.title}</div><div className="text-muted-foreground">{h.change}</div></TD>
              <TD><VerdictBadge v={h.verdict} /></TD>
              <TD className="text-right"><Rv v={h.results.val.avg_r} /></TD>
              <TD className="text-right"><Rv v={h.results.test.avg_r} /></TD>
              <TD className="text-right">{h.results.oos.n}</TD>
              <TD className="text-right"><Rv v={h.results.oos.total_r} digits={1} /></TD>
              <TD className="text-right">{h.results.oos.max_drawdown_r?.toFixed(1) ?? "—"}R</TD>
              <TD className="text-right">{h.bootstrap.p_improvement?.toFixed(2) ?? "—"}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-semibold">Walk-forward (fit on the past only, then test the next fold)</div>
          {Object.entries(s.walk_forward).map(([k, folds]) => (
            <div key={k} className="mb-2 text-xs">
              <span className="font-medium">{k}</span>: {folds.map((f) => <span key={f.fold} className="mr-2">fold {f.fold} <Rv v={f.baseline.avg_r} /> → <Rv v={f.variant.avg_r} /></span>)}
            </div>
          ))}
        </div>
        <div>
          <div className="mb-1 text-xs font-semibold">Machine-learning quality filter <VerdictBadge v={s.ml.verdict} /></div>
          {s.ml.folds.map((f) => (
            <div key={f.fold} className="text-xs">fold {f.fold}: {f.enabled ? <>Brier {f.brier_model?.toFixed(4)} vs climatology {f.brier_climatology?.toFixed(4)}, AUC {f.auc?.toFixed(3) ?? "—"}; filtered <Rv v={f.filtered?.avg_r} /> vs <Rv v={f.baseline?.avg_r} /></> : f.reason}</div>
          ))}
          <div className="mt-1 text-xs text-muted-foreground">{s.ml.note}</div>
        </div>
      </div>
      <div className="mt-4">
        <div className="mb-1 text-xs font-semibold">Counterfactual comparison on the same history (most recent baseline trades; simulations, not trades)</div>
        {tq.data ? (
          <div tabIndex={0} className="max-h-80 overflow-auto">
            <Table>
              <THead><TR><TH>Time (SL)</TH><TH>Signal</TH><TH>Period</TH><TH className="text-right">Score</TH><TH>Entry</TH><TH>Regime</TH><TH className="text-right">Original net R</TH><TH>Proposed</TH></TR></THead>
              <TBody>
                {tq.data.map((t) => (
                  <TR key={t.id}>
                    <TD><TimeText iso={t.time} text={slTime(t.time, true)} /></TD>
                    <TD>{t.symbol.replace("USDT", "")} {t.timeframe} <DirectionBadge direction={t.direction} /> {t.strategy}</TD>
                    <TD>{t.period}</TD><TD className="text-right">{t.score}</TD><TD>{t.entry_class}</TD><TD>{t.regime}</TD>
                    <TD className="text-right"><Rv v={t.net_r} /></TD>
                    <TD>{t.proposed_takes == null ? "—" : t.proposed_takes ? <Badge variant="bull">takes</Badge> : <Badge variant="outline">skips</Badge>}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        ) : <Empty>Loading…</Empty>}
        <p className="mt-1 text-xs text-muted-foreground">&ldquo;Takes&rdquo; uses the proposed filters on the original entry; the proposed stop changes each trade&apos;s result, which is reflected in the combination table above, not in this column.</p>
      </div>
    </Section>
  );
}

export function StudySections({ s }: { s: StudyResponse }) {
  const a = s.analyses;
  const sc = a.score_calibration;
  const stops = Object.entries(a.stops).filter(([k]) => k !== "atr_multiplier_sensitivity") as [string, StopStats][];
  const h3 = s.hypotheses.filter((h) => h.key.startsWith("H3"));
  return (
    <>
      <Section id="failures" title="C · Historical failure categories"
        description={`How often each measured condition appears among losing vs winning baseline trades (${s.meta.filled_trades} filled trades, all periods). A condition only matters if trades with it perform worse than trades without it.`}>
        <Table>
          <THead><TR><TH>Condition (known at decision time)</TH><TH className="text-right">% of losers</TH><TH className="text-right">% of winners</TH><TH className="text-right">Avg R with</TH><TH className="text-right">Avg R without</TH><TH className="text-right">n with</TH></TR></THead>
          <TBody>
            {a.failure_categories.map((f) => (
              <TR key={f.category}>
                <TD>{f.category}</TD><TD className="text-right">{f.share_of_losers_pct}%</TD><TD className="text-right">{f.share_of_winners_pct}%</TD>
                <TD className="text-right"><Rv v={f.with_flag.avg_r} /></TD><TD className="text-right"><Rv v={f.without_flag.avg_r} /></TD><TD className="text-right">{f.with_flag.n}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </Section>

      <Section id="score" title="D · Win rate versus signal score"
        description={`Spearman rank correlation of score with net R: ${sc.spearman_score_vs_net_r?.toFixed(3) ?? "—"}. The score is a rule-agreement count, not a probability. Its relationship with outcomes reversed between the development period and later data, so it is flagged as UNVALIDATED.`}>
        <div className="grid gap-4 lg:grid-cols-2">
          <GroupTable title="All periods" groups={sc.bands_all} label="Score band" />
          <GroupTable title="Out-of-sample only" groups={sc.bands_oos} label="Score band" />
        </div>
        <div className="mt-3 text-xs font-medium text-muted-foreground">Score components (trades above vs at/below the component&apos;s median)</div>
        <Table>
          <THead><TR><TH>Component</TH><TH className="text-right">Above median avg R (n)</TH><TH className="text-right">At/below median avg R (n)</TH></TR></THead>
          <TBody>
            {Object.entries(sc.components).map(([k, v]) => (
              <TR key={k}><TD>{k}</TD><TD className="text-right"><Rv v={v.above_median.avg_r} /> ({v.above_median.n})</TD><TD className="text-right"><Rv v={v.at_or_below_median.avg_r} /> ({v.at_or_below_median.n})</TD></TR>
            ))}
          </TBody>
        </Table>
      </Section>

      <Section id="regime" title="E · Market regime performance" description="Baseline trades grouped by the regime at the decision close (causal classifier).">
        <div className="grid gap-4 lg:grid-cols-2">
          <GroupTable title="Signal-timeframe regime" groups={a.regimes.by_regime} label="Regime" />
          <div className="space-y-3">
            <GroupTable title="Regime alignment with the trade" groups={a.regimes.by_alignment} label="Alignment" />
            <GroupTable title="4h regime alignment" groups={a.regimes.by_htf4h_alignment} label="4h" />
            <GroupTable title="BTC regime alignment (alts)" groups={a.regimes.by_btc_alignment} label="BTC" />
          </div>
        </div>
      </Section>

      <Section id="strategies" title="F · Strategy comparison" description="Production strategies and research-only families (shadow, never alerted), each evaluated independently with the same cost model.">
        <Table><PeriodHead first="Strategy" /><TBody>
          {Object.entries(a.strategies).map(([k, rep]) => (
            <PeriodRow key={k} label={<>{k} {["breakout", "pullback"].includes(k) ? <Badge variant="info">production</Badge> : <Badge variant="outline">research</Badge>}</>} rep={rep} />
          ))}
        </TBody></Table>
      </Section>

      <Section id="entry" title="G · Entry timing analysis" description="Does entering late (far from the short-term mean) hurt? Measured at the decision close, in the trade direction.">
        <div className="grid gap-4 lg:grid-cols-2">
          <GroupTable title="Entry class" groups={a.entry_timing.by_class} label="Class" />
          <GroupTable title="Extension beyond EMA-fast (ATR)" groups={a.entry_timing.by_extension} label="Extension" />
          <GroupTable title="Distance to opposing swing (ATR)" groups={a.entry_timing.by_swing_room} label="Distance" />
          <GroupTable title="Consecutive closes in the trade direction" groups={a.entry_timing.by_run} label="Run" />
        </div>
      </Section>

      <Section id="stops" title="H · Stop-loss analysis" description="Same entries, different stop placement; position size always rescaled so each trade risks 1R. 'Noise stop-outs' = stopped trades whose target was reached later anyway.">
        <Table>
          <THead><TR><TH>Method</TH><TH className="text-right">Trades</TH><TH className="text-right">Stop hit</TH><TH className="text-right">Avg stop</TH><TH className="text-right">MFE / MAE</TH><TH className="text-right">Cost per trade</TH><TH className="text-right">Noise stop-outs</TH><TH className="text-right">Gross avg</TH><TH className="text-right">Net avg</TH><TH className="text-right">Net OOS</TH></TR></THead>
          <TBody>
            {stops.map(([k, v]) => (
              <TR key={k}>
                <TD>{k}</TD><TD className="text-right">{v.trades}</TD><TD className="text-right">{v.stop_hit_pct}%</TD><TD className="text-right">{v.avg_stop_atr} ATR</TD>
                <TD className="text-right">{v.avg_mfe_r} / {v.avg_mae_r}R</TD><TD className="text-right">{v.avg_cost_r}R</TD><TD className="text-right">{v.noise_stopouts_pct}%</TD>
                <TD className="text-right"><Rv v={v.gross.avg_r} digits={3} /></TD><TD className="text-right"><Rv v={v.net.avg_r} digits={3} /></TD><TD className="text-right"><Rv v={v.net_oos.avg_r} digits={3} /></TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {a.stops.atr_multiplier_sensitivity && (
          <>
            <div className="mt-3 text-xs font-medium text-muted-foreground">ATR multiplier sensitivity (2R target)</div>
            <Table>
              <THead><TR><TH>Multiplier</TH><TH className="text-right">Dev</TH><TH className="text-right">Validation</TH><TH className="text-right">Test</TH><TH className="text-right">Avg cost</TH><TH className="text-right">Stop hit</TH></TR></THead>
              <TBody>
                {Object.entries(a.stops.atr_multiplier_sensitivity).map(([m, v]) => (
                  <TR key={m}><TD>{m} × ATR</TD><TD className="text-right"><Rv v={v.dev} digits={3} /></TD><TD className="text-right"><Rv v={v.val} digits={3} /></TD><TD className="text-right"><Rv v={v.test} digits={3} /></TD><TD className="text-right">{v.avg_cost_r}R</TD><TD className="text-right">{v.stop_hit_pct}%</TD></TR>
                ))}
              </TBody>
            </Table>
          </>
        )}
      </Section>

      <Section id="correlation" title="I · Portfolio correlation analysis"
        description={`${a.correlation.same_hour_same_direction_share_pct}% of baseline trades had another coin signal the same direction within ±60 minutes. Correlations are rolling 7-day hourly-return correlations, recomputed at each decision.`}>
        <div className="grid gap-4 lg:grid-cols-2">
          <GroupTable title="Same-direction signals on other coins in the previous 60 min" groups={a.correlation.by_cluster_prior} label="Prior signals" />
          <div className="text-xs">
            <div className="mb-1 font-medium text-muted-foreground">Portfolio limits (default) on the baseline: {a.correlation.portfolio_accepted} accepted; rejections</div>
            {Object.entries(a.correlation.portfolio_rejections).map(([k, v]) => <KV key={k} k={k.replaceAll("_", " ")} v={v} />)}
            {h3.map((h) => (
              <div key={h.key} className="mt-2"><span className="font-medium">{h.key} {h.title}</span> <VerdictBadge v={h.verdict} />
                <div className="text-muted-foreground">{h.notes.join(" · ")}</div></div>
            ))}
          </div>
        </div>
      </Section>

      <Improvements s={s} />
    </>
  );
}

