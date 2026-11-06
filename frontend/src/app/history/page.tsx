"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Time } from "@/components/time";
import { PageHeader } from "@/components/app-shell";
import { ScoreBar } from "@/components/signal-detail";
import { DirectionBadge, Empty, ErrorBox, EvaluationBadge, NotificationBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/form";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { fmtNum, fmtPrice } from "@/lib/format";
import { TIMEFRAMES } from "@/lib/types";

const PAGE = 25;

function SignalsTable() {
  const [f, setF] = useState({ status: "all", symbol: "", timeframe: "15m", direction: "", evaluation_status: "", min_score: "" });
  const [sort, setSort] = useState<{ key: string; order: "asc" | "desc" }>({ key: "created_at", order: "desc" });
  const [page, setPage] = useState(0);
  const symbolParam = /^[A-Z0-9]{2,20}USDT$/.test(f.symbol.toUpperCase()) ? f.symbol.toUpperCase() : undefined;
  const q = useQuery({
    queryKey: ["signals", "history", f, sort, page, symbolParam],
    queryFn: () =>
      api.signals({
        status: f.status,
        symbol: symbolParam,
        timeframe: f.timeframe || undefined,
        direction: f.direction || undefined,
        evaluation_status: f.evaluation_status || undefined,
        min_score: f.min_score ? Number(f.min_score) : undefined,
        sort: sort.key,
        order: sort.order,
        limit: PAGE,
        offset: page * PAGE,
      }),
    placeholderData: keepPreviousData,
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setPage(0);
    setF((x) => ({ ...x, [k]: e.target.value }));
  };
  const sortHeader = (key: string, label: string, cls = "") => (
    <TH className={cls}>
      <button
        className="inline-flex items-center gap-1 uppercase hover:text-foreground"
        onClick={() => setSort((s) => ({ key, order: s.key === key && s.order === "desc" ? "asc" : "desc" }))}
      >
        {label}
        {sort.key === key && (sort.order === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </button>
    </TH>
  );
  const total = q.data?.total ?? 0;

  return (
    <>
      <Card className="mb-4">
        <CardContent className="grid gap-3 pt-4 sm:grid-cols-3 lg:grid-cols-6">
          <NativeSelect value={f.status} onChange={set("status")} aria-label="Status">
            <option value="all">All signals</option>
            <option value="active">Active</option>
            <option value="closed">Resolved</option>
          </NativeSelect>
          <Input placeholder="Symbol, e.g. BTCUSDT" value={f.symbol} onChange={set("symbol")} aria-label="Symbol" />
          <NativeSelect value={f.timeframe} onChange={set("timeframe")} aria-label="Timeframe">
            <option value="">All timeframes</option>
            {TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}
          </NativeSelect>
          <NativeSelect value={f.direction} onChange={set("direction")} aria-label="Direction">
            <option value="">Long & short</option>
            <option value="LONG">Long</option>
            <option value="SHORT">Short</option>
          </NativeSelect>
          <NativeSelect value={f.evaluation_status} onChange={set("evaluation_status")} aria-label="Outcome">
            <option value="">Any outcome</option>
            {["PENDING", "TARGET_HIT", "STOP_HIT", "AMBIGUOUS_STOP", "EXPIRED", "INVALIDATED"].map((s) => (
              <option key={s} value={s}>{s.replace(/_/g, " ").toLowerCase()}</option>
            ))}
          </NativeSelect>
          <Input type="number" min={0} max={100} placeholder="Min score" value={f.min_score} onChange={set("min_score")} aria-label="Minimum score" />
        </CardContent>
      </Card>
      <ErrorBox error={q.error} />
      <Card>
        <CardContent className="pt-2">
          <Table>
            <THead>
              <TR>
                {sortHeader("created_at", "Detected")}
                {sortHeader("candle_close_time", "Candle close")}
                {sortHeader("symbol", "Symbol")}
                <TH>TF</TH><TH>Dir</TH>
                <TH className="text-right">Entry ref</TH><TH className="text-right">Stop</TH><TH className="text-right">Target</TH>
                {sortHeader("score", "Score")}
                <TH>HTF</TH><TH>Alert</TH><TH>Outcome*</TH><TH className="text-right">R*</TH>
              </TR>
            </THead>
            <TBody>
              {q.data?.items.map((s) => (
                <TR key={s.id}>
                  <TD className="text-muted-foreground"><Time iso={s.created_at} /></TD>
                  <TD className="text-muted-foreground"><Time iso={s.candle_close_time} /></TD>
                  <TD><Link href={`/signals?id=${s.id}`} className="font-medium hover:underline">{s.symbol}</Link></TD>
                  <TD>{s.timeframe}</TD>
                  <TD><DirectionBadge direction={s.direction} /></TD>
                  <TD className="text-right">{fmtPrice(s.entry_reference)}</TD>
                  <TD className="text-right text-bear">{fmtPrice(s.stop_loss)}</TD>
                  <TD className="text-right text-bull">{fmtPrice(s.take_profit)}</TD>
                  <TD><ScoreBar score={s.score} /></TD>
                  <TD>{s.mtf_confirmed === null ? <span className="text-muted-foreground">—</span> : s.mtf_confirmed ? <Badge variant="bull">agree</Badge> : <Badge variant="warn">mixed</Badge>}</TD>
                  <TD><NotificationBadge status={s.notification_status} /></TD>
                  <TD><EvaluationBadge status={s.evaluation_status} /></TD>
                  <TD className="text-right">{fmtNum(s.evaluation?.r_multiple)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
          {q.data && q.data.items.length === 0 && <Empty>No signals match these filters.</Empty>}
          <div className="flex items-center justify-between pt-3 text-xs text-muted-foreground">
            <span>* Hypothetical outcome by methodology v1 (next-open entry, OHLC touches, stop-first on ambiguity). Not realized results.</span>
            <div className="flex items-center gap-2">
              <span>{total ? `${page * PAGE + 1}–${Math.min(total, (page + 1) * PAGE)} of ${total}` : "0"}</span>
              <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Prev</Button>
              <Button size="sm" variant="outline" disabled={(page + 1) * PAGE >= total} onClick={() => setPage((p) => p + 1)}>Next</Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </>
  );
}

function NotificationsTable() {
  const q = useQuery({ queryKey: ["notifications"], queryFn: () => api.notifications(100), refetchInterval: 30_000 });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Notification history</CardTitle>
        <CardDescription>Every Telegram delivery attempt, including test messages.</CardDescription>
      </CardHeader>
      <CardContent>
        <ErrorBox error={q.error} />
        <Table>
          <THead>
            <TR><TH>Time</TH><TH>Kind</TH><TH>Status</TH><TH>Attempts</TH><TH>Signal</TH><TH>Error</TH></TR>
          </THead>
          <TBody>
            {q.data?.items.map((n) => (
              <TR key={n.id}>
                <TD className="text-muted-foreground"><Time iso={n.created_at} seconds /></TD>
                <TD>{n.kind}</TD>
                <TD><NotificationBadge status={n.status} /></TD>
                <TD>{n.attempts}</TD>
                <TD>{n.signal_id ? <Link href={`/signals?id=${n.signal_id}`} className="text-primary hover:underline">view</Link> : "—"}</TD>
                <TD className="max-w-xs truncate text-bear" title={n.error ?? ""}>{n.error ?? ""}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {q.data && q.data.items.length === 0 && <Empty>No notifications sent yet.</Empty>}
      </CardContent>
    </Card>
  );
}

export default function HistoryPage() {
  return (
    <>
      <PageHeader title="Signal History" description="Every persisted signal with its alert delivery and outcome evaluation." />
      <Tabs defaultValue="signals">
        <TabsList>
          <TabsTrigger hasPanel value="signals">Signals</TabsTrigger>
          <TabsTrigger hasPanel value="notifications">Notifications</TabsTrigger>
        </TabsList>
        <TabsContent value="signals"><SignalsTable /></TabsContent>
        <TabsContent value="notifications"><NotificationsTable /></TabsContent>
      </Tabs>
    </>
  );
}
