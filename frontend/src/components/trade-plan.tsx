"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { DirectionBadge, ErrorBox, StrategyLabel } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, NativeSelect } from "@/components/ui/form";
import { api } from "@/lib/api";
import { fmtNum, fmtUsd } from "@/lib/format";
import { useTicker } from "@/lib/live";
import type { GateStatus, PlannedSetup, PlanZone, PositionInput, TradePlanResponse } from "@/lib/planning-types";
import type { Ticker } from "@/lib/types";

export const GATE_LABEL: Record<GateStatus, string> = {
  QUALIFIED: "Qualified",
  WAIT_FOR_RETEST: "Wait for retest",
  OVEREXTENDED: "Overextended",
  INSUFFICIENT_REWARD: "Insufficient reward",
  CONFLICTING_TIMEFRAMES: "Conflicting timeframes",
  REJECTED: "Rejected",
};
const FEASIBILITY: Record<string, { label: string; className: string }> = {
  clear: { label: "Clear of zone", className: "text-bull" },
  near_opposing: { label: "Just before zone", className: "text-warn" },
  blocked: { label: "Beyond zone", className: "text-warn" },
  no_opposing_zone: { label: "No zone found", className: "text-muted-foreground" },
  invalid: { label: "Invalid", className: "text-warn" },
};

