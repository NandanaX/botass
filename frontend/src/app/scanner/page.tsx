"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { PageHeader } from "@/components/app-shell";
import { DataStateBadge, Empty, ErrorBox, SignalStatusBadge, TrendText, useNow } from "@/components/status";
import { Card, CardContent } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/form";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { api } from "@/lib/api";
import { changeClass, fmtCompact, fmtNum, fmtPct, fmtPrice, timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";
import { TIMEFRAMES, type ScannerRow } from "@/lib/types";

type SortKey = "symbol" | "timeframe" | "price" | "change" | "volume" | "trend" | "rsi" | "signal" | "updated";

const STATUSES = ["LONG_SIGNAL", "SHORT_SIGNAL", "WATCH_LONG", "WATCH_SHORT", "REJECTED", "NONE", "NOT_READY", "DATA_ISSUE"];

export default function ScannerPage() {
  const [symbol, setSymbol] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [trend, setTrend] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "symbol", dir: 1 });
  const { tickers } = useLive();
  useNow(10_000);

  const q = useQuery({
    queryKey: ["scanner", timeframe, trend, status],
    queryFn: () => api.scanner({ timeframe: timeframe || undefined, trend: trend || undefined, signal_status: status || undefined }),
    refetchInterval: 30_000,
  });

  const rows = useMemo(() => {
    const val = (r: ScannerRow): string | number => {
      const t = tickers[r.symbol] ?? r.ticker;
      switch (sort.key) {
        case "price": return t.last_price ?? -Infinity;
        case "change": return t.change_24h_pct ?? -Infinity;
        case "volume": return t.turnover_24h ?? -Infinity;
        case "rsi": return r.rsi ?? -Infinity;
        case "trend": return r.trend;
        case "signal": return r.signal_status;
        case "updated": return r.updated_at;
        case "timeframe": return TIMEFRAMES.indexOf(r.timeframe as (typeof TIMEFRAMES)[number]);
        default: return `${r.symbol}-${TIMEFRAMES.indexOf(r.timeframe as (typeof TIMEFRAMES)[number])}`;
      }
    };
    return (q.data?.rows ?? [])
      .filter((r) => !symbol || r.symbol.includes(symbol.toUpperCase()))
      .sort((a, b) => {
        const va = val(a), vb = val(b);
        return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir;
      });
  }, [q.data, symbol, sort, tickers]);

  const header = (key: SortKey, label: string, right = false) => (
    <TH className={right ? "text-right" : ""}>
      <button
        className="inline-flex items-center gap-1 uppercase hover:text-foreground"
        onClick={() => setSort((s) => ({ key, dir: s.key === key ? ((-s.dir) as 1 | -1) : 1 }))}
      >
        {label}
        {sort.key === key && (sort.dir === 1 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </button>
    </TH>
  );

  return (
    <>
      <PageHeader title="Market Scanner" description="Latest closed-candle analysis for every watchlisted symbol and scanned timeframe." />
      <Card className="mb-4">
        <CardContent className="grid gap-3 pt-4 sm:grid-cols-4">
          <Input placeholder="Filter symbol…" value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Symbol filter" />
          <NativeSelect value={timeframe} onChange={(e) => setTimeframe(e.target.value)} aria-label="Timeframe filter">
            <option value="">All timeframes</option>
            {TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}
          </NativeSelect>
          <NativeSelect value={trend} onChange={(e) => setTrend(e.target.value)} aria-label="Trend filter">
            <option value="">All trends</option>
            <option value="BULLISH">Bullish</option>
            <option value="BEARISH">Bearish</option>
            <option value="NEUTRAL">Neutral</option>
          </NativeSelect>
          <NativeSelect value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Signal status filter">
            <option value="">All signal states</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ").toLowerCase()}</option>)}
          </NativeSelect>
        </CardContent>
      </Card>
      <ErrorBox error={q.error} />
      <Card>
        <CardContent className="pt-2">
          <Table>
            <THead>
              <TR>
                {header("symbol", "Symbol")}
                {header("timeframe", "TF")}
                {header("price", "Price", true)}
                {header("change", "24h", true)}
                {header("volume", "24h Volume", true)}
                {header("trend", "Trend")}
                {header("rsi", "RSI", true)}
                <TH>MACD</TH>
                <TH>EMA alignment</TH>
                <TH>Nearest S / R</TH>
                {header("signal", "Signal")}
                {header("updated", "Last updated")}
              </TR>
            </THead>
            <TBody>
              {rows.map((r) => {
                const t = tickers[r.symbol] ?? r.ticker;
                return (
                  <TR key={`${r.symbol}-${r.timeframe}`}>
                    <TD>
                      <Link className="font-medium hover:underline" href={`/market/${r.symbol}?tf=${r.timeframe}`}>{r.symbol}</Link>
                    </TD>
                    <TD>{r.timeframe}</TD>
                    <TD className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        {fmtPrice(t.last_price)} <DataStateBadge ticker={t} />
                      </div>
                    </TD>
                    <TD className={`text-right ${changeClass(t.change_24h_pct)}`}>{fmtPct(t.change_24h_pct)}</TD>
                    <TD className="text-right">${fmtCompact(t.turnover_24h)}</TD>
                    <TD><TrendText value={r.trend} /></TD>
                    <TD className="text-right">{fmtNum(r.rsi, 1)}</TD>
                    <TD><TrendText value={r.macd_state} /></TD>
                    <TD><TrendText value={r.ema_alignment} /></TD>
                    <TD className="tabular text-xs">
                      <span className="text-bull">{r.nearest_support ? `${fmtPrice(r.nearest_support.lower)}–${fmtPrice(r.nearest_support.upper)}` : "—"}</span>
                      <span className="text-muted-foreground"> / </span>
                      <span className="text-bear">{r.nearest_resistance ? `${fmtPrice(r.nearest_resistance.lower)}–${fmtPrice(r.nearest_resistance.upper)}` : "—"}</span>
                    </TD>
                    <TD>
                      <div className="flex items-center gap-1">
                        <Link href={`/setup/${r.symbol}?tf=${r.timeframe}`} title={r.setup ? `${r.setup.direction} ${r.setup.strategy}: ${r.setup.reason ?? r.setup.status}` : "Open trade setup"}>
                          <SignalStatusBadge status={r.signal_status} />
                        </Link>
                        {r.active_signals.length > 0 && (
                          <Link href="/signals" className="text-[11px] text-primary hover:underline">{r.active_signals.length} active</Link>
                        )}
                      </div>
                    </TD>
                    <TD className="text-muted-foreground" title={r.updated_at}>{timeAgo(r.updated_at)}</TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
          {!q.isLoading && rows.length === 0 && <Empty>No rows match. The scanner fills this table after its first cycle.</Empty>}
          {q.isLoading && <Empty>Loading…</Empty>}
        </CardContent>
      </Card>
    </>
  );
}
