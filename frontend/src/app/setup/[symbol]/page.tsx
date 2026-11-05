"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Time } from "@/components/time";
import { PageHeader } from "@/components/app-shell";
import { ChartFrame } from "@/components/chart-frame";
import { DecisionBlock, RuleChecklist, TriggerLines } from "@/components/decision";
import { TradePlanCard } from "@/components/trade-plan";
import { PriceAlertsCard } from "@/components/price-alerts";
import { ChartToggleBar, DEFAULT_TOGGLES, PriceChart, type ChartToggles, type FocusPlan } from "@/components/price-chart";
import { DataStateBadge, DirectionBadge, Empty, ErrorBox, SetupStatusBadge, StrategyLabel, TrendText } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, NativeSelect } from "@/components/ui/form";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { nearestActive, ZoneList, ZoneRange } from "@/components/zones";
import { api } from "@/lib/api";
import { changeClass, fmtNum, fmtPct, fmtPrice, fmtTime, fmtUsd, timeAgo, fmtClock, fmtDay } from "@/lib/format";
import { useTicker } from "@/lib/live";
import { TIMEFRAMES, type Setup, type SetupResponse, type Swing } from "@/lib/types";

/** Newest first, with the date once as a heading and "15:15 · 81,181.10" beneath it. */
function SwingList({ swings }: { swings: Swing[] }) {
  const days = new Map<string, Swing[]>();
  for (const s of swings.slice().reverse()) {
    const day = fmtDay(new Date(s.time * 1000).toISOString());
    days.set(day, [...(days.get(day) ?? []), s]);
  }
  return (
    <div className="space-y-2 text-xs" data-testid="swings">
      {[...days.entries()].map(([day, items]) => (
        <div key={day}>
          <div className="mb-0.5 text-[11px] font-medium text-muted-foreground">{day}</div>
          <div className="grid grid-cols-2 gap-x-3">
            {items.map((s) => {
              const iso = new Date(s.time * 1000).toISOString();
              return (
                <div key={`${s.kind}-${s.time}`} className="flex justify-between gap-2 whitespace-nowrap">
                  <span className={s.kind === "high" ? "text-bear" : "text-bull"}>{s.kind === "high" ? "High" : "Low"}</span>
                  <span className="tabular"><Time iso={iso} text={fmtClock(iso)} /> · {fmtPrice(s.price)}</span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function Row({ label, value, sub, className }: { label: React.ReactNode; value: React.ReactNode; sub?: React.ReactNode; className?: string }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className={`text-right tabular ${className ?? ""}`}>
        {value}
        {sub && <span className="block text-[11px] text-muted-foreground">{sub}</span>}
      </span>
    </div>
  );
}

const qty = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: 8 }));

function planOf(s: Setup): FocusPlan {
  const p = s.plan;
  return {
    direction: s.direction,
    label: `${s.direction} ${s.strategy}${s.status === "REJECTED" ? " rejected" : ""}`,
    entry: s.entry_reference,
    stop: s.stop_loss,
    targets: (p.targets ?? []).map((t) => ({ label: t.label, price: t.price, blocked: t.blocked })),
    confirmationTime: p.confirmation_candle ? Math.floor(new Date(p.confirmation_candle.open_time).getTime() / 1000) : null,
    invalidation: p.zone && s.status !== "WATCHLIST" ? (s.direction === "LONG" ? p.zone.lower : p.zone.upper) : null,
    entryZone: p.entry_zone,
  };
}

function SetupCard({ s, base }: { s: Setup; base: string }) {
  const p = s.plan;
  const triggered = s.status !== "WATCHLIST";
  const rejected = s.status === "REJECTED";
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <DirectionBadge direction={s.direction} /> <StrategyLabel strategy={s.strategy} />
          </span>
          <SetupStatusBadge status={s.status} />
        </CardTitle>
        <CardDescription>
          {triggered ? `Confirmation candle closed ${fmtTime(p.confirmation_candle?.close_time)} · score ${s.score}/100 (rule agreement, not a win probability)` : "Setup developing; no trade plan until the trigger is confirmed."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {s.status === "QUALIFIED" && (
          <div className="rounded-md border border-bull/40 bg-bull/10 px-3 py-2 text-xs text-bull">
            ✅ Trade signal. The scanner stores it on Trading Signals and sends it to Telegram (for watchlist coins on
            scanned timeframes).
          </div>
        )}
        {rejected && (
          <div className="rounded-md border border-warn/50 bg-warn/15 px-3 py-2 text-xs font-medium text-warn">
            ⛔ NOT A TRADE SIGNAL: this setup failed the rules below, so it is not stored and no Telegram alert is sent.
            The levels are shown greyed out only to explain the decision. Do not trade them.
          </div>
        )}
        {s.status === "WATCHLIST" && (
          <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            👀 Not a signal yet: waiting for the trigger candle. You will get a Telegram message only if it qualifies.
          </div>
        )}
        {s.reasons.length > 0 && (
          <div className={`space-y-1 rounded-md border px-3 py-2 text-xs ${s.status === "REJECTED" ? "border-warn/40 bg-warn/10" : "border-border bg-muted/30"}`}>
            {s.reasons.map((r) => (
              <div key={r.code} className="flex gap-1.5">
                {s.status === "REJECTED" ? <X className="mt-px h-3.5 w-3.5 shrink-0 text-warn" /> : <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                <span>{r.message} <code className="text-[10px] text-muted-foreground">{r.code}</code></span>
              </div>
            ))}
          </div>
        )}
        {triggered && (
          <div className={`divide-y divide-border/60 ${rejected ? "opacity-75" : ""}`} title={rejected ? "Rejected setup: not a trade signal" : undefined}>
            <Row label="Entry reference" value={fmtPrice(s.entry_reference)} sub="confirmation close; hypothetical fill at next open" />
            {p.entry_zone && <Row label="Entry zone" value={`${fmtPrice(p.entry_zone[0])} – ${fmtPrice(p.entry_zone[1])}`} />}
            {p.zone && <Row label={s.strategy === "breakout" ? "Broken zone" : "Retested zone"} value={<ZoneRange zone={p.zone} />} sub={`${p.zone.timeframe} · ${p.zone.tests} tests · strength ${Math.round(p.zone.strength)}`} />}
            <Row label={`Stop-loss (${p.stop?.method ?? "—"})`} value={fmtPrice(s.stop_loss)} className="text-bear"
              sub={p.stop ? `${fmtPrice(p.stop.distance)} away · ${fmtNum(p.stop.distance_atr, 2)} ATR${p.stop.widened ? " · widened" : ""}` : undefined} />
            {p.targets.map((t, k) => (
              <Row key={t.label} label={`${t.label} (${t.r_multiple}R${p.partial_exits ? ` · ${t.allocation_pct}%` : ""})${k === p.primary_target_index ? " · primary" : ""}`}
                value={fmtPrice(t.price)} className={t.blocked ? "text-warn" : "text-bull"} sub={t.note ?? undefined} />
            ))}
            <Row label="Risk / reward (primary)" value={s.risk_reward != null ? `1:${fmtNum(s.risk_reward, 2)}` : "—"}
              sub={p.net_risk_reward != null ? `≈ 1:${fmtNum(p.net_risk_reward, 2)} after est. fees & slippage` : undefined} />
            <Row label="Nearest opposing zone" value={p.opposing_zone ? <ZoneRange zone={p.opposing_zone} /> : "none in range"}
              sub={p.room_r != null ? `${fmtNum(p.room_r, 2)}R of room (${p.opposing_zone?.timeframe})` : undefined} />
          </div>
        )}
        {p.stop && <p className="text-xs text-muted-foreground">{p.stop.reasoning}</p>}
        <div>
          <div className="mb-1 text-xs font-semibold">Technical reasoning</div>
          <p className="text-xs">{s.explanation}</p>
        </div>
        <div>
          <div className="mb-1 text-xs font-semibold">Conditions</div>
          <ul className="space-y-1 text-xs">
            {s.conditions.map((c) => (
              <li key={c.key} className="flex items-start gap-1.5">
                {c.passed ? <Check className="mt-px h-3.5 w-3.5 shrink-0 text-bull" /> : <X className="mt-px h-3.5 w-3.5 shrink-0 text-bear" />}
                <span>{c.label} <span className="text-muted-foreground">· {c.detail}</span></span>
              </li>
            ))}
          </ul>
        </div>
        {p.invalidation.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-semibold">Invalidation</div>
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">{p.invalidation.map((x) => <li key={x}>{x}</li>)}</ul>
          </div>
        )}
        {p.warnings.length > 0 && (
          <ul className="space-y-0.5 text-xs text-warn">{p.warnings.map((w) => <li key={w}>⚠ {w}</li>)}</ul>
        )}
        {p.sizing && !rejected && <SizingBlock s={s} base={base} />}
      </CardContent>
    </Card>
  );
}

function SizingBlock({ s, base }: { s: Setup; base: string }) {
  const z = s.plan.sizing!;
  return (
    <div className="rounded-md border border-border p-3">
      <div className="mb-1 flex items-center justify-between text-xs font-semibold">
        Position calculator
        {z.feasible ? <Badge variant="bull">feasible</Badge> : <Badge variant="warn">not feasible</Badge>}
      </div>
      <div className="divide-y divide-border/60">
        <Row label="Suggested quantity" value={`${qty(z.quantity)} ${base}`} sub={z.rules ? `step ${z.rules.qty_step}, min ${z.rules.min_qty}` : "no exchange rules (not rounded)"} />
        <Row label="Position notional" value={fmtUsd(z.notional)} />
        <Row label="Estimated margin" value={fmtUsd(z.initial_margin)} sub={`at ${z.leverage}x`} />
        <Row label="Risk budget" value={fmtUsd(z.risk_budget)} />
        <Row label="Planned price risk" value={fmtUsd(z.price_risk)} sub="quantity × |entry − stop|" />
        <Row label="Est. loss at stop" value={fmtUsd(z.estimated_loss)} className="text-bear" sub="incl. fees and adverse slippage" />
        <Row label="Est. liquidation" value={fmtPrice(z.liquidation_price)} sub={z.max_safe_leverage ? `liquidation passes the stop above ≈${fmtNum(z.max_safe_leverage, 1)}x` : undefined} />
        {z.targets.map((t) => (
          <Row key={t.label} label={`Profit at ${t.label}`} value={fmtUsd(t.net_profit)} className="text-bull" sub={`gross ${fmtUsd(t.gross_profit)} − fees ${fmtUsd(t.fees)} · ${fmtNum(t.net_rr, 2)}R net`} />
        ))}
        {s.plan.partial_exits && z.weighted_net_profit != null && <Row label="Weighted (partial exits)" value={fmtUsd(z.weighted_net_profit)} />}
      </div>
      {z.issues.map((i) => <p key={i.code} className="mt-1 text-xs text-warn">{i.message}</p>)}
      <p className="mt-2 text-[11px] text-muted-foreground">
        Planned risk is not a guaranteed maximum loss: gaps, slippage beyond the estimate, liquidation and operational failures can cause larger losses.
      </p>
    </div>
  );
}

function SetupPageInner() {
  const params = useParams<{ symbol: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const symbol = (params.symbol || "BTCUSDT").toUpperCase();
  const tf = search.get("tf") && TIMEFRAMES.includes(search.get("tf") as never) ? search.get("tf")! : "15m";
  const [toggles, setToggles] = useState<ChartToggles>(DEFAULT_TOGGLES);
  const [form, setForm] = useState<{ account_balance?: string; risk_pct?: string; leverage?: string }>({});
  const [applied, setApplied] = useState<{ account_balance?: number; risk_pct?: number; leverage?: number }>({});
  const [selected, setSelected] = useState(0);

  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const setup = useQuery({
    queryKey: ["setup", symbol, tf, applied],
    queryFn: () => api.setup(symbol, tf, applied),
    refetchInterval: 30_000,
    retry: false,
    // Keep the page in place while sizing inputs recalculate; a different market starts clean.
    placeholderData: (prev, q) => (q?.queryKey[1] === symbol && q?.queryKey[2] === tf ? prev : undefined),
  });
  const candles = useQuery({ queryKey: ["candles", symbol, tf], queryFn: () => api.candles(symbol, tf, 400), refetchInterval: 60_000 });
  const data: SetupResponse | undefined = setup.data;
  const ticker = useTicker(symbol, data?.market.ticker ?? candles.data?.ticker);
  const setups = data?.setups ?? [];
  const current = setups[Math.min(selected, Math.max(0, setups.length - 1))];
  const focusPlan = useMemo(() => (current ? planOf(current) : null), [current]);
  const watchlist = settings.data?.settings.watchlist ?? [];
  const risk = settings.data?.settings.risk;
  const base = data?.instrument?.base_coin || symbol.replace(/USDT$/, "");
  const go = (sym: string, timeframe: string) => {
    setSelected(0);
    router.push(`/setup/${sym}?tf=${timeframe}`);
  };
  const maxLeverage = risk?.max_leverage;
  const leverageTooHigh = maxLeverage != null && Number(form.leverage) > maxLeverage;
  // Recalculate shortly after typing stops.
  useEffect(() => {
    if (leverageTooHigh) return;
    const num = (v?: string) => (v && Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : undefined);
    const t = setTimeout(() => setApplied({ account_balance: num(form.account_balance), risk_pct: num(form.risk_pct), leverage: num(form.leverage) }), 500);
    return () => clearTimeout(t);
  }, [form, leverageTooHigh]);
  const m = data?.market;
  const st = data?.structure;
  const livePrice = ticker?.available ? ticker.last_price : m?.close;
  const nearestSupport = nearestActive(st?.zones, "SUPPORT");
  const nearestResistance = nearestActive(st?.zones, "RESISTANCE");

  return (
    <>
      <PageHeader
        title={`Trade setup · ${symbol}`}
        description="Latest closed-candle analysis: support/resistance zones, breakout and pullback setups, stop-loss, targets and position size. Research only; nothing is executed."
        actions={
          <>
            <NativeSelect value={symbol} onChange={(e) => go(e.target.value, tf)} className="h-8 w-32" aria-label="Symbol">
              {[...new Set([symbol, ...watchlist])].map((s) => <option key={s}>{s}</option>)}
            </NativeSelect>
            <Tabs value={tf} onValueChange={(v) => go(symbol, v)}>
              <TabsList>{TIMEFRAMES.map((t) => <TabsTrigger key={t} value={t}>{t}</TabsTrigger>)}</TabsList>
            </Tabs>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-stretch gap-4">
        <DecisionBlock data={data} timeframe={tf} />
        <div className="flex min-w-60 flex-1 flex-col justify-center gap-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            {ticker?.available ? (
              <>
                <span className="tabular text-3xl font-semibold">{fmtPrice(ticker.last_price)}</span>
                <span className={`tabular ${changeClass(ticker.change_24h_pct)}`}>{fmtPct(ticker.change_24h_pct)} 24h</span>
                <DataStateBadge ticker={ticker} />
              </>
            ) : m ? (
              <>
                <span className="tabular text-3xl font-semibold">{fmtPrice(m.close)}</span>
                <Badge variant="outline" title="Live prices are streamed for watchlist symbols only">last close · not streamed</Badge>
              </>
            ) : (
              <span className="tabular text-3xl font-semibold">—</span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
            <span className="text-muted-foreground">Trend <TrendText value={m?.trend} /></span>
            <span className="text-muted-foreground">Structure <span className="text-foreground">{m?.structure ?? "—"}</span></span>
            <span className="text-muted-foreground">Last closed candle <span className="text-foreground"><Time iso={m?.candle_close_time} /></span></span>
            {ticker?.available && <span className="text-muted-foreground">Last market-data update <span className="text-foreground">{timeAgo(ticker.updated_at)}</span></span>}
            {m && <span className="text-muted-foreground">Candles <span className={m.data_current ? "text-foreground" : "text-warn"}>{m.data_current ? "current" : "not current"}</span></span>}
          </div>
        </div>
      </div>
      {data?.diagnostics && <TriggerLines diagnostics={data.diagnostics} />}
      <ErrorBox error={setup.error} />
      {m && !m.data_current && (
        <div className="mb-3 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">Candle history could not be refreshed from Bybit; analysis may be stale.</div>
      )}
      {m && !m.ready && <div className="mb-3 rounded-md border border-border px-3 py-2 text-sm text-muted-foreground">{m.not_ready_reason}</div>}

      <div className="grid items-start gap-4 xl:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          <ChartFrame
            toolbar={
              <div className="flex flex-wrap items-center gap-4">
                <span className="text-sm font-semibold">{symbol} · {tf}</span>
                <ChartToggleBar toggles={toggles} onChange={setToggles} />
              </div>
            }
          >
            {(full) =>
              candles.data ? (
                <PriceChart data={candles.data} toggles={toggles} focusPlan={focusPlan} height={520} fill={full} />
              ) : (
                <div className={`flex ${full ? "h-full" : "h-[520px]"} items-center justify-center text-sm text-muted-foreground`}>{candles.isLoading ? "Loading candles from Bybit…" : "No chart data"}</div>
              )
            }
          </ChartFrame>

          <TradePlanCard key={`${symbol}-${tf}`} symbol={symbol} timeframe={tf} preferred={current ? { direction: current.direction, strategy: current.strategy } : undefined} />

          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader><CardTitle>Price structure</CardTitle><CardDescription>Nearest active zones and higher-timeframe levels.</CardDescription></CardHeader>
              <CardContent className="divide-y divide-border/60">
                <Row label={`Nearest support (${tf})`} value={<ZoneRange zone={nearestSupport} none="none active" />} className="text-bull" sub={nearestSupport ? `${nearestSupport.tests} tests` : undefined} />
                <Row label={`Nearest resistance (${tf})`} value={<ZoneRange zone={nearestResistance} none="none active" />} className="text-bear" sub={nearestResistance ? `${nearestResistance.tests} tests` : undefined} />
                {Object.entries(st?.htf_zones ?? {}).map(([htf, zs]) => (
                  <Row key={htf} label={<span className="whitespace-nowrap">{htf} major S / R</span>} value={<span><ZoneRange zone={nearestActive(zs, "SUPPORT")} none="none active" /> / <ZoneRange zone={nearestActive(zs, "RESISTANCE")} none="none active" /></span>} />
                ))}
                <div className="pt-2">
                  <div className="mb-1 text-xs font-semibold">Recent confirmed swings</div>
                  <SwingList swings={st?.swings ?? []} />
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>All zones</CardTitle><CardDescription>{tf} entry zones plus 1h/4h structure.</CardDescription></CardHeader>
              <CardContent><ZoneList zones={[...(st?.zones ?? []), ...Object.values(st?.htf_zones ?? {}).flat()]} price={livePrice} /></CardContent>
            </Card>
          </div>
        </div>

        <div className="space-y-4">
          <Card data-testid="account-risk">
            <CardHeader><CardTitle>Account &amp; risk</CardTitle><CardDescription>Defaults from Settings. Changes here recalculate automatically for this analysis only.</CardDescription></CardHeader>
            <CardContent>
              <form className="grid grid-cols-3 gap-2" onSubmit={(e) => e.preventDefault()}>
                <Field label="Balance (USDT)"><Input type="number" step="any" placeholder={risk ? String(risk.account_balance) : ""} value={form.account_balance ?? ""} onChange={(e) => setForm({ ...form, account_balance: e.target.value })} /></Field>
                <Field label="Risk %"><Input type="number" step="0.1" placeholder={risk ? String(risk.risk_per_trade_pct) : ""} value={form.risk_pct ?? ""} onChange={(e) => setForm({ ...form, risk_pct: e.target.value })} /></Field>
                <Field label={`Leverage (≤${risk?.max_leverage ?? "?"}x)`}><Input type="number" step="1" placeholder={risk ? String(risk.leverage) : ""} value={form.leverage ?? ""} onChange={(e) => setForm({ ...form, leverage: e.target.value })} /></Field>
              </form>
              {leverageTooHigh && <p role="alert" className="mt-2 text-xs text-warn">Leverage is above your maximum of {maxLeverage}x, so it was not applied.</p>}
              {data && <p className="mt-2 text-[11px] text-muted-foreground" aria-live="polite">{setup.isFetching && !setup.isLoading ? "Recalculating… " : ""}Using {fmtUsd(data.sizing_inputs.account_balance)} · {data.sizing_inputs.risk_pct}% risk · {data.sizing_inputs.leverage}x · fees {data.sizing_inputs.fee_rate_pct}%/side · slippage {data.sizing_inputs.slippage_pct}%.</p>}
            </CardContent>
          </Card>

          {setups.length > 1 && (
            <div className="flex flex-wrap gap-2">
              {setups.map((s, k) => (
                <Button key={`${s.strategy}-${s.direction}`} size="sm" variant={k === selected ? "default" : "outline"} onClick={() => setSelected(k)}>
                  {s.direction} {s.strategy} · {s.status.toLowerCase()}
                </Button>
              ))}
            </div>
          )}
          {current && <SetupCard s={current} base={base} />}
          {data?.diagnostics?.ready ? (
            <RuleChecklist key={`${symbol}-${tf}`} diagnostics={data.diagnostics} closeTime={m?.candle_close_time} preferred={current?.direction} />
          ) : !current && (
            <Card>
              <CardHeader><CardTitle>Rule checklist</CardTitle></CardHeader>
              <CardContent><Empty>{setup.isLoading ? "Analysing…" : m?.not_ready_reason ?? "No analysis available."}</Empty></CardContent>
            </Card>
          )}

          <PriceAlertsCard
            symbol={symbol}
            inWatchlist={watchlist.includes(symbol)}
            price={livePrice}
            support={nearestSupport}
            resistance={nearestResistance}
          />

          {data?.active_signals.length ? (
            <Card>
              <CardHeader><CardTitle>Active stored signals</CardTitle><CardDescription>Qualified signals awaiting their hypothetical outcome.</CardDescription></CardHeader>
              <CardContent className="space-y-1 text-xs">
                {data.active_signals.map((s) => (
                  <Link key={s.id} href={`/market/${s.symbol}?tf=${s.timeframe}&signal=${s.id}`} className="flex justify-between hover:underline">
                    <span>{s.direction} · <StrategyLabel strategy={s.strategy_type} /></span>
                    <span className="text-muted-foreground"><Time iso={s.candle_close_time} /></span>
                  </Link>
                ))}
              </CardContent>
            </Card>
          ) : null}
          {data && <p className="text-[11px] text-muted-foreground">{data.disclaimer}</p>}
        </div>
      </div>
    </>
  );
}

export default function SetupPage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <SetupPageInner />
    </Suspense>
  );
}
