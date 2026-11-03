"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/app-shell";
import { ScoreBar } from "@/components/signal-detail";
import {
  DataStateBadge,
  DirectionBadge,
  Empty,
  ErrorBox,
  EvaluationBadge,
  SignalStatusBadge,
  TrendText,
} from "@/components/status";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/form";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { api } from "@/lib/api";
import { changeClass, fmtCompact, fmtNum, fmtPct, fmtPrice, fmtTime, timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";
import { TIMEFRAMES, type AnalysisSnapshot, type Ticker } from "@/lib/types";

function MarketCard({ ticker: initial, analysis }: { ticker: Ticker; analysis: AnalysisSnapshot | null }) {
  const { tickers } = useLive();
  const t = tickers[initial.symbol] ?? initial;
  const ev = analysis?.evaluation;
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <Link href={`/market/${t.symbol}`} className="text-sm font-semibold hover:underline">
          {t.symbol}
        </Link>
        <DataStateBadge ticker={t} />
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-baseline justify-between">
          <span className="tabular text-2xl font-semibold">{t.available ? fmtPrice(t.last_price) : "—"}</span>
          <span className={`tabular text-sm ${changeClass(t.change_24h_pct)}`}>{fmtPct(t.change_24h_pct)}</span>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          <span className="text-muted-foreground">24h turnover</span>
          <span className="tabular text-right">${fmtCompact(t.turnover_24h)}</span>
          <span className="text-muted-foreground">Open interest</span>
          <span className="tabular text-right">${fmtCompact(t.open_interest_value)}</span>
          <span className="text-muted-foreground">Funding rate</span>
          <span className="tabular text-right">{t.funding_rate != null ? fmtPct(t.funding_rate * 100, 4) : "—"}</span>
        </div>
        <div className="border-t pt-3">
          {!ev ? (
            <p className="text-xs text-muted-foreground">Waiting for the scanner to analyse this timeframe…</p>
          ) : !ev.ready ? (
            <p className="text-xs text-muted-foreground">{ev.not_ready_reason}</p>
          ) : (
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              <span className="text-muted-foreground">Trend (EMA)</span>
              <span className="text-right"><TrendText value={ev.trend} /></span>
              <span className="text-muted-foreground">RSI 14</span>
              <span className="tabular text-right">{fmtNum(ev.indicators.rsi, 1)}</span>
              <span className="text-muted-foreground">EMA status</span>
              <span className="text-right"><TrendText value={ev.ema_alignment} /></span>
              <span className="text-muted-foreground">MACD momentum</span>
              <span className="text-right"><TrendText value={ev.macd_state} /></span>
              <span className="text-muted-foreground">Signal</span>
              <span className="text-right"><SignalStatusBadge status={analysis!.signal_status} /></span>
              <span className="col-span-2 text-[11px] text-muted-foreground">
                As of candle close {fmtTime(ev.close_time)}
              </span>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function OverviewPage() {
  const [tf, setTf] = useState("15m");
  const overview = useQuery({ queryKey: ["overview", tf], queryFn: () => api.overview(tf), refetchInterval: 30_000 });
  const active = useQuery({ queryKey: ["signals", "active-overview"], queryFn: () => api.signals({ status: "active", limit: 10 }) });
  const recent = useQuery({ queryKey: ["signals", "recent-overview"], queryFn: () => api.signals({ limit: 8 }) });
  const stats = useQuery({ queryKey: ["signal-stats"], queryFn: api.signalStats });
  const { scanner } = useLive();
  const sc = scanner ?? overview.data?.scanner;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Live Bybit USDT-perpetual prices with indicator analysis on closed candles."
        actions={
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            Analysis timeframe
            <NativeSelect value={tf} onChange={(e) => setTf(e.target.value)} className="h-8 w-20">
              {TIMEFRAMES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </NativeSelect>
          </div>
        }
      />
      <ErrorBox error={overview.error} />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {overview.data?.markets.map((m) => <MarketCard key={m.ticker.symbol} ticker={m.ticker} analysis={m.analysis} />)}
      </div>
      {overview.isLoading && <Empty>Loading market data…</Empty>}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Active signals</CardTitle>
            <CardDescription>Confirmed setups awaiting an outcome. These are research signals, not open positions.</CardDescription>
          </CardHeader>
          <CardContent>
            {active.data?.items.length ? (
              <Table>
                <THead>
                  <TR>
                    <TH>Symbol</TH><TH>TF</TH><TH>Dir</TH><TH className="text-right">Entry ref</TH>
                    <TH className="text-right">Stop</TH><TH className="text-right">Target</TH><TH>Score</TH><TH>Detected</TH>
                  </TR>
                </THead>
                <TBody>
                  {active.data.items.map((s) => (
                    <TR key={s.id}>
                      <TD><Link className="hover:underline" href={`/signals?id=${s.id}`}>{s.symbol}</Link></TD>
                      <TD>{s.timeframe}</TD>
                      <TD><DirectionBadge direction={s.direction} /></TD>
                      <TD className="text-right">{fmtPrice(s.entry_reference)}</TD>
                      <TD className="text-right text-bear">{fmtPrice(s.stop_loss)}</TD>
                      <TD className="text-right text-bull">{fmtPrice(s.take_profit)}</TD>
                      <TD><ScoreBar score={s.score} /></TD>
                      <TD className="text-muted-foreground">{timeAgo(s.created_at)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            ) : (
              <Empty>No active signals. The scanner checks every closed candle.</Empty>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Market scanner</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-y-1.5 text-xs">
              <span className="text-muted-foreground">Status</span>
              <span className="text-right">{sc?.running ? (sc.is_leader ? "Running" : "Standby") : "Stopped"}</span>
              <span className="text-muted-foreground">Last cycle</span>
              <span className="text-right">{timeAgo(sc?.last_cycle_at)}</span>
              <span className="text-muted-foreground">Cycle duration</span>
              <span className="tabular text-right">{sc?.last_cycle_ms != null ? `${sc.last_cycle_ms} ms` : "—"}</span>
              <span className="text-muted-foreground">Candle evaluations</span>
              <span className="tabular text-right">{sc?.evaluations ?? "—"}</span>
              <span className="text-muted-foreground">Signals this session</span>
              <span className="tabular text-right">{sc?.signals_created ?? "—"}</span>
              <span className="text-muted-foreground">Errors</span>
              <span className={`tabular text-right ${sc?.errors ? "text-bear" : ""}`}>{sc?.errors ?? "—"}</span>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Signal performance</CardTitle>
              <CardDescription>Hypothetical outcomes of live signals (methodology v1). Not realized trades.</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-y-1.5 text-xs">
              <span className="text-muted-foreground">Total signals</span>
              <span className="tabular text-right">{stats.data?.total ?? "—"}</span>
              <span className="text-muted-foreground">Active</span>
              <span className="tabular text-right">{stats.data?.active ?? "—"}</span>
              <span className="text-muted-foreground">Resolved</span>
              <span className="tabular text-right">{stats.data?.hypothetical.resolved ?? "—"}</span>
              <span className="text-muted-foreground">Target / stop hits</span>
              <span className="tabular text-right">
                {stats.data ? `${stats.data.hypothetical.target_hits} / ${stats.data.hypothetical.stop_hits}` : "—"}
              </span>
              <span className="text-muted-foreground">Hit rate</span>
              <span className="tabular text-right">{fmtPct(stats.data?.hypothetical.hit_rate_pct, 1, false)}</span>
              <span className="text-muted-foreground">Avg R (gross)</span>
              <span className="tabular text-right">{fmtNum(stats.data?.hypothetical.avg_r_multiple)}</span>
            </CardContent>
          </Card>
        </div>
      </div>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Recent signals</CardTitle>
        </CardHeader>
        <CardContent>
          {recent.data?.items.length ? (
            <Table>
              <THead>
                <TR><TH>Detected</TH><TH>Symbol</TH><TH>TF</TH><TH>Dir</TH><TH className="text-right">Entry ref</TH><TH>Score</TH><TH>Status</TH></TR>
              </THead>
              <TBody>
                {recent.data.items.map((s) => (
                  <TR key={s.id}>
                    <TD className="text-muted-foreground">{fmtTime(s.created_at)}</TD>
                    <TD><Link className="hover:underline" href={`/signals?id=${s.id}`}>{s.symbol}</Link></TD>
                    <TD>{s.timeframe}</TD>
                    <TD><DirectionBadge direction={s.direction} /></TD>
                    <TD className="text-right">{fmtPrice(s.entry_reference)}</TD>
                    <TD><ScoreBar score={s.score} /></TD>
                    <TD><EvaluationBadge status={s.evaluation_status} /></TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : (
            <Empty>No signals recorded yet.</Empty>
          )}
        </CardContent>
      </Card>
    </>
  );
}
