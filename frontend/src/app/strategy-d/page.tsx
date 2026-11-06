"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { PageHeader } from "@/components/app-shell";
import { Empty, ErrorBox } from "@/components/status";
import { BiasText, DecisionBadge, DrawdownReset, fmtPct, fmtR, HtfSummary, SegmentsTable, StrategyDLatestCard, StudyTable } from "@/components/strategy-d";
import { Time } from "@/components/time";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/form";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { fmtPrice } from "@/lib/format";
import type { DDecision } from "@/lib/strategy-d-types";

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return <div><div className="text-xs text-muted-foreground">{label}</div><div className="tabular text-lg font-semibold">{value}</div>{sub && <div className="text-xs text-muted-foreground">{sub}</div>}</div>;
}

function DecisionDetail({ d }: { d: DDecision }) {
  const p = d.data.plan;
  return (
    <div className="space-y-3 p-2 text-xs">
      <p>{d.reason}</p>
      {p && (
        <div className="grid gap-2 sm:grid-cols-3">
          <div><span className="text-muted-foreground">Entry reference: </span>{fmtPrice(p.entry_reference)}</div>
          <div><span className="text-muted-foreground">Stop-loss: </span>{fmtPrice(p.stop_loss)} ({p.stop_move_pct.toFixed(2)}%)</div>
          {p.targets.map((t) => <div key={t.label}><span className="text-muted-foreground">{t.label} ({t.r}R, {Math.round(t.weight * 100)}%): </span>{fmtPrice(t.price)} ({t.move_pct.toFixed(2)}%)</div>)}
          <div className="sm:col-span-3 text-muted-foreground">Plan if every target is reached: {fmtR(p.gross_r_all_targets)} gross, {fmtR(p.net_r_all_targets)} after costs. At the stop: {fmtR(p.net_r_at_stop)}. {p.plan_note}</div>
        </div>
      )}
      <div>
        <span className="text-muted-foreground">Alignment {d.data.confluence.score.toFixed(0)} of {d.data.confluence.out_of.toFixed(0)}: </span>
        {d.data.confluence.parts.map((x) => `${x.timeframe} ${x.points.toFixed(0)}/${x.weight.toFixed(0)}`).join(" · ")}. {d.data.confluence.note}
      </div>
      {d.risk_amount != null && <div><span className="text-muted-foreground">Paper risk: </span>{d.risk_amount.toFixed(2)} USDT, quantity {d.quantity}</div>}
      <HtfSummary symbol={d.symbol} at={d.signal_time} direction={d.direction} />
    </div>
  );
}

