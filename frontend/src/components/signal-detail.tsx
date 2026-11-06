"use client";

import { Check, X } from "lucide-react";
import Link from "next/link";
import { Time } from "@/components/time";
import { Button } from "@/components/ui/button";
import { fmtNum, fmtPrice, fmtUsd } from "@/lib/format";
import type { Signal } from "@/lib/types";
import { DirectionBadge, EvaluationBadge, NotificationBadge, SetupStatusBadge, StrategyLabel } from "./status";
import { HtfSummary } from "./strategy-d";
import { ZoneRange } from "./zones";

export function ScoreBar({ score }: { score: number }) {
  const color = score >= 75 ? "bg-bull" : score >= 50 ? "bg-primary" : "bg-warn";
  return (
    <div className="flex items-center gap-2" title="Rule-agreement score (0-100). Not a probability of success.">
      <div className="h-1.5 w-16 overflow-hidden rounded bg-muted">
        <div className={`h-full ${color}`} style={{ width: `${score}%` }} />
      </div>
      <span className="tabular text-xs">{score}</span>
    </div>
  );
}

export function SignalDetail({ signal }: { signal: Signal }) {
  const risk = Math.abs(signal.entry_reference - signal.stop_loss);
  const ev = signal.evaluation;
  const plan = signal.plan;
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-semibold">{signal.symbol}</span>
        <DirectionBadge direction={signal.direction} />
        <span className="text-muted-foreground">{signal.timeframe}</span>
        <span className="text-muted-foreground"><StrategyLabel strategy={signal.plan ? signal.strategy_type : null} /></span>
        {signal.setup_status && <SetupStatusBadge status={signal.setup_status} />}
        <EvaluationBadge status={signal.evaluation_status} />
        <NotificationBadge status={signal.notification_status} />
        <Button asChild size="sm" variant="outline" className="ml-auto">
          <Link href={`/market/${signal.symbol}?tf=${signal.timeframe}&signal=${signal.id}`}>View on chart</Link>
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Entry reference" value={fmtPrice(signal.entry_reference)} />
        <Stat label="Stop loss" value={fmtPrice(signal.stop_loss)} className="text-bear" sub={`${fmtNum((risk / signal.entry_reference) * 100)}% away`} />
        <Stat label={plan ? `Primary target (TP${plan.primary_target_index + 1})` : "Take profit"} value={fmtPrice(signal.take_profit)} className="text-bull" />
        <Stat label="Risk / reward" value={`1:${signal.risk_reward}`} sub={`ATR ${fmtPrice(signal.atr)}`} />
      </div>

      {signal.timeframe === "15m" && <HtfSummary symbol={signal.symbol} at={signal.candle_close_time} direction={signal.direction} />}

      {plan && (
        <div className="grid gap-3 rounded-md border p-3 text-xs sm:grid-cols-2">
          <div className="space-y-1">
            {plan.zone && <div><span className="text-muted-foreground">{plan.strategy === "breakout" ? "Broken zone" : "Retested zone"}: </span><ZoneRange zone={plan.zone} /> ({plan.zone.timeframe}, {plan.zone.tests} tests)</div>}
            {plan.stop && <div><span className="text-muted-foreground">Stop ({plan.stop.method}): </span>{plan.stop.reasoning}</div>}
            {plan.targets.map((t) => (
              <div key={t.label} className={t.blocked ? "text-warn" : undefined}>
                <span className="text-muted-foreground">{t.label} ({t.r_multiple}R{plan.partial_exits ? `, ${t.allocation_pct}%` : ""}): </span>
                <span className="tabular">{fmtPrice(t.price)}</span>{t.note ? ` · ${t.note}` : ""}
              </div>
            ))}
            {plan.net_risk_reward != null && <div><span className="text-muted-foreground">Net R:R (primary, est. costs): </span>1:{fmtNum(plan.net_risk_reward, 2)}</div>}
          </div>
          <div className="space-y-1">
            {plan.sizing && (
              <>
                <div><span className="text-muted-foreground">Suggested quantity: </span><span className="tabular">{plan.sizing.quantity.toLocaleString("en-US", { maximumFractionDigits: 8 })} {plan.sizing.rules?.base_coin ?? ""}</span></div>
                <div><span className="text-muted-foreground">Notional / margin: </span>{fmtUsd(plan.sizing.notional)} / {fmtUsd(plan.sizing.initial_margin)} at {plan.sizing.leverage}x</div>
                <div><span className="text-muted-foreground">Planned risk: </span>{fmtUsd(plan.sizing.estimated_loss)} incl. costs (budget {fmtUsd(plan.sizing.risk_budget)})</div>
                <div className="text-muted-foreground">Sized with the account settings at signal time. Not an executed position.</div>
              </>
            )}
            {plan.invalidation.length > 0 && (
              <ul className="list-disc pl-4 text-muted-foreground">{plan.invalidation.map((x) => <li key={x}>{x}</li>)}</ul>
            )}
          </div>
        </div>
      )}

      <p className="rounded-md bg-muted/60 p-3 leading-relaxed">{signal.explanation}</p>

      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Rule checks (all mandatory ones passed)</h4>
          <ul className="space-y-1">
            {signal.conditions.map((c) => (
              <li key={c.key} className="flex items-start gap-2">
                {c.passed ? <Check className="mt-0.5 h-3.5 w-3.5 text-bull" /> : <X className="mt-0.5 h-3.5 w-3.5 text-bear" />}
                <span>
                  {c.label} <span className="text-muted-foreground">({c.detail})</span>
                </span>
              </li>
            ))}
          </ul>
          <h4 className="mb-2 mt-4 text-xs font-semibold uppercase text-muted-foreground">Higher timeframes</h4>
          {signal.mtf_details.length === 0 ? (
            <p className="text-muted-foreground">Not evaluated.</p>
          ) : (
            <ul className="space-y-1">
              {signal.mtf_details.map((h) => (
                <li key={h.timeframe}>
                  {h.timeframe}: <span className={h.trend === "BULLISH" ? "text-bull" : h.trend === "BEARISH" ? "text-bear" : "text-muted-foreground"}>{h.trend.toLowerCase()}</span>
                  <span className="text-muted-foreground"> (candle closed <Time iso={h.candle_close_time} />)</span>
                </li>
              ))}
              <li className={signal.mtf_confirmed ? "text-bull" : "text-warn"}>
                {signal.mtf_confirmed ? "Higher-quality: higher timeframes agree" : "Higher timeframes do not fully agree"}
              </li>
            </ul>
          )}
        </div>
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
            Score {signal.score}/100 <span className="normal-case">(rule agreement, not a win probability)</span>
          </h4>
          <ul className="space-y-2">
            {signal.score_breakdown.map((c) => (
              <li key={c.key}>
                <div className="flex justify-between">
                  <span>{c.label}</span>
                  <span className="tabular text-muted-foreground">
                    {c.evaluated ? `${fmtNum(c.points, 1)} / ${c.max_points}` : "not evaluated"}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground">{c.reason}</div>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
        <div>Candle close: <Time iso={signal.candle_close_time} /></div>
        <div>Detected: <Time iso={signal.created_at} seconds /></div>
        <div>Strategy: {signal.strategy_name} v{signal.strategy_version}</div>
        <div className="truncate" title={signal.id}>ID: {signal.id.slice(0, 16)}…</div>
      </div>

      {ev && (
        <div className="rounded-md border p-3 text-xs">
          <div className="mb-1 font-semibold">Hypothetical outcome ({ev.methodology})</div>
          {ev.r_multiple !== undefined && (
            <div>
              {ev.exit_reason} at {fmtPrice(ev.exit_price)} (<Time iso={ev.exit_time} />) · R multiple {fmtNum(ev.r_multiple)}
            </div>
          )}
          {ev.targets && (
            <div>
              Targets reached before exit: {ev.targets.targets_hit.length ? ev.targets.targets_hit.join(", ") : "none"}
              {ev.targets.weighted_r != null && ` · weighted R (partial exits) ${fmtNum(ev.targets.weighted_r)}`}
            </div>
          )}
          <div className="text-muted-foreground">{ev.note}</div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, sub, className }: { label: string; value: string; sub?: string; className?: string }) {
  return (
    <div className="rounded-md border p-2.5">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={`tabular text-base font-semibold ${className ?? ""}`}>{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}
