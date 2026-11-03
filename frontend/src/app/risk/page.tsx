"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { PageHeader } from "@/components/app-shell";
import { Empty, ErrorBox } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, NativeSelect, Switch } from "@/components/ui/form";
import { api } from "@/lib/api";
import { fmtNum, fmtPct, fmtPrice, fmtUsd } from "@/lib/format";
import type { Direction } from "@/lib/types";

function Row({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="flex justify-between border-b py-1.5 last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className={`tabular font-medium ${className ?? ""}`}>{value}</span>
    </div>
  );
}

function RiskCalculator() {
  const search = useSearchParams();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const active = useQuery({ queryKey: ["signals", "active"], queryFn: () => api.signals({ status: "active", limit: 100 }) });
  const [f, setF] = useState({
    account_balance: "10000",
    risk_pct: "1",
    direction: "LONG" as Direction,
    entry_price: "",
    stop_loss: "",
    take_profit: "",
    tp1: "",
    tp2: "",
    tp3: "",
    fee_rate_pct: "0.055",
    slippage_pct: "0.02",
    leverage: "5",
    symbol: "BTCUSDT",
    include_fees_in_sizing: true,
  });
  const [loadedDefaults, setLoadedDefaults] = useState(false);

  useEffect(() => {
    const r = settings.data?.settings.risk;
    if (r && !loadedDefaults) {
      setF((x) => ({
        ...x,
        account_balance: String(r.account_balance),
        risk_pct: String(r.risk_per_trade_pct),
        fee_rate_pct: String(r.fee_rate_pct),
        slippage_pct: String(r.slippage_pct),
        leverage: String(r.leverage),
      }));
      setLoadedDefaults(true);
    }
  }, [settings.data, loadedDefaults]);

  const calc = useMutation({
    mutationFn: () =>
      api.risk({
        account_balance: Number(f.account_balance),
        risk_pct: Number(f.risk_pct),
        direction: f.direction,
        entry_price: Number(f.entry_price),
        stop_loss: Number(f.stop_loss),
        take_profit: f.take_profit ? Number(f.take_profit) : null,
        take_profits: [f.tp1, f.tp2, f.tp3].filter(Boolean).map(Number),
        fee_rate_pct: Number(f.fee_rate_pct),
        slippage_pct: Number(f.slippage_pct),
        leverage: f.leverage ? Number(f.leverage) : null,
        symbol: f.symbol.trim() ? f.symbol.trim().toUpperCase() : null,
        include_fees_in_sizing: f.include_fees_in_sizing,
      }),
  });

  const applySignal = (id: string) => {
    const s = active.data?.items.find((x) => x.id === id);
    if (!s) return;
    const t = s.plan?.targets ?? [];
    setF((x) => ({
      ...x,
      symbol: s.symbol,
      direction: s.direction,
      entry_price: String(s.entry_reference),
      stop_loss: String(s.stop_loss),
      take_profit: String(s.take_profit),
      tp1: t[0] ? String(t[0].price) : "",
      tp2: t[1] ? String(t[1].price) : "",
      tp3: t[2] ? String(t[2].price) : "",
    }));
  };

  const preselect = search.get("signal");
  useEffect(() => {
    if (preselect && active.data) applySignal(preselect);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preselect, active.data]);

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF((x) => ({ ...x, [k]: e.target.value }));
  const r = calc.data;

  return (
    <>
      <PageHeader title="Risk Calculator" description="Futures position sizing from a fixed account-risk budget, including fees, slippage, exchange quantity rules, margin and liquidation estimates. No orders are placed and leverage is never changed." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Inputs</CardTitle></CardHeader>
          <CardContent>
            <form
              className="grid grid-cols-2 gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                calc.mutate();
              }}
            >
              <Field label="Load levels from active signal" className="col-span-2">
                <NativeSelect defaultValue="" onChange={(e) => applySignal(e.target.value)}>
                  <option value="">— choose a signal —</option>
                  {active.data?.items.map((s) => (
                    <option key={s.id} value={s.id}>{s.symbol} {s.timeframe} {s.direction} @ {fmtPrice(s.entry_reference)}</option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Symbol (Bybit rules)" hint="Rounds quantity to the exchange step and checks minimums. Leave empty to skip."><Input value={f.symbol} onChange={set("symbol")} placeholder="BTCUSDT" /></Field>
              <Field label="Preferred leverage" hint={settings.data ? `Configured maximum ${settings.data.settings.risk.max_leverage}x` : undefined}><Input type="number" step="any" min="1" value={f.leverage} onChange={set("leverage")} /></Field>
              <Field label="Account balance ($)"><Input type="number" step="any" min="0" required value={f.account_balance} onChange={set("account_balance")} /></Field>
              <Field label="Risk per trade (%)"><Input type="number" step="any" min="0" required value={f.risk_pct} onChange={set("risk_pct")} /></Field>
              <Field label="Direction">
                <NativeSelect value={f.direction} onChange={set("direction")}>
                  <option value="LONG">LONG</option>
                  <option value="SHORT">SHORT</option>
                </NativeSelect>
              </Field>
              <Field label="Estimated fee per side (%)"><Input type="number" step="any" min="0" required value={f.fee_rate_pct} onChange={set("fee_rate_pct")} /></Field>
              <Field label="Entry price"><Input type="number" step="any" min="0" required value={f.entry_price} onChange={set("entry_price")} /></Field>
              <Field label="Stop-loss price"><Input type="number" step="any" min="0" required value={f.stop_loss} onChange={set("stop_loss")} /></Field>
              <Field label="Take-profit price (optional)"><Input type="number" step="any" min="0" value={f.take_profit} onChange={set("take_profit")} /></Field>
              <Field label="Estimated slippage (%)"><Input type="number" step="any" min="0" value={f.slippage_pct} onChange={set("slippage_pct")} /></Field>
              <Field label="TP1 / TP2 / TP3 (optional)" className="col-span-2">
                <div className="grid grid-cols-3 gap-2">
                  <Input type="number" step="any" min="0" value={f.tp1} onChange={set("tp1")} placeholder="TP1" />
                  <Input type="number" step="any" min="0" value={f.tp2} onChange={set("tp2")} placeholder="TP2" />
                  <Input type="number" step="any" min="0" value={f.tp3} onChange={set("tp3")} placeholder="TP3" />
                </div>
              </Field>
              <Field label="Include fees in sizing">
                <div className="flex h-9 items-center gap-2 text-xs text-muted-foreground">
                  <Switch checked={f.include_fees_in_sizing} onCheckedChange={(v) => setF((x) => ({ ...x, include_fees_in_sizing: v }))} />
                  Loss at stop incl. fees = risk budget
                </div>
              </Field>
              <div className="col-span-2">
                <ErrorBox error={calc.error} />
                <Button type="submit" className="mt-2 w-full" disabled={calc.isPending}>Calculate</Button>
              </div>
            </form>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Estimated outcome</CardTitle>
            <CardDescription>Estimates, not realized results. Planned risk is not a guaranteed maximum loss.</CardDescription>
          </CardHeader>
          <CardContent className="text-sm">
            {r ? (
              <>
                {!r.feasible && (
                  <div className="mb-2 space-y-0.5 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
                    {r.issues.map((i) => <div key={i.code}>{i.message}</div>)}
                  </div>
                )}
                {r.warnings.map((w) => <p key={w} className="mb-1 text-xs text-warn">⚠ {w}</p>)}
                <Row label="Risk budget" value={fmtUsd(r.max_loss)} />
                <Row label="Suggested quantity" value={`${r.quantity.toLocaleString("en-US", { maximumFractionDigits: 8 })}${r.instrument ? ` ${r.instrument.base_coin}` : ""}`} />
                {r.instrument && <Row label="Exchange rules" value={`step ${r.instrument.qty_step} · min ${r.instrument.min_qty} · min notional $${r.instrument.min_notional} · tick ${r.instrument.tick_size}`} />}
                <Row label="Position notional value" value={fmtUsd(r.notional)} />
                <Row label="Estimated initial margin" value={`${fmtUsd(r.initial_margin)} at ${fmtNum(r.leverage)}x`} />
                <Row label="Estimated liquidation price" value={fmtPrice(r.liquidation_price)} className={r.issues.some((i) => i.code === "liquidation_before_stop") ? "text-bear" : undefined} />
                <Row label="Leverage where liquidation passes the stop" value={r.max_safe_leverage ? `≈${fmtNum(r.max_safe_leverage, 1)}x` : "—"} />
                <Row label="Stop distance" value={`${fmtPrice(r.stop_distance)} (${fmtPct(r.stop_distance_pct, 2, false)})`} />
                <Row label="Estimated entry fee" value={fmtUsd(r.estimated_entry_fee)} />
                <Row label="Estimated loss at stop (gross)" value={fmtUsd(-r.estimated_loss_at_stop_gross)} className="text-bear" />
                <Row label="Estimated loss at stop (incl. fees)" value={fmtUsd(-r.estimated_loss_at_stop_net)} className="text-bear" />
                <Row label="Estimated risk / reward" value={r.risk_reward != null ? `1:${fmtNum(r.risk_reward)}` : "—"} />
                <Row label="Potential gross profit at target" value={fmtUsd(r.potential_gross_profit)} className="text-bull" />
                <Row label="Estimated fees at target" value={fmtUsd(r.estimated_fees_at_target)} />
                <Row label="Potential net profit after fees" value={fmtUsd(r.potential_net_profit)} className="text-bull" />
                <Row label="Net risk / reward after costs" value={r.net_risk_reward != null ? `1:${fmtNum(r.net_risk_reward)}` : "—"} />
                {r.targets.map((t) => (
                  <Row key={t.label} label={`${t.label} ${fmtPrice(t.price)} (${fmtNum(t.r_multiple)}R)`} value={`${fmtUsd(t.net_profit)} net · ${t.net_rr != null ? fmtNum(t.net_rr) : "—"}R`} className="text-bull" />
                ))}
                <p className="mt-3 text-xs text-muted-foreground">{r.note}</p>
              </>
            ) : (
              <Empty>Enter levels and press Calculate.</Empty>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

export default function RiskPage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <RiskCalculator />
    </Suspense>
  );
}
