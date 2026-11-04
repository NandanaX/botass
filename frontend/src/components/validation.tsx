"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { EquityChart } from "@/components/price-chart";
import { DirectionBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/form";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { CHART_TZ } from "@/lib/format";
import type { HistoricalRobustness, StatBlock, ValidationOverview, VariantForward } from "@/lib/validation-types";

const KEYS = ["A", "B", "C"] as const;
const COLORS: Record<string, string> = { A: "#8a93a3", B: "#3b82f6", C: "#16c784" };

export const slTime = (iso: string | null | undefined) =>
  iso ? new Intl.DateTimeFormat("en-GB", { timeZone: CHART_TZ, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(iso)) : "—";

function Rv({ v, d = 3 }: { v: number | null | undefined; d?: number }) {
  if (v === null || v === undefined || !Number.isFinite(v)) return <span className="text-muted-foreground">—</span>;
  return <span className={v > 0 ? "text-bull" : v < 0 ? "text-bear" : ""}>{v > 0 ? "+" : ""}{v.toFixed(d)}R</span>;
}

const ci = (x: [number, number] | null | undefined, d = 3) => (x ? `${x[0].toFixed(d)} … ${x[1].toFixed(d)}` : "—");

/** Minimal SVG line chart for sensitivity curves (x numeric, y in R). */
export function LineChart({ series, xLabel, height = 180 }: { series: { name: string; color: string; points: { x: number; y: number | null }[] }[]; xLabel: string; height?: number }) {
  const pts = series.flatMap((s) => s.points.filter((p) => p.y != null)) as { x: number; y: number }[];
  if (!pts.length) return null;
  const W = 420, H = height, P = 32;
  const xs = pts.map((p) => p.x), ys = [...pts.map((p) => p.y), 0];
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const sx = (x: number) => P + ((x - x0) / (x1 - x0 || 1)) * (W - 2 * P);
  const sy = (y: number) => H - P + -((y - y0) / (y1 - y0 || 1)) * (H - 2 * P);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[520px]" role="img" aria-label={`chart by ${xLabel}`}>
      <line x1={P} x2={W - P} y1={sy(0)} y2={sy(0)} stroke="#8a93a3" strokeDasharray="3 3" strokeWidth={1} />
      <text x={W - P} y={sy(0) - 4} fontSize={9} fill="#8a93a3" textAnchor="end">0R</text>
      {series.map((s) => {
        const p = s.points.filter((q) => q.y != null) as { x: number; y: number }[];
        return (
          <g key={s.name}>
            <polyline fill="none" stroke={s.color} strokeWidth={2} points={p.map((q) => `${sx(q.x)},${sy(q.y)}`).join(" ")} />
            {p.map((q) => <circle key={q.x} cx={sx(q.x)} cy={sy(q.y)} r={2.5} fill={s.color}><title>{`${s.name} ${q.x}: ${q.y.toFixed(3)}R`}</title></circle>)}
          </g>
        );
      })}
      {[...new Set(xs)].map((x) => <text key={x} x={sx(x)} y={H - 10} fontSize={9} fill="#8a93a3" textAnchor="middle">{x}</text>)}
      <text x={P} y={12} fontSize={9} fill="#8a93a3">{y1.toFixed(2)}R</text>
      <text x={P} y={H - P + 12} fontSize={9} fill="#8a93a3">{y0.toFixed(2)}R</text>
      <text x={W / 2} y={H} fontSize={9} fill="#8a93a3" textAnchor="middle">{xLabel}</text>
      {series.map((s, k) => <text key={s.name} x={W - P} y={14 + 11 * k} fontSize={9} fill={s.color} textAnchor="end">{s.name}</text>)}
    </svg>
  );
}

function Zone({ kind, title, description, children }: { kind: "forward" | "historical" | "gate"; title: string; description: string; children: React.ReactNode }) {
  const cls = kind === "forward" ? "border-primary/50" : kind === "historical" ? "border-warn/50" : "border-bull/40";
  const tag = kind === "forward" ? <Badge variant="info">FORWARD · live paper</Badge> : kind === "historical" ? <Badge variant="warn">HISTORICAL · re-simulation</Badge> : <Badge variant="outline">GATE · manual review</Badge>;
  return (
    <Card className={`mb-4 border-2 ${cls}`}>
      <CardHeader><CardTitle className="flex items-center gap-2">{tag} {title}</CardTitle><CardDescription>{description}</CardDescription></CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function StatRow({ label, b }: { label: string; b: StatBlock }) {
  const s = b.summary, bt = b.bootstrap;
  return (
    <TR>
      <TD>{label}</TD>
      <TD className="text-right">{s.n ?? 0}</TD>
      <TD className="text-right">{bt.n_clusters ?? 0}</TD>
      <TD className="text-right"><Rv v={s.avg_r} /></TD>
      <TD className="text-right text-muted-foreground">{ci(bt.avg_net_r_ci95)}</TD>
      <TD className="text-right">{bt.prob_avg_positive != null ? `${(100 * bt.prob_avg_positive).toFixed(0)}%` : "—"}</TD>
      <TD className="text-right"><Rv v={s.total_r} d={2} /></TD>
      <TD className="text-right">{s.win_rate_pct != null ? `${s.win_rate_pct.toFixed(1)}%` : "—"}</TD>
      <TD className="text-right">{s.profit_factor?.toFixed(2) ?? "—"}</TD>
      <TD className="text-right">{s.max_drawdown_r != null ? `${s.max_drawdown_r.toFixed(1)}R` : "—"}</TD>
    </TR>
  );
}

const STATHEAD = <THead><TR><TH>Variant</TH><TH className="text-right">Closed</TH><TH className="text-right">Clusters</TH><TH className="text-right">Avg net R</TH><TH className="text-right">95% CI (cluster bootstrap)</TH><TH className="text-right">P(avg &gt; 0)</TH><TH className="text-right">Net R</TH><TH className="text-right">Win rate</TH><TH className="text-right">PF</TH><TH className="text-right">Max DD</TH></TR></THead>;

export function ForwardSection({ o }: { o: ValidationOverview }) {
  const v = o.variants;
  const start = v.A?.variant.period_start;
  const days = v.A?.elapsed_days ?? 0;
  const closed = KEYS.map((k) => v[k]?.strategy.summary.n ?? 0);
  return (
    <Zone kind="forward" title="Forward paper-trading results"
      description={`Only signals detected live after ${slTime(start)} (Sri Lanka). Entries fill at the live bid/ask at detection; exits trigger on the closing side of the book; fees, slippage and funding are charged. Elapsed ${days.toFixed(1)} days; completed trades A/B/C: ${closed.join(" / ")}. An incomplete forward test is not a validation.`}>
      {Math.max(...closed) === 0 && <div className="mb-3 rounded-md border border-warn/40 bg-warn/10 p-3 text-sm">Forward observation has just started: no completed paper trades yet. Results will appear as live signals are detected and resolved. Nothing here is a validation yet.</div>}
      <div className="mb-1 text-xs font-semibold">Signal level (every filled paper trade of the variant)</div>
      <Table>{STATHEAD}<TBody>{KEYS.map((k) => v[k] && <StatRow key={k} label={`${k} · ${v[k].variant.name}`} b={v[k].strategy} />)}</TBody></Table>
      <div className="mb-1 mt-3 text-xs font-semibold">Portfolio level (trades the paper account took under the risk limits)</div>
      <Table>{STATHEAD}<TBody>{KEYS.map((k) => v[k] && <StatRow key={k} label={`${k} · ${v[k].variant.name}`} b={v[k].portfolio} />)}</TBody></Table>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        {KEYS.map((k) => v[k] && <VariantCard key={k} k={k} r={v[k]} />)}
      </div>
      <Decisions o={o} />
    </Zone>
  );
}

function VariantCard({ k, r }: { k: string; r: VariantForward }) {
  const ex = r.risk.exposure;
  const c = r.costs;
  const months = Object.entries(r.strategy.consistency?.months ?? {});
  const weeks = Object.entries(r.strategy.consistency?.weeks ?? {}).slice(-8);
  return (
    <div className="rounded-md border p-3 text-xs">
      <div className="mb-2 flex items-center justify-between"><span className="text-sm font-semibold" style={{ color: COLORS[k] }}>{k} · {r.variant.name}</span><span className="text-muted-foreground" title={r.variant.id}>v {r.variant.version.slice(-10)}</span></div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
        <span className="text-muted-foreground">Accepted / rejected / missed</span><span className="text-right tabular">{r.counts.decisions.ACCEPTED ?? 0} / {r.counts.decisions.REJECTED ?? 0} / {r.counts.decisions.MISSED ?? 0}</span>
        <span className="text-muted-foreground">Open / closed / unfilled</span><span className="text-right tabular">{r.counts.status.OPEN ?? 0} / {r.counts.status.CLOSED ?? 0} / {r.counts.status.UNFILLED ?? 0}</span>
        <span className="text-muted-foreground">Paper equity · drawdown</span><span className="text-right tabular">${ex.equity.toLocaleString("en-US")} · {ex.drawdown_pct.toFixed(2)}%</span>
        <span className="text-muted-foreground">Open risk · net direction</span><span className="text-right tabular">{ex.open_risk_pct ?? 0}% · ${ex.net_directional_notional.toLocaleString("en-US")}</span>
        <span className="text-muted-foreground">Fees · spread · slippage · funding</span><span className="text-right tabular">{c.n ? `${c.avg_fees_r} · ${c.avg_spread_r} · ${c.avg_slippage_r} · ${c.avg_funding_r} R` : "—"}</span>
        <span className="text-muted-foreground">Estimated (not observed) costs</span><span className="text-right tabular">{c.estimated_share != null ? `${(100 * c.estimated_share).toFixed(0)}% of trades` : "—"}</span>
        <span className="text-muted-foreground">Circuit breakers</span><span className="text-right">{Object.entries(r.risk.breakers).map(([n, b]) => <Badge key={n} variant={b.tripped ? "bear" : "outline"} className="ml-1">{n.replace("_", " ")}{b.tripped ? " TRIPPED" : ""}</Badge>)}</span>
      </div>
      {r.portfolio.equity_curve.length > 1 ? <div className="mt-2"><EquityChart points={r.portfolio.equity_curve} height={140} /></div> : <div className="mt-2 text-muted-foreground">Equity curve appears after the first closed paper trade.</div>}
      {!!months.length && <div className="mt-2"><span className="font-medium">By month: </span>{months.map(([m, x]) => <span key={m} className="mr-2">{m} <Rv v={x.total_r} d={1} /> ({x.n})</span>)}</div>}
      {!!weeks.length && <div className="mt-1"><span className="font-medium">By week: </span>{weeks.map(([w, x]) => <span key={w} className="mr-2">{w} <Rv v={x.total_r} d={1} /></span>)}</div>}
    </div>
  );
}

function Decisions({ o }: { o: ValidationOverview }) {
  const rows = KEYS.flatMap((k) => o.variants[k]?.recent ?? []).sort((a, b) => b.detected_at.localeCompare(a.detected_at)).slice(0, 60);
  return (
    <div className="mt-4">
      <div className="mb-1 text-xs font-semibold">Acceptance / rejection decisions (latest)</div>
      {!rows.length ? <div className="text-xs text-muted-foreground">No live signals since the period started.</div> : (
        <div className="max-h-96 overflow-auto">
          <Table>
            <THead><TR><TH>Detected (SL)</TH><TH>Variant</TH><TH>Signal</TH><TH>Decision</TH><TH>Status</TH><TH>Portfolio</TH><TH className="text-right">Fill</TH><TH className="text-right">Net R</TH><TH>Reason / costs</TH></TR></THead>
            <TBody>
              {rows.map((t) => (
                <TR key={t.id}>
                  <TD>{slTime(t.detected_at)}</TD><TD style={{ color: COLORS[t.variant_key] }}>{t.variant_key}</TD>
                  <TD>{t.symbol.replace("USDT", "")} {t.timeframe} <DirectionBadge direction={t.direction} /></TD>
                  <TD><Badge variant={t.decision === "ACCEPTED" ? "bull" : t.decision === "REJECTED" ? "bear" : "warn"}>{t.decision}</Badge></TD>
                  <TD>{t.status}{t.exit_reason ? ` · ${t.exit_reason}` : ""}</TD>
                  <TD>{t.status === "OPEN" || t.status === "CLOSED" ? (t.portfolio_taken ? "taken" : <span className="text-warn" title={t.portfolio_reason ?? ""}>blocked</span>) : "—"}</TD>
                  <TD className="text-right tabular">{t.fill_price?.toPrecision(6) ?? "—"}</TD>
                  <TD className="text-right"><Rv v={t.net_r} d={2} /></TD>
                  <TD className="max-w-[320px] text-[11px] text-muted-foreground">{t.portfolio_taken ? t.decision_reason : (t.portfolio_reason ?? t.decision_reason)}{t.cost_flags?.entry ? ` · entry ${t.cost_flags.entry}` : ""}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}
    </div>
  );
}

export function PromotionSection({ o }: { o: ValidationOverview }) {
  return (
    <Zone kind="gate" title="Strategy promotion gate"
      description="A variant is READY FOR REVIEW only when every criterion passes on FORWARD evidence. Readiness only requests a human review: recording a decision never changes production signals or enables enforcement.">
      <div className="grid gap-4 lg:grid-cols-3">
        {KEYS.map((k) => o.variants[k] && <GateCard key={k} k={k} r={o.variants[k]} />)}
      </div>
      <div className="mt-3 text-xs text-muted-foreground">
        Last full test run: {o.tests ? `${o.tests.passed} passed, ${o.tests.failed} failed (${slTime(o.tests.finished_at)})` : "none recorded"}.
        {!!o.decisions.length && <> Review log: {o.decisions.slice(0, 5).map((d) => `${d.decision} ${d.variant_id.split("@")[0]} by ${d.reviewer} (${slTime(d.created_at)})`).join("; ")}.</>}
      </div>
    </Zone>
  );
}

function GateCard({ k, r }: { k: string; r: VariantForward }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [decision, setDecision] = useState("DEFERRED");
  const m = useMutation({ mutationFn: () => api.promotionDecision(k, { decision, note, reviewer }), onSuccess: () => qc.invalidateQueries({ queryKey: ["validation-overview"] }) });
  const p = r.promotion;
  const ready = p.readiness === "READY_FOR_REVIEW";
  return (
    <div className="rounded-md border p-3 text-xs">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold" style={{ color: COLORS[k] }}>{k} · {r.variant.name}</span>
        <Badge variant={ready ? "bull" : p.readiness === "NOT_READY" ? "bear" : "warn"}>{p.readiness.replaceAll("_", " ")}</Badge>
      </div>
      <ul className="space-y-1">
        {p.checks.map((c) => <li key={c.key}><span className={c.passed ? "text-bull" : "text-bear"}>{c.passed ? "✓" : "✗"}</span> <span className="font-medium">{c.criterion}</span><div className="pl-4 text-muted-foreground">{c.detail}</div></li>)}
      </ul>
      <div className="mt-2 space-y-1 border-t pt-2">
        <div className="font-medium">Manual review</div>
        <NativeSelect value={decision} onChange={(e) => setDecision(e.target.value)} className="h-8">
          <option value="DEFERRED">Defer (keep observing)</option>
          <option value="REJECTED">Reject</option>
          <option value="APPROVED" disabled={!ready}>Approve for production review{ready ? "" : " (gate not passed)"}</option>
        </NativeSelect>
        <Input placeholder="Reviewer" value={reviewer} onChange={(e) => setReviewer(e.target.value)} className="h-8" />
        <Input placeholder="Reason / note" value={note} onChange={(e) => setNote(e.target.value)} className="h-8" />
        <Button size="sm" variant="outline" disabled={!reviewer || note.length < 3 || m.isPending} onClick={() => m.mutate()}>Record decision</Button>
        {m.data && <div className="text-muted-foreground">{m.data.note}</div>}
        {m.error && <div className="text-bear">{String((m.error as Error).message)}</div>}
      </div>
    </div>
  );
}

export function HistoricalSection({ h }: { h: HistoricalRobustness }) {
  const v = h.variants;
  const keys = Object.keys(v);
  const mults = Object.keys(h.stability.B.avg_r_by_multiplier).map(Number);
  const stressNames = Object.keys(h.stress.A).filter((s) => !s.startsWith("_"));
  const main = ["A", "B@2.5", "C@2.5"];
  return (
    <Zone kind="historical" title="Historical robustness and stress tests"
      description={`${h.meta.label} ${h.meta.signals} production signals, ${h.meta.days} days (${h.meta.data_start.slice(0, 10)} → ${h.meta.data_end.slice(0, 10)}); ${h.meta.configurations_evaluated} configurations evaluated so far (repeated testing inflates apparent significance).`}>
      <div className="mb-1 text-xs font-semibold">Stop-multiplier comparison (same signals, fixed risk per trade, position size rescaled)</div>
      <Table>
        <THead><TR><TH>Config</TH><TH className="text-right">Trades</TH><TH className="text-right">Per day</TH><TH className="text-right">Avg net R</TH><TH className="text-right">Gross</TH><TH className="text-right">Fees</TH><TH className="text-right">PF</TH><TH className="text-right">Max DD</TH><TH className="text-right">Stop-outs</TH><TH className="text-right">Hold (bars)</TH><TH className="text-right">P(avg&gt;0)</TH><TH className="text-right">Dev / val / test (all inspected)</TH><TH className="text-right">Holm p vs A</TH></TR></THead>
        <TBody>
          {keys.map((k) => {
            const x = v[k];
            return (
              <TR key={k} className={main.includes(k) ? "bg-muted/30" : ""}>
                <TD className="font-medium">{k}</TD><TD className="text-right">{x.summary.n}</TD><TD className="text-right">{x.trades_per_day}</TD>
                <TD className="text-right"><Rv v={x.summary.avg_r} /></TD><TD className="text-right"><Rv v={x.gross_avg_r} /></TD><TD className="text-right">{x.avg_fee_r}R</TD>
                <TD className="text-right">{x.summary.profit_factor?.toFixed(2)}</TD><TD className="text-right">{x.summary.max_drawdown_r?.toFixed(0)}R</TD>
                <TD className="text-right">{x.stop_out_pct}%</TD><TD className="text-right">{x.avg_holding_bars}</TD>
                <TD className="text-right">{x.bootstrap.prob_avg_positive != null ? `${(100 * x.bootstrap.prob_avg_positive).toFixed(0)}%` : "—"}</TD>
                <TD className="text-right">{["dev", "val", "test"].map((p) => <span key={p} className="ml-1"><Rv v={x.periods[p]?.avg_r} d={2} /></span>)}</TD>
                <TD className="text-right">{h.holm_adjusted_p[k] ?? "—"}</TD>
              </TR>
            );
          })}
        </TBody>
      </Table>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-semibold">Parameter stability: average net R by stop multiplier</div>
          <LineChart xLabel="stop multiplier (× ATR)" series={[
            { name: "A (production)", color: COLORS.A, points: mults.map((m) => ({ x: m, y: v.A.summary.avg_r ?? null })) },
            { name: "B", color: COLORS.B, points: mults.map((m) => ({ x: m, y: h.stability.B.avg_r_by_multiplier[String(m)] })) },
            { name: "C (+HTF)", color: COLORS.C, points: mults.map((m) => ({ x: m, y: h.stability.C.avg_r_by_multiplier[String(m)] })) },
          ]} />
          <div className="text-xs text-muted-foreground">
            {(["B", "C"] as const).map((f) => <div key={f}>{f}: better than A in {h.stability[f].cells_better_than_A}/{h.stability[f].cells_total} symbol/timeframe/period cells; 2.0–3.0 ATR all above A: {h.stability[f].plateau_2_to_3_all_above_A ? "yes" : "no"}; all positive: {h.stability[f].plateau_2_to_3_all_positive ? "yes" : "no"}.</div>)}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-semibold">Cost sensitivity (all costs scaled together)</div>
          <LineChart xLabel="cost multiplier (1 = base: fee 0.055%/side, slippage 0.02% + 0.05% stop)" series={main.map((k) => ({
            name: k, color: COLORS[k[0]], points: h.stress[k]._cost_curve.map((p) => ({ x: p.cost_multiplier, y: p.avg_r })) }))} />
          <div className="text-xs text-muted-foreground">Break-even cost multiplier: {main.map((k) => `${k} ${h.stress[k]._break_even_cost_multiplier ?? "never profitable"}`).join(" · ")}</div>
        </div>
      </div>
      <div className="mt-4 mb-1 text-xs font-semibold">Stress tests (average net R per trade)</div>
      <Table>
        <THead><TR><TH>Scenario</TH>{main.map((k) => <TH key={k} className="text-right">{k}</TH>)}</TR></THead>
        <TBody>{stressNames.map((s) => <TR key={s}><TD>{s}</TD>{main.map((k) => <TD key={k} className="text-right"><Rv v={h.stress[k][s]?.avg_r} />{h.stress[k][s]?.p05 != null && <span className="text-muted-foreground"> (p5 {h.stress[k][s].p05!.toFixed(3)})</span>}</TD>)}</TR>)}</TBody>
      </Table>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-semibold">Drawdown distribution (every signal taken, in R)</div>
          <Table>
            <THead><TR><TH>Variant</TH><TH className="text-right">Clusters</TH><TH className="text-right">Historical</TH><TH className="text-right">Median</TH><TH className="text-right">95%</TH>{Object.keys(h.drawdown.A.prob_dd_beyond ?? {}).map((t) => <TH key={t} className="text-right">P(&gt;{t})</TH>)}</TR></THead>
            <TBody>{main.map((k) => { const d = h.drawdown[k]; return <TR key={k}><TD>{k}</TD><TD className="text-right">{d.n_clusters}</TD><TD className="text-right">{d.historical_max_dd_r?.toFixed(0)}R</TD><TD className="text-right">{d.dd_quantiles_r?.["50"]?.toFixed(0)}R</TD><TD className="text-right">{d.dd_quantiles_r?.["95"]?.toFixed(0)}R</TD>{Object.values(d.prob_dd_beyond ?? {}).map((p, j) => <TD key={j} className="text-right">{(100 * p).toFixed(0)}%</TD>)}</TR>; })}</TBody>
          </Table>
          <div className="text-[11px] text-muted-foreground">{h.drawdown.A.assumption}</div>
        </div>
        <div>
          <div className="mb-1 text-xs font-semibold">Portfolio replay with the forward risk limits (0.5% risk/trade)</div>
          <Table>
            <THead><TR><TH>Variant</TH><TH>Limits</TH><TH className="text-right">Taken</TH><TH className="text-right">Avg net R</TH><TH className="text-right">Return</TH><TH className="text-right">Max DD</TH></TR></THead>
            <TBody>
              {main.flatMap((k) => [["as configured", h.portfolio[k]], ["no drawdown breaker", h.portfolio_no_drawdown_breaker[k]]].map(([lbl, p]) => {
                const x = p as HistoricalRobustness["portfolio"][string];
                return <TR key={k + lbl}><TD>{k}</TD><TD>{lbl as string}</TD><TD className="text-right">{x.taken}</TD><TD className="text-right"><Rv v={x.avg_net_r} /></TD><TD className="text-right">{x.return_pct}%</TD><TD className="text-right">{x.max_drawdown_pct}%</TD></TR>;
              }))}
            </TBody>
          </Table>
          <div className="mt-2 text-xs">{main.map((k) => { const c = h.consistency[k]; return <div key={k}>{k}: {c.positive_months}/{c.months_total} months positive; best month = {c.best_month_share_of_positive_r != null ? `${(100 * c.best_month_share_of_positive_r).toFixed(0)}%` : "—"} of positive R; {c.positive_thirds}/3 sub-periods positive.</div>; })}</div>
        </div>
      </div>
      <div className="mt-4 mb-1 text-xs font-semibold">By timeframe and regime (A · B@2.5 · C@2.5, avg net R)</div>
      <Table>
        <THead><TR><TH>Group</TH>{main.map((k) => <TH key={k} className="text-right">{k}</TH>)}</TR></THead>
        <TBody>
          {[...Object.keys(v.A.by_timeframe).map((g) => ["by_timeframe", g]), ...Object.keys(v.A.by_regime).map((g) => ["by_regime", g]), ...Object.keys(v.A.by_symbol).map((g) => ["by_symbol", g])].map(([grp, g]) => (
            <TR key={grp + g}><TD>{g}</TD>{main.map((k) => { const x = (v[k] as unknown as Record<string, Record<string, { n: number; avg_r: number | null }>>)[grp][g]; return <TD key={k} className="text-right">{x ? <><Rv v={x.avg_r} /> <span className="text-muted-foreground">({x.n})</span></> : "—"}</TD>; })}</TR>
          ))}
        </TBody>
      </Table>
    </Zone>
  );
}
