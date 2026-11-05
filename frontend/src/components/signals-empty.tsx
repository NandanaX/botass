"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Time } from "@/components/time";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { fmtPrice } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { ScannerRow } from "@/lib/types";
import { CandleCountdown } from "./decision";
import { DirectionBadge, Empty, ErrorBox, EvaluationBadge, SignalStatusBadge } from "./status";

const FORMING = ["REJECTED", "WATCH_LONG", "WATCH_SHORT"];

export interface ClosestRow {
  row: ScannerRow;
  distancePct: number | null;
  zone: { kind: "support" | "resistance"; lower: number; upper: number; status: string } | null;
}

/** Forming setups first, then trend-aligned markets by distance to the nearest zone a strategy could use. */
export function closestToTriggering(rows: ScannerRow[], priceOf: (r: ScannerRow) => number | null | undefined, limit = 6): ClosestRow[] {
  const measured = rows
    .filter((r) => FORMING.includes(r.signal_status) || r.trend === "BULLISH" || r.trend === "BEARISH")
    .map((row) => {
      const price = priceOf(row);
      const zones = [
        row.nearest_support && { kind: "support" as const, ...row.nearest_support },
        row.nearest_resistance && { kind: "resistance" as const, ...row.nearest_resistance },
      ].filter((z) => !!z);
      const dist = (z: { lower: number; upper: number }) => (price ? ((price < z.lower ? z.lower - price : price > z.upper ? price - z.upper : 0) / price) * 100 : null);
      const best = zones.map((zone) => ({ zone, d: dist(zone) })).sort((a, b) => (a.d ?? Infinity) - (b.d ?? Infinity))[0];
      return { row, distancePct: best?.d ?? null, zone: best?.zone ?? null };
    });
  return measured
    .sort((a, b) => Number(FORMING.includes(b.row.signal_status)) - Number(FORMING.includes(a.row.signal_status)) || (a.distancePct ?? Infinity) - (b.distancePct ?? Infinity))
    .slice(0, limit);
}

/** Shown instead of an empty list when no signal is active. */
export function SignalsEmptyState() {
  const { tickers } = useLive();
  const scanner = useQuery({ queryKey: ["scanner", "closest"], queryFn: () => api.scanner({}), refetchInterval: 30_000 });
  const recent = useQuery({ queryKey: ["signals", "recent-empty"], queryFn: () => api.signals({ status: "all", limit: 5, sort: "created_at", order: "desc" }) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const timeframes = settings.data?.settings.timeframes ?? [];
  const closest = closestToTriggering(scanner.data?.rows ?? [], (r) => tickers[r.symbol]?.last_price ?? r.ticker?.last_price ?? r.close);

  return (
    <div className="space-y-4" data-testid="signals-empty">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 rounded-md border border-border bg-card px-4 py-3">
        <div>
          <div className="text-sm font-semibold">No active signals right now</div>
          <p className="text-xs text-muted-foreground">The scanner checks every closed candle. Next closes:</p>
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground" data-testid="next-closes">
          {timeframes.map((tf) => <CandleCountdown key={tf} timeframe={tf} label={false} />)}
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Closest to triggering</CardTitle>
            <CardDescription>Forming setups first, then trend-aligned markets by distance to their nearest zone. Being close doesn&apos;t mean a signal will follow.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1.5" data-testid="closest">
            <ErrorBox error={scanner.error} />
            {closest.map(({ row, distancePct, zone }) => (
              <Link key={`${row.symbol}-${row.timeframe}`} href={`/setup/${row.symbol}?tf=${row.timeframe}`} className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2 text-xs hover:bg-accent">
                <span className="font-medium">{row.symbol}</span>
                <span className="text-muted-foreground">{row.timeframe}</span>
                <SignalStatusBadge status={row.signal_status} />
                <span className="ml-auto tabular text-muted-foreground">
                  {zone ? `${distancePct === 0 ? "inside" : `${(distancePct ?? 0).toFixed(2)}% from`} ${zone.kind} ${fmtPrice(zone.lower)}–${fmtPrice(zone.upper)}` : `${row.trend.toLowerCase()}, no zone nearby`}
                </span>
              </Link>
            ))}
            {scanner.data && closest.length === 0 && <Empty>No market has an aligned trend right now, so nothing can trigger yet.</Empty>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
            <div className="space-y-1">
              <CardTitle>Last 5 signals</CardTitle>
              <CardDescription>Outcomes marked * are hypothetical, not realized trades.</CardDescription>
            </div>
            <Button asChild size="sm" variant="outline"><Link href="/history">Signal History →</Link></Button>
          </CardHeader>
          <CardContent className="space-y-1.5" data-testid="recent">
            <ErrorBox error={recent.error} />
            {recent.data?.items.map((s) => (
              <Link key={s.id} href={`/signals?id=${s.id}`} className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2 text-xs hover:bg-accent">
                <span className="font-medium">{s.symbol}</span>
                <span className="text-muted-foreground">{s.timeframe}</span>
                <DirectionBadge direction={s.direction} />
                <span className="tabular text-muted-foreground"><Time iso={s.candle_close_time} /></span>
                <span className="ml-auto"><EvaluationBadge status={s.evaluation_status} /></span>
              </Link>
            ))}
            {recent.data && recent.data.items.length === 0 && <Empty>No signals recorded yet.</Empty>}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