export default function StrategyDPage() {
  const [decision, setDecision] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const o = useQuery({ queryKey: ["strategy-d-overview"], queryFn: api.strategyDOverview, refetchInterval: 60_000 });
  const list = useQuery({ queryKey: ["strategy-d-decisions", decision], queryFn: () => api.strategyDDecisions(decision || undefined), refetchInterval: 60_000 });
  const study = useQuery({ queryKey: ["strategy-d-study"], queryFn: api.strategyDStudy, retry: false });
  const forward = useQuery({ queryKey: ["validation-overview"], queryFn: api.validationOverview });
  const d = o.data;
  const c = forward.data?.variants.C;
  const cs = c?.strategy.summary as { n?: number; avg_r?: number | null; win_rate_pct?: number | null; total_r?: number | null } | undefined;
  const s = study.data;
  return (
    <>
      <PageHeader title="Strategy D: 15M Multi-Timeframe Confluence"
        description="Research and shadow mode. 15m setups judged against 1D, 4H and 1H. No orders, not part of the locked strategies A, B and C, and never promoted automatically." />
      <ErrorBox error={o.error} />
      {d && (
        <Card className="mb-4" data-testid="strategy-d-forward">
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center gap-2">Forward shadow results <Badge>{d.evidence}</Badge></CardTitle>
            <CardDescription>{d.label} Version {d.version}, evaluation segment {d.segment}. Results below cover this segment only. Telegram for D: {d.telegram_enabled ? "on" : "off"}.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
              <Stat label="Closed trades" value={`${d.closed} of ${d.closed_needed}`} sub={d.enough_data ? "enough to review" : "not enough to judge"} />
              <Stat label="Win rate" value={fmtPct(d.win_rate_pct)} />
              <Stat label="Avg net" value={fmtR(d.avg_net_r)} />
              <Stat label="Paper equity" value={d.account.equity.toLocaleString()} sub={`Drawdown ${d.account.drawdown_pct.toFixed(1)}%`} />
              <Stat label="Open" value={d.open} sub={`Risk ${fmtPct(d.account.open_risk_pct, 1)}`} />
            </div>
            <p className="mt-3 text-xs text-muted-foreground">Since the last backend start: {d.status.candles_checked} closed 15m candles checked, {d.status.setups_seen} setups seen.{d.status.last_error && <span className="text-bear"> Last error: {d.status.last_error}</span>}</p>
            {d.notes.map((n) => <p key={n} className="mt-1 text-xs text-muted-foreground">{n}</p>)}
            <DrawdownReset overview={d} />
            <SegmentsTable overview={d} />
          </CardContent>
        </Card>
      )}
      <StrategyDLatestCard className="mb-4" />
      <Card className="mb-4" data-testid="c-vs-d">
        <CardHeader>
          <CardTitle>Strategy C and Strategy D</CardTitle>
          <CardDescription>Forward paper results and historical replays are different evidence and are never merged. C&apos;s forward account runs on every scanned timeframe with a single 2R exit.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <h3 className="mb-1 text-sm font-semibold">Forward, new data only</h3>
            <Table>
              <THead><TR><TH>Strategy</TH><TH>Closed</TH><TH>Win rate</TH><TH>Avg net</TH><TH>Total net</TH></TR></THead>
              <TBody>
                <TR><TD>C (locked)</TD><TD>{cs?.n ?? "—"}</TD><TD>{fmtPct(cs?.win_rate_pct)}</TD><TD>{fmtR(cs?.avg_r)}</TD><TD>{fmtR(cs?.total_r)}</TD></TR>
                <TR><TD>D (shadow{d && d.segment > 1 ? `, segment ${d.segment} only` : ""})</TD><TD>{d?.closed ?? "—"}</TD><TD>{fmtPct(d?.win_rate_pct)}</TD><TD>{fmtR(d?.avg_net_r)}</TD><TD>{fmtR(d?.total_net_r)}</TD></TR>
              </TBody>
            </Table>
          </div>
          <div>
            <h3 className="mb-1 flex flex-wrap items-center gap-2 text-sm font-semibold">Historical replay, hypothetical {s && <Badge variant="outline">{s.meta.evidence}</Badge>}</h3>
            {s ? (
              <>
                <p className="mb-2 text-xs text-muted-foreground">{s.meta.label} {s.meta.start.slice(0, 10)} to {s.meta.end.slice(0, 10)}. Same costs, execution and risk limits for every row. {s.meta.configurations_tested} configurations tested.</p>
                <StudyTable rows={[
                  { key: "d", label: "Strategy D default", row: s.strategy_d, strong: true },
                  { key: "c15", label: "Strategy C rules, 15m only", row: s.baselines.C_15m },
                  { key: "call", label: "Strategy C rules, all timeframes", row: s.baselines.C_all },
                  { key: "a15", label: "Strategy A rules, 15m only", row: s.baselines.A_15m },
                  ...Object.entries(s.rules).filter(([k]) => k !== "d_default").map(([k, v]) => ({ key: k, label: v.label ?? k, row: v })),
                ]} />
              </>
            ) : <Empty>{study.error ? "The historical study hasn't been run yet." : "Loading the historical study…"}</Empty>}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><CardTitle>Recorded decisions</CardTitle><CardDescription>Only candles with a confirmed 15m setup are recorded. Every other candle is no trade.</CardDescription></div>
            <NativeSelect value={decision} onChange={(e) => setDecision(e.target.value)} aria-label="Decision filter" className="h-8 w-40">
              <option value="">All decisions</option><option value="QUALIFIED">Qualified</option><option value="WATCHLIST">Watchlist</option><option value="REJECTED">Rejected</option>
            </NativeSelect>
          </div>
        </CardHeader>
        <CardContent>
          {list.data?.length === 0 ? <Empty>No decisions recorded yet.</Empty> : (
            <Table>
              <THead><TR><TH>15m candle</TH><TH>Symbol</TH><TH>Side</TH><TH>Entry type</TH><TH>1D</TH><TH>4H</TH><TH>1H</TH><TH>Score</TH><TH>Decision</TH><TH>Result</TH></TR></THead>
              <TBody>
                {list.data?.flatMap((r) => [
                  <TR key={r.id} className="cursor-pointer" onClick={() => setOpen(open === r.id ? null : r.id)}>
                    <TD><Time iso={r.signal_time} /></TD><TD>{r.symbol}</TD><TD>{r.direction}</TD><TD>{r.data.entry_label}</TD>
                    {(["1d", "4h", "1h"] as const).map((tf) => <TD key={tf}><BiasText bias={r.data.states[tf]?.bias} /></TD>)}
                    <TD>{r.score.toFixed(0)}</TD><TD><DecisionBadge decision={r.decision} /></TD>
                    <TD>{r.net_r != null ? fmtR(r.net_r) : r.taken ? r.status.toLowerCase() : "not taken"}</TD>
                  </TR>,
                  ...(open === r.id ? [<TR key={`${r.id}-detail`}><TD colSpan={10}><DecisionDetail d={r} /></TD></TR>] : []),
                ])}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