/** A price at the exchange's tick precision when the rules are known. */
export function fmtPlanPrice(v: number | null | undefined, decimals: number | null): string {
  if (v == null) return "—";
  return decimals == null ? v.toLocaleString("en-US", { maximumFractionDigits: 8 }) : v.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

/** A quantity at the exchange's step precision. It truncates, so display never exceeds the real size. */
export function fmtQuantity(v: number, decimals: number | null): string {
  if (decimals == null) return v.toLocaleString("en-US", { maximumFractionDigits: 8 });
  const f = 10 ** decimals;
  return (Math.floor(v * f + 1e-9) / f).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

/** A positive number typed by the user, or null. Empty means "use the default". */
export function readPositive(v: string): number | null {
  const n = Number(v);
  return v.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
}

/** Three increasing multiples are required; anything else keeps the last valid set. */
export function readMultiples(values: string[]): number[] | null {
  const nums = values.map(readPositive);
  if (nums.some((x) => x == null || x > 20)) return null;
  const list = nums as number[];
  return list.every((x, k) => k === 0 || x > list[k - 1]) ? list : null;
}

function Fact({ label, value, sub, className }: { label: string; value: React.ReactNode; sub?: React.ReactNode; className?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className={`tabular text-sm font-medium ${className ?? ""}`}>{value}</dd>
      {sub && <dd className="text-[11px] text-muted-foreground">{sub}</dd>}
    </div>
  );
}

const zoneText = (z: PlanZone | null, d: number | null) => (z ? `${fmtPlanPrice(z.lower, d)} – ${fmtPlanPrice(z.upper, d)}` : "None active");

function ScrollTable({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={label}>
      <table className="w-full min-w-160 text-xs">{children}</table>
    </div>
  );
}
const th = "whitespace-nowrap border-b border-border px-2 py-1.5 text-left font-medium text-muted-foreground";
const td = "whitespace-nowrap border-b border-border/50 px-2 py-1.5 tabular";

function Reasoning({ s }: { s: PlannedSetup }) {
  const r = s.reasoning;
  return (
    <div className="grid gap-3 text-xs md:grid-cols-2">
      <div>
        <div className="mb-1 font-semibold">Technical evidence</div>
        <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">{r.technical_evidence.map((x) => <li key={x}>{x}</li>)}</ul>
      </div>
      <div>
        <div className="mb-1 font-semibold">Entry conditions</div>
        <ul className="space-y-0.5">
          {s.gate.checks.map((c) => (
            <li key={c.key} className="flex gap-1.5">
              {c.passed ? <Check className="mt-px h-3.5 w-3.5 shrink-0 text-bull" aria-label="Pass" /> : <X className="mt-px h-3.5 w-3.5 shrink-0 text-warn" aria-label="Fail" />}
              <span>{c.label} <span className="text-muted-foreground">· {c.detail}</span></span>
            </li>
          ))}
        </ul>
      </div>
      <div><div className="mb-1 font-semibold">Stop-loss justification</div><p className="text-muted-foreground">{r.stop_justification}</p></div>
      <div><div className="mb-1 font-semibold">Take-profit feasibility</div><p className="text-muted-foreground">{r.take_profit_feasibility} {r.net_risk_reward}</p></div>
      <div><div className="mb-1 font-semibold">Correlation exposure</div><p className="text-muted-foreground">{r.correlation_exposure}</p></div>
      {s.invalidation.length > 0 && (
        <div>
          <div className="mb-1 font-semibold">Invalidation</div>
          <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">{s.invalidation.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

function SetupPlan({ s, data, ticker, equity, riskPct, leverage }: { s: PlannedSetup; data: TradePlanResponse; ticker: Ticker | null | undefined; equity: number; riskPct: number; leverage: number }) {
  const d = data.price_decimals;
  const stop = s.stop;
  const def = data.defaults;
  const valid = s.targets.filter((t) => t.valid && t.price != null);
  const rejected = s.production_status === "REJECTED";
  const body: PositionInput | null = stop.valid && stop.price != null && !rejected ? {
    symbol: data.symbol, direction: s.direction, equity, risk_pct: riskPct, entry: s.entry_reference, stop: stop.price, leverage,
    entry_fee_pct: def.entry_fee_pct, exit_fee_pct: def.exit_fee_pct, entry_slippage_pct: def.entry_slippage_pct, stop_slippage_pct: def.stop_slippage_pct,
    maintenance_margin_pct: def.maintenance_margin_pct, open_risk_usdt: def.open_risk_usdt, max_open_risk_pct: def.max_open_risk_pct,
    targets: valid.map((t) => ({ label: t.label, price: t.price as number })),
  } : null;
  const pos = useQuery({
    queryKey: ["position-plan", body],
    queryFn: () => api.positionPlan(body as PositionInput),
    enabled: body != null,
    placeholderData: keepPreviousData,
  });
  const z = body ? pos.data : undefined;
  const live = ticker?.available ? ticker.last_price : data.ticker.available ? data.ticker.last_price : null;
  const tp2 = z?.targets[Math.min(1, (z?.targets.length ?? 1) - 1)];
  return (
    <div className="space-y-4">
      {rejected ? (
        <div className="rounded-md border border-warn/50 bg-warn/15 px-3 py-2 text-xs font-medium text-warn">
          Not a trade signal: the production rules rejected this setup. The levels are shown only to explain why, so no quantity is suggested.
        </div>
      ) : s.gate.status !== "QUALIFIED" && (
        <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs">
          <span className="font-medium text-warn">{GATE_LABEL[s.gate.status]}.</span> The experimental gate would skip this setup. The live signal and strategies A, B and C aren&apos;t affected.
          {s.evidence.gate_support && <span className="mt-1 block text-muted-foreground" data-testid="plan-gate-support">{s.evidence.gate_support}</span>}
        </div>
      )}
      {s.gate.reasons.length > 0 && (
        <ul className="space-y-0.5 text-xs" data-testid="plan-rejections">
          {s.gate.reasons.map((r) => <li key={r.code} className="flex gap-1.5"><X className="mt-px h-3.5 w-3.5 shrink-0 text-warn" aria-hidden /> {r.message}</li>)}
        </ul>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
        <Fact label="Symbol" value={data.symbol} />
        <Fact label="Direction" value={<DirectionBadge direction={s.direction} />} />
        <Fact label="Strategy" value={<StrategyLabel strategy={s.strategy} />} sub={s.research_version} />
        <Fact label="Setup status" value={GATE_LABEL[s.gate.status]} className={s.gate.status === "QUALIFIED" ? "text-bull" : "text-warn"} sub={rejected ? "Production: rejected" : "Production: qualified"} />
        <Fact label="Current price" value={fmtPlanPrice(live, d)} />
        <Fact label="Suggested entry reference" value={fmtPlanPrice(s.entry_reference, d)} sub="Confirmation close" />
        <Fact label="Entry zone" value={s.entry_zone ? `${fmtPlanPrice(s.entry_zone[0], d)} – ${fmtPlanPrice(s.entry_zone[1], d)}` : "—"} />
        <Fact label="Stop-loss" value={stop.valid ? fmtPlanPrice(stop.price, d) : "No valid stop"} className="text-bear"
          sub={stop.valid ? `${fmtNum(stop.distance_pct, 2)}% · ${fmtNum(stop.distance_atr, 2)} ATR` : undefined} />
        <Fact label="Support zone" value={zoneText(data.support, d)} />
        <Fact label="Resistance zone" value={zoneText(data.resistance, d)} />
      </dl>

      {!stop.valid && <p className="text-xs text-warn">{stop.justification}</p>}

      {valid.length > 0 && (
        <ScrollTable label="Take-profit candidates">
          <thead><tr>{["Target", "Price", "Move", "ATR", "Gross R", "Net R", "Net profit", "Against structure"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
          <tbody>
            {s.targets.map((t) => {
              const money = z?.targets.find((x) => x.label === t.label);
              return (
                <tr key={t.label}>
                  <td className={td}>{t.label} ({t.r_multiple}R)</td>
                  <td className={td}>{fmtPlanPrice(t.price, d)}</td>
                  <td className={td}>{t.distance_pct != null ? `${fmtNum(t.distance_pct, 2)}%` : "—"}</td>
                  <td className={td}>{fmtNum(t.distance_atr, 2)}</td>
                  <td className={td}>{fmtNum(t.gross_r, 2)}</td>
                  <td className={td}>{fmtNum(t.net_r, 2)}</td>
                  <td className={`${td} text-bull`}>{money ? fmtUsd(money.net_profit) : "—"}</td>
                  <td className={`${td} ${FEASIBILITY[t.feasibility]?.className ?? ""}`} title={t.note ?? undefined}>{FEASIBILITY[t.feasibility]?.label}</td>
                </tr>
              );
            })}
          </tbody>
        </ScrollTable>
      )}

      {body && (
        <div data-testid="plan-position">
          <div className="mb-1 flex items-center gap-2 text-xs font-semibold">
            Position
            {z && (z.feasible ? <Badge variant="bull">Feasible</Badge> : <Badge variant="warn">Refused</Badge>)}
            {pos.isFetching && <span className="font-normal text-muted-foreground" aria-live="polite">Recalculating…</span>}
          </div>
          <ErrorBox error={pos.error} />
          {z && (
            <>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
                <Fact label="Suggested quantity" value={`${fmtQuantity(z.quantity, z.quantity_decimals)} ${z.base_coin ?? ""}`} sub={z.min_order_quantity != null ? `Exchange minimum ${z.min_order_quantity}` : undefined} />
                <Fact label="Position notional" value={fmtUsd(z.notional)} sub={`${fmtNum(z.effective_exposure_x, 2)}x of equity`} />
                <Fact label="Estimated margin" value={fmtUsd(z.initial_margin)} sub={`At ${fmtNum(z.leverage, 0)}x, as entered`} />
                <Fact label="Risk budget" value={fmtUsd(z.risk_budget)} sub={`${z.risk_pct}% of equity`} />
                <Fact label="Planned loss at stop" value={fmtUsd(z.planned_loss)} className="text-bear" sub={`${fmtNum(z.planned_loss_pct_of_equity, 2)}% of equity, with costs`} />
                <Fact label="Potential gross profit" value={tp2 ? fmtUsd(tp2.gross_profit) : "—"} sub={tp2 ? `At ${tp2.label}` : undefined} />
                <Fact label="Potential net profit" value={tp2 ? fmtUsd(tp2.net_profit) : "—"} className="text-bull" sub={tp2 ? `At ${tp2.label}, after fees` : undefined} />
                <Fact label="Net risk / reward" value={tp2?.net_rr != null ? `1 : ${fmtNum(tp2.net_rr, 2)}` : "—"} sub={tp2 ? `At ${tp2.label}` : undefined} />
                <Fact label="Estimated liquidation" value={fmtPlanPrice(z.liquidation_price, d)} sub={z.max_safe_leverage ? `Stop comes first below ≈${fmtNum(z.max_safe_leverage, 1)}x` : undefined} />
                <Fact label="If the stop gaps" value={z.stop_gap_stress.length ? fmtUsd(z.stop_gap_stress[z.stop_gap_stress.length - 1].loss) : "—"} className="text-bear" sub={z.stop_gap_stress.length ? `Filled ${z.stop_gap_stress[z.stop_gap_stress.length - 1].gap_pct}% past the stop` : undefined} />
              </dl>
              {z.issues.map((i) => <p key={i.code} role="alert" className="mt-2 text-xs text-warn">{i.message}</p>)}
              {z.warnings.map((w) => <p key={w} className="mt-1 text-xs text-warn">{w}</p>)}
              <p className="mt-2 text-[11px] text-muted-foreground">{z.note}</p>
            </>
          )}
        </div>
      )}

      {s.structural_levels.length > 0 && (
        <ScrollTable label="Structural reference levels">
          <thead><tr>{["Structural level", "Price", "Move", "ATR", "Gross R", "Net R"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
          <tbody>
            {s.structural_levels.map((l) => (
              <tr key={l.kind}>
                <td className={td}>{l.label}</td><td className={td}>{fmtPlanPrice(l.price, d)}</td><td className={td}>{fmtNum(l.distance_pct, 2)}%</td>
                <td className={td}>{fmtNum(l.distance_atr, 2)}</td><td className={td}>{fmtNum(l.gross_r, 2)}</td><td className={td}>{fmtNum(l.net_r, 2)}</td>
              </tr>
            ))}
          </tbody>
        </ScrollTable>
      )}

      <ScrollTable label="Stop methods compared">
        <thead><tr>{["Stop method", "Stop", "Distance", "ATR", "Room", "Net at 2R", "Entry check"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>
          {s.stop_variants.map((v) => (
            <tr key={v.method} className={v.method === s.stop_method ? "bg-muted/40" : undefined}>
              <td className={td} title={v.justification}>{v.label}</td>
              <td className={td}>{v.valid ? fmtPlanPrice(v.price, d) : "Refused"}</td>
              <td className={td}>{v.valid ? `${fmtNum(v.distance_pct, 2)}%` : "—"}</td>
              <td className={td}>{v.valid ? fmtNum(v.distance_atr, 2) : "—"}</td>
              <td className={td}>{v.room_r != null ? `${fmtNum(v.room_r, 2)}R` : "—"}</td>
              <td className={td}>{v.net_r_at_primary != null ? `${fmtNum(v.net_r_at_primary, 2)}R` : "—"}</td>
              <td className={td}>{v.gate_status ? GATE_LABEL[v.gate_status] : "—"}</td>
            </tr>
          ))}
        </tbody>
      </ScrollTable>

      <Reasoning s={s} />

      <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs" data-testid="plan-evidence">
        <Badge variant="outline">{s.evidence.status}</Badge> <span className="text-muted-foreground">{s.evidence.note}</span>
        {s.evidence.historical && (
          <p className="mt-1 text-muted-foreground">
            {s.evidence.historical.label}: {s.evidence.historical.trades} trades, average {fmtNum(s.evidence.historical.avg_net_r, 3)}R net, {fmtNum(s.evidence.historical.win_rate_pct, 1)}% wins, {fmtNum(s.evidence.historical.stop_out_pct, 0)}% stopped out.
          </p>
        )}
      </div>
    </div>
  );
}

export function TradePlanCard({ symbol, timeframe, preferred }: { symbol: string; timeframe: string; preferred?: { direction: string; strategy: string } }) {
  const [method, setMethod] = useState("A_original");
  const [tpText, setTpText] = useState(["1", "2", "3"]);
  const [tp, setTp] = useState([1, 2, 3]);
  const [equityText, setEquityText] = useState("");
  const [riskText, setRiskText] = useState("");
  const [levText, setLevText] = useState("");
  const [picked, setPicked] = useState<number | null>(null);
  useEffect(() => {
    const next = readMultiples(tpText);
    if (next) setTp(next);
  }, [tpText]);
  const plan = useQuery({
    queryKey: ["trade-plan", symbol, timeframe, method, tp],
    queryFn: () => api.tradePlan(symbol, timeframe, method, tp),
    refetchInterval: 30_000,
    retry: false,
    placeholderData: (prev, q) => (q?.queryKey[1] === symbol && q?.queryKey[2] === timeframe ? prev : undefined),
  });
  const ticker = useTicker(symbol, undefined);
  const data = plan.data;
  const def = data?.defaults;
  const setups = useMemo(() => data?.setups ?? [], [data]);
  const preferredIndex = setups.findIndex((s) => s.direction === preferred?.direction && s.strategy === preferred?.strategy);
  const index = picked != null && picked < setups.length ? picked : preferredIndex >= 0 ? preferredIndex : 0;
  const current = setups[index];
  const tpInvalid = !readMultiples(tpText);
  const levOver = def != null && readPositive(levText) != null && (readPositive(levText) as number) > def.max_leverage;
  const riskOver = readPositive(riskText) != null && (readPositive(riskText) as number) > 10;
  const equity = readPositive(equityText) ?? def?.equity ?? 0;
  const riskPct = riskOver ? def?.risk_pct ?? 0 : readPositive(riskText) ?? def?.risk_pct ?? 0;
  const leverage = levOver ? def?.leverage ?? 1 : Math.max(1, readPositive(levText) ?? def?.leverage ?? 1);
  return (
    <Card data-testid="trade-plan">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Trade plan <Badge variant="outline">Experimental</Badge>
          {current && <Badge variant={current.gate.status === "QUALIFIED" ? "bull" : "warn"}>{GATE_LABEL[current.gate.status]}</Badge>}
        </CardTitle>
        <CardDescription>
          {data?.label ?? "Experimental research plan. Nothing is executed."} Defaults use the paper account{def ? ` (${fmtUsd(def.equity)}, ${def.risk_pct}% risk)` : ""}. Figures update as you type.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7" onSubmit={(e) => e.preventDefault()}>
          <Field label="Equity (USDT)"><Input type="number" min="0" step="any" placeholder={def ? String(def.equity) : ""} value={equityText} onChange={(e) => setEquityText(e.target.value)} /></Field>
          <Field label="Risk %"><Input type="number" min="0" step="0.1" placeholder={def ? String(def.risk_pct) : ""} value={riskText} onChange={(e) => setRiskText(e.target.value)} /></Field>
          <Field label={`Leverage (≤${def?.max_leverage ?? "?"}x)`}><Input type="number" min="1" step="1" placeholder={def ? String(def.leverage) : ""} value={levText} onChange={(e) => setLevText(e.target.value)} /></Field>
          <Field label="Stop method">
            <NativeSelect value={method} onChange={(e) => setMethod(e.target.value)}>
              {Object.entries(data?.stop_methods ?? { A_original: "Original production stop" }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </NativeSelect>
          </Field>
          {[0, 1, 2].map((k) => (
            <Field key={k} label={`TP${k + 1} (R)`}>
              <Input type="number" min="0" step="0.5" value={tpText[k]} onChange={(e) => setTpText(tpText.map((x, j) => (j === k ? e.target.value : x)))} />
            </Field>
          ))}
        </form>
        {tpInvalid && <p role="alert" className="text-xs text-warn">Targets must increase and stay between 0 and 20R. The last valid set is still in use.</p>}
        {levOver && <p role="alert" className="text-xs text-warn">Leverage is above your maximum of {def?.max_leverage}x, so it wasn&apos;t applied.</p>}
        {riskOver && <p role="alert" className="text-xs text-warn">Risk above 10% isn&apos;t accepted, so it wasn&apos;t applied.</p>}
        <ErrorBox error={plan.error} />
        {setups.length > 1 && (
          <div className="flex flex-wrap gap-2 text-xs">
            {setups.map((s, k) => (
              <button key={`${s.strategy}-${s.direction}`} type="button" aria-pressed={k === index} onClick={() => setPicked(k)}
                className={`rounded-md border px-2 py-1 ${k === index ? "border-primary bg-primary/10" : "border-border"}`}>
                {s.direction} {s.strategy} · {GATE_LABEL[s.gate.status]}
              </button>
            ))}
          </div>
        )}
        {data && current ? (
          <SetupPlan s={current} data={data} ticker={ticker} equity={equity} riskPct={riskPct} leverage={leverage} />
        ) : data ? (
          <p className="text-xs text-muted-foreground" data-testid="plan-empty">
            {data.ready ? "No confirmed setup on the last closed candle, so there is no plan. A plan appears only when the engine finds a setup; none is forced." : data.not_ready_reason}
          </p>
        ) : plan.isLoading ? <p className="text-xs text-muted-foreground">Building the plan…</p> : null}
      </CardContent>
    </Card>
  );
}
