"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { fmtPrice } from "@/lib/format";
import type { DBias, DContext, DStudyRow, DSummary } from "@/lib/strategy-d-types";

const BIAS_LABEL: Record<DBias, string> = { BULLISH: "Bullish", BEARISH: "Bearish", SIDEWAYS: "Sideways", CONFLICTED: "Conflicted", INSUFFICIENT_DATA: "No data" };
const BIAS_CLASS: Partial<Record<DBias, string>> = { BULLISH: "text-bull", BEARISH: "text-bear", CONFLICTED: "text-warn" };

export const fmtR = (v: number | null | undefined, digits = 2) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(digits)}R`);
export const fmtPct = (v: number | null | undefined, digits = 0) => (v == null ? "—" : `${v.toFixed(digits)}%`);

export function BiasText({ bias }: { bias: DBias | undefined }) {
  return bias ? <span className={BIAS_CLASS[bias]}>{BIAS_LABEL[bias]}</span> : <span className="text-muted-foreground">—</span>;
}

export function DecisionBadge({ decision }: { decision: string }) {
  const label = decision === "NO_TRADE" ? "No trade" : decision.charAt(0) + decision.slice(1).toLowerCase();
  return <Badge variant={decision === "QUALIFIED" ? "default" : "outline"}>{label}</Badge>;
}

/** Compact 1D, 4H and 1H context of one 15m candle, as known when that candle closed. */
export function HtfSummary({ symbol, at, direction }: { symbol: string; at?: string; direction?: "LONG" | "SHORT" }) {
  const q = useQuery<DContext>({ queryKey: ["strategy-d-context", symbol, at, direction], queryFn: () => api.strategyDContext(symbol, at, direction), staleTime: 60_000, retry: false });
  const c = q.data;
  if (q.error) return null;
  if (!c) return <div className="rounded-md border p-3 text-xs text-muted-foreground" data-testid="htf-summary">Loading higher-timeframe context…</div>;
  if (!c.available) return <div className="rounded-md border p-3 text-xs text-muted-foreground" data-testid="htf-summary">{c.reason}</div>;
  const d = c.d_decisions?.find((x) => !direction || x.direction === direction);
  return (
    <div className="rounded-md border p-3 text-xs" data-testid="htf-summary">
      <div className="mb-2 font-semibold">Higher-timeframe context</div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {(["1d", "4h", "1h", "15m"] as const).map((tf) => (
          <div key={tf} title={c.states?.[tf]?.why}>
            <div className="text-muted-foreground">{tf === "15m" ? "15m setup" : `${tf.toUpperCase()} bias`}</div>
            <BiasText bias={c.states?.[tf]?.bias} />{c.states?.[tf]?.pullback ? <span className="text-muted-foreground"> (pullback)</span> : null}
          </div>
        ))}
      </div>
      {c.conflicts && c.conflicts.length > 0 && <div className="mt-2"><span className="text-muted-foreground">Conflicts: </span>{c.conflicts.join("; ")}</div>}
      <div className="mt-2 grid gap-1 sm:grid-cols-2">
        {(["1h", "4h"] as const).map((tf) => (
          <div key={tf}><span className="text-muted-foreground">{tf.toUpperCase()} support / resistance: </span>
            <span className="tabular">{c.levels?.[tf]?.support != null ? fmtPrice(c.levels[tf].support) : "—"} / {c.levels?.[tf]?.resistance != null ? fmtPrice(c.levels[tf].resistance) : "—"}</span></div>
        ))}
      </div>
      {c.alignment && <div className="mt-2"><span className="text-muted-foreground">Alignment: </span>{c.alignment.score.toFixed(0)} of {c.alignment.out_of.toFixed(0)}. {c.alignment.note}</div>}
      <div className="mt-2"><span className="text-muted-foreground">Strategy D decision: </span>
        {d ? <><DecisionBadge decision={d.decision} /> {d.reason}</> : "None recorded for this candle (research shadow)."}</div>
    </div>
  );
}

/** What D currently makes of each coin. Comes from the last closed 15m candle. */
export function StrategyDLatestCard({ className }: { className?: string }) {
  const q = useQuery({ queryKey: ["strategy-d-overview"], queryFn: api.strategyDOverview, refetchInterval: 60_000 });
  const d = q.data;
  if (!d) return null;
  return (
    <Card className={className} data-testid="strategy-d-latest">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">15m decisions <Badge variant="outline">Research shadow</Badge> <Badge variant="outline">{d.evidence}</Badge></CardTitle>
        <CardDescription>Each coin&apos;s last closed 15m candle, judged against 1D, 4H and 1H by Strategy D. No orders and no Telegram alerts. In the historical replay this version lost money after costs and did worse than Strategy C, so don&apos;t treat these as trade recommendations. <Link className="underline" href="/strategy-d">Full results</Link></CardDescription>
      </CardHeader>
      <CardContent>
        {d.latest.length === 0 ? <p className="text-sm text-muted-foreground">Waiting for the next 15m candle to close.</p> : (
          <Table>
            <THead><TR><TH>Symbol</TH><TH>1D</TH><TH>4H</TH><TH>1H</TH><TH>15m</TH><TH>Decision</TH><TH>Why</TH></TR></THead>
            <TBody>
              {d.latest.map((r) => (
                <TR key={r.symbol}>
                  <TD><Link className="font-medium hover:underline" href={`/analysis/${r.symbol}?tf=15m`}>{r.symbol}</Link></TD>
                  {(["1d", "4h", "1h", "15m"] as const).map((tf) => <TD key={tf}><BiasText bias={r.biases[tf]} /></TD>)}
                  <TD><DecisionBadge decision={r.decision} />{r.direction ? ` ${r.direction}` : ""}</TD>
                  <TD className="text-xs text-muted-foreground">{r.reason}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

const summaryOf = (r: DStudyRow | undefined): DSummary => r?.summary ?? {};

/** The same metrics side by side. Used for the historical study only. */
export function StudyTable({ rows }: { rows: { key: string; label: string; row: DStudyRow | undefined; strong?: boolean }[] }) {
  return (
    <Table>
      <THead><TR><TH>Rule</TH><TH>Signals</TH><TH>Filled</TH><TH>Per week</TH><TH>Win rate</TH><TH>Avg net</TH><TH>95% range</TH><TH>Profit factor</TH><TH>LONG</TH><TH>SHORT</TH><TH>Portfolio return</TH><TH>Max drawdown</TH></TR></THead>
      <TBody>
        {rows.map(({ key, label, row, strong }) => {
          const s = summaryOf(row);
          return (
            <TR key={key} className={strong ? "font-semibold" : undefined}>
              <TD>{label}</TD>
              <TD>{row?.signals ?? "—"}</TD>
              <TD>{row?.filled ?? row?.n ?? "—"}</TD>
              <TD>{row?.trades_per_week ?? "—"}</TD>
              <TD>{fmtPct(s.win_rate_pct)}</TD>
              <TD>{fmtR(s.avg_r, 3)}</TD>
              <TD className="whitespace-nowrap">{row?.avg_net_r_ci95 ? `${row.avg_net_r_ci95[0].toFixed(2)} to ${row.avg_net_r_ci95[1].toFixed(2)}` : "—"}</TD>
              <TD>{s.profit_factor != null ? s.profit_factor.toFixed(2) : "—"}</TD>
              <TD>{fmtR(row?.groups?.by_direction?.LONG?.avg_r, 3)}</TD>
              <TD>{fmtR(row?.groups?.by_direction?.SHORT?.avg_r, 3)}</TD>
              <TD>{row?.portfolio ? `${row.portfolio.return_pct.toFixed(1)}%` : "—"}</TD>
              <TD>{row?.portfolio ? `${row.portfolio.max_drawdown_pct.toFixed(1)}%` : "—"}</TD>
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}
