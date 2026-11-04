"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { PageHeader } from "@/components/app-shell";
import { ChartFrame } from "@/components/chart-frame";
import { ChartToggleBar, DEFAULT_TOGGLES, PriceChart, type ChartToggles } from "@/components/price-chart";
import { DataStateBadge, Empty, ErrorBox, SetupStatusBadge, SignalStatusBadge, StrategyLabel, TrendText } from "@/components/status";
import { ZoneList } from "@/components/zones";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/form";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { changeClass, fmtCompact, fmtNum, fmtPct, fmtPrice, fmtTime } from "@/lib/format";
import { useTicker } from "@/lib/live";
import { TIMEFRAMES, type ConditionCheck } from "@/lib/types";

function Checks({ title, checks }: { title: string; checks: ConditionCheck[] }) {
  const passed = checks.filter((c) => c.passed).length;
  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold">
        {title} <span className="text-muted-foreground">{passed}/{checks.length}</span>
      </div>
      <ul className="space-y-1 text-xs">
        {checks.map((c) => (
          <li key={c.key} className="flex items-start gap-1.5">
            {c.passed ? <Check className="mt-px h-3.5 w-3.5 shrink-0 text-bull" /> : <X className="mt-px h-3.5 w-3.5 shrink-0 text-bear" />}
            <span>{c.label} <span className="text-muted-foreground">· {c.detail}</span></span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MarketDetail() {
  const params = useParams<{ symbol: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const symbol = (params.symbol || "BTCUSDT").toUpperCase();
  const tf = search.get("tf") && TIMEFRAMES.includes(search.get("tf") as never) ? search.get("tf")! : "15m";
  const focusId = search.get("signal");
  const [toggles, setToggles] = useState<ChartToggles>(DEFAULT_TOGGLES);

  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const candles = useQuery({
    queryKey: ["candles", symbol, tf],
    queryFn: () => api.candles(symbol, tf),
    refetchInterval: 60_000,
  });
  const explanation = useQuery({
    queryKey: ["explanation", symbol, tf],
    queryFn: () => api.explanation(symbol, tf),
    retry: false,
  });
  const ticker = useTicker(symbol, candles.data?.ticker);
  const ev = candles.data?.analysis;
  const focusSignal = focusId ? candles.data?.signals.find((s) => s.id === focusId) ?? null : null;
  const watchlist = settings.data?.settings.watchlist ?? [];
  const go = (sym: string, timeframe: string) => router.push(`/market/${sym}?tf=${timeframe}`);
  const ind = ev?.indicators ?? {};

  return (
    <>
      <PageHeader
        title={`${symbol} perpetual`}
        description="Candles shown are closed candles plus the live forming candle (streamed). Analysis uses closed candles only."
        actions={
          <>
            <NativeSelect value={symbol} onChange={(e) => go(e.target.value, tf)} className="h-8 w-32" aria-label="Symbol">
              {[...new Set([symbol, ...watchlist])].map((s) => <option key={s}>{s}</option>)}
            </NativeSelect>
            <Tabs value={tf} onValueChange={(v) => go(symbol, v)}>
              <TabsList>
                {TIMEFRAMES.map((t) => <TabsTrigger key={t} value={t}>{t}</TabsTrigger>)}
              </TabsList>
            </Tabs>
          </>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span className="tabular text-2xl font-semibold">{ticker?.available ? fmtPrice(ticker.last_price) : "—"}</span>
        <span className={`tabular ${changeClass(ticker?.change_24h_pct)}`}>{fmtPct(ticker?.change_24h_pct)} 24h</span>
        <DataStateBadge ticker={ticker} />
        <span className="text-muted-foreground">Mark <span className="tabular text-foreground">{fmtPrice(ticker?.mark_price)}</span></span>
        <span className="text-muted-foreground">24h vol <span className="tabular text-foreground">${fmtCompact(ticker?.turnover_24h)}</span></span>
        <span className="text-muted-foreground">OI <span className="tabular text-foreground">${fmtCompact(ticker?.open_interest_value)}</span></span>
        <span className="text-muted-foreground">Funding <span className="tabular text-foreground">{ticker?.funding_rate != null ? fmtPct(ticker.funding_rate * 100, 4) : "—"}</span></span>
      </div>
      <ErrorBox error={candles.error} />
      {candles.data && !candles.data.data_quality.current && (
        <div className="mb-3 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          Candle history could not be refreshed from Bybit; the chart may be stale.
        </div>
      )}

      <div className="grid items-start gap-4 xl:grid-cols-[1fr_340px]">
        <ChartFrame
          toolbar={
            <div className="flex flex-wrap items-center gap-4">
              <span className="text-sm font-semibold">{symbol} · {tf}</span>
              <ChartToggleBar toggles={toggles} onChange={setToggles} />
              {focusSignal && <Badge variant="info">Showing levels of selected signal</Badge>}
            </div>
          }
        >
          {(full) =>
            candles.data ? (
              <PriceChart data={candles.data} toggles={toggles} focusSignal={focusSignal} fill={full} />
            ) : (
              <div className={`flex ${full ? "h-full" : "h-[480px]"} items-center justify-center text-sm text-muted-foreground`}>
                {candles.isLoading ? "Loading candles from Bybit…" : "No chart data"}
              </div>
            )
          }
        </ChartFrame>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                Indicators ({tf})
                {ev && <SignalStatusBadge status={ev.setup ? `${ev.setup.direction}_SIGNAL` : ev.setups?.some((x) => x.status === "REJECTED") ? "REJECTED" : ev.setups?.[0] ? `WATCH_${ev.setups[0].direction}` : ev.ready ? "NONE" : "NOT_READY"} />}
              </CardTitle>
              <CardDescription>Last closed candle: {fmtTime(ev?.close_time)}</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-y-1.5 text-xs">
              <span className="text-muted-foreground">Trend</span><span className="text-right"><TrendText value={ev?.trend} /></span>
              <span className="text-muted-foreground">Structure</span><span className="text-right">{ev?.structure ?? "—"}</span>
              <span className="text-muted-foreground">EMA 20 / 50</span><span className="tabular text-right">{fmtPrice(ind.ema_fast)} / {fmtPrice(ind.ema_mid)}</span>
              <span className="text-muted-foreground">EMA 200 / SMA 200</span><span className="tabular text-right">{fmtPrice(ind.ema_slow)} / {fmtPrice(ind.sma_long)}</span>
              <span className="text-muted-foreground">RSI 14</span><span className="tabular text-right">{fmtNum(ind.rsi, 1)}</span>
              <span className="text-muted-foreground">MACD / signal</span><span className="tabular text-right">{fmtNum(ind.macd, 2)} / {fmtNum(ind.macd_signal, 2)}</span>
              <span className="text-muted-foreground">MACD histogram</span><span className={`tabular text-right ${changeClass(ind.macd_hist)}`}>{fmtNum(ind.macd_hist, 2)}</span>
              <span className="text-muted-foreground">ATR 14</span><span className="tabular text-right">{fmtPrice(ind.atr)}</span>
              <span className="text-muted-foreground">Bollinger (U/L)</span><span className="tabular text-right">{fmtPrice(ind.bb_upper)} / {fmtPrice(ind.bb_lower)}</span>
              <span className="text-muted-foreground">Relative volume</span><span className="tabular text-right">{fmtNum(ind.rvol, 2)}x</span>
              <span className="text-muted-foreground">20-bar high / low</span><span className="tabular text-right">{fmtPrice(ind.prior_high)} / {fmtPrice(ind.prior_low)}</span>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                Entry setups
                <span className="flex gap-1">
                  <Button asChild size="sm" variant="outline"><Link href={`/analysis/${symbol}?tf=${tf}`}>Chart analysis →</Link></Button>
                  <Button asChild size="sm" variant="outline"><Link href={`/setup/${symbol}?tf=${tf}`}>Trade setup →</Link></Button>
                </span>
              </CardTitle>
              <CardDescription>Breakout and pullback rules on the last closed candle.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
              {ev?.setups?.length ? ev.setups.map((st) => (
                <div key={`${st.strategy}-${st.direction}`} className="space-y-0.5 border-b border-border/60 pb-2 last:border-0">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{st.direction} · <StrategyLabel strategy={st.strategy} /></span>
                    <SetupStatusBadge status={st.status} />
                  </div>
                  <p className="text-muted-foreground">{st.status === "QUALIFIED" ? st.explanation : st.reasons[0]?.message}</p>
                </div>
              )) : <span className="text-muted-foreground">{ev?.ready ? "No breakout or pullback setup on the last closed candle." : "Warming up."}</span>}
            </CardContent>
          </Card>
          {ev?.ready && (
            <Card>
              <CardHeader><CardTitle>Trend &amp; momentum checks</CardTitle><CardDescription>Range-breakout rule set (Phase 1), shown for context.</CardDescription></CardHeader>
              <CardContent className="space-y-3">
                <Checks title="LONG" checks={ev.long_checks} />
                <Checks title="SHORT" checks={ev.short_checks} />
                {ev.observations.length > 0 && (
                  <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                    {ev.observations.map((o) => <li key={o}>{o}</li>)}
                  </ul>
                )}
              </CardContent>
            </Card>
          )}
          <Card>
            <CardHeader><CardTitle>Support / resistance zones</CardTitle><CardDescription>ATR-clustered confirmed swing points ({tf}, plus 1h/4h).</CardDescription></CardHeader>
            <CardContent>
              <ZoneList zones={[...(candles.data?.zones ?? []), ...Object.values(candles.data?.htf_zones ?? {}).flat()]} price={ev?.close} />
            </CardContent>
          </Card>
        </div>
      </div>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Market explanation</CardTitle>
          <CardDescription>Generated from the deterministic indicator values (template explainer; no external AI service).</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {explanation.data ? (
            <>
              <p>{explanation.data.explanation.summary}</p>
              <div className="grid gap-4 md:grid-cols-3 text-xs">
                <div><div className="mb-1 font-semibold">Conditions</div><ul className="space-y-0.5">{explanation.data.explanation.conditions.map((c) => <li key={c}>{c}</li>)}</ul></div>
                <div><div className="mb-1 font-semibold">Invalidation</div><ul className="space-y-0.5">{explanation.data.explanation.invalidation.map((c) => <li key={c}>{c}</li>)}</ul></div>
                <div><div className="mb-1 font-semibold">Risks</div><ul className="space-y-0.5">{explanation.data.explanation.risks.map((c) => <li key={c}>{c}</li>)}</ul></div>
              </div>
              <p className="text-[11px] text-muted-foreground">{explanation.data.explanation.disclaimer}</p>
            </>
          ) : (
            <Empty>{explanation.isLoading ? "Loading…" : "No scanner analysis for this timeframe yet. Add it to the scanned timeframes in Settings."}</Empty>
          )}
        </CardContent>
      </Card>
    </>
  );
}

export default function MarketPage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <MarketDetail />
    </Suspense>
  );
}
