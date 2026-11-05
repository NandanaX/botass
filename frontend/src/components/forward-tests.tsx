"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";

const r = (v: number | null) => (v === null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}R`);
const pct = (v: number | null) => (v === null ? "—" : `${v.toFixed(0)}%`);

/** Research candidates tracked on new data. Kept apart from strategies A, B and C. */
export function ForwardTestsCard() {
  const q = useQuery({ queryKey: ["forward-tests"], queryFn: api.forwardTests, refetchInterval: 60_000 });
  const d = q.data;
  if (!d) return null;
  return (
    <Card className="mt-4" data-testid="forward-tests">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">Forward paper tests <Badge>{d.evidence}</Badge></CardTitle>
        <CardDescription>{d.label}</CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <THead><TR><TH>Test</TH><TH>Signals</TH><TH>Waiting / open</TH><TH>Not filled</TH><TH>Closed</TH><TH>Win rate</TH><TH>Avg net</TH><TH>Total net</TH></TR></THead>
          <TBody>
            {d.tracks.map((t) => (
              <TR key={t.key}>
                <TD>
                  {t.label}
                  {t.vs_control && <div className="text-xs text-muted-foreground">{r(t.vs_control.avg_difference_r)} against the control on {t.vs_control.n} shared signals</div>}
                  {t.fill_rate_pct !== null && <div className="text-xs text-muted-foreground">Fill rate {pct(t.fill_rate_pct)}</div>}
                </TD>
                <TD>{t.signals}</TD>
                <TD>{t.waiting} / {t.open}</TD>
                <TD>{t.not_filled + t.unfilled + t.expired}</TD>
                <TD>{t.closed} of {t.closed_needed}</TD>
                <TD>{pct(t.win_rate_pct)}</TD>
                <TD>{r(t.avg_net_r)}</TD>
                <TD>{r(t.total_net_r)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
        <p className="mt-3 text-xs text-muted-foreground">
          Since the last backend start: {d.status.candles_checked} closed candles checked, {d.status.signals_seen} qualified signals seen.
          {d.status.last_error && <span className="text-bear"> Last error: {d.status.last_error}</span>}
        </p>
        {d.notes.map((n) => <p key={n} className="mt-1 text-xs text-muted-foreground">{n}</p>)}
      </CardContent>
    </Card>
  );
}
