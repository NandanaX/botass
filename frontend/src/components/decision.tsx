"use client";

import { Check, Clock, Minus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Time } from "@/components/time";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fmtCountdown, nextCandleClose } from "@/lib/candles";
import type { DecisionDiagnostics, DiagnosticRule, Direction, SetupResponse, StrategyDiagnostics } from "@/lib/types";
import { cn } from "@/lib/utils";
import { SetupStatusBadge, useNow } from "./status";

type Tone = "bull" | "bear" | "info" | "warn" | "neutral";

const TONE: Record<Tone, string> = {
  bull: "border-bull/60 bg-bull/15",
  bear: "border-bear/60 bg-bear/15",
  info: "border-primary/60 bg-primary/15",
  warn: "border-warn/60 bg-warn/15",
  neutral: "border-border bg-muted/40",
};

export interface DecisionState {
  title: string;
  note: string;
  tone: Tone;
}

/** The headline state of the Trade Setup page, taken from the engine's decision. */
export function decisionState(data: SetupResponse | undefined): DecisionState {
  if (!data) return { title: "Analysing…", note: "Reading the latest closed candle.", tone: "neutral" };
  if (!data.market.ready) return { title: "Warming up", note: data.market.not_ready_reason ?? "Not enough candle history yet.", tone: "neutral" };
  const first = data.primary_setup;
  const side = first?.direction === "SHORT" ? "short" : "long";
  if (data.decision === "QUALIFIED" && first) {
    return { title: first.direction === "SHORT" ? "Short" : "Long", note: "Qualified signal: every rule passed. A research signal, not an open position.", tone: first.direction === "SHORT" ? "bear" : "bull" };
  }
  if (data.decision === "REJECTED") return { title: `Rejected ${side}`, note: "The trigger happened but a rule failed, so this is not a trade signal.", tone: "warn" };
  if (data.decision === "WATCHLIST") return { title: `Watchlist ${side}`, note: "Developing: waiting for the trigger candle. Not a signal yet.", tone: "info" };
  return { title: "No setup", note: "No rule set matches on the last closed candle. Nothing is forced.", tone: "neutral" };
}

export function CandleCountdown({ timeframe, label = true, className }: { timeframe: string; label?: boolean; className?: string }) {
  const now = useNow(1000);
  // Empty until mounted: the server's clock would never match the browser's during hydration.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const left = nextCandleClose(timeframe, now) - now;
  return (
    <span className={cn("inline-flex items-center gap-1.5 tabular", className)} title={`Time until the current ${timeframe} candle closes. Decisions are only made on closed candles.`}>
      <Clock className="h-3.5 w-3.5 shrink-0" aria-hidden />
      {label && <span>Next {timeframe} close in</span>}
      {!label && <span>{timeframe}</span>}
      <span className="font-semibold text-foreground">{mounted ? fmtCountdown(left) : "–:–"}</span>
    </span>
  );
}

export function DecisionBlock({ data, timeframe }: { data: SetupResponse | undefined; timeframe: string }) {
  const s = decisionState(data);
  return (
    <div role="status" aria-label={`Decision: ${s.title}`} data-testid="decision-block" className={cn("rounded-lg border-2 px-4 py-2.5", TONE[s.tone])}>
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Decision · {timeframe}</div>
      <div className="text-2xl font-semibold leading-tight">{s.title}</div>
      <p className="mt-0.5 max-w-sm text-xs text-muted-foreground">{s.note}</p>
      <CandleCountdown timeframe={timeframe} className="mt-1.5 text-xs text-muted-foreground" />
    </div>
  );
}

function RuleIcon({ rule }: { rule: DiagnosticRule }) {
  if (rule.state === "pass") return <Check className="mt-px h-3.5 w-3.5 shrink-0 text-bull" role="img" aria-label="Passed" />;
  if (rule.state === "na") return <Minus className="mt-px h-3.5 w-3.5 shrink-0 text-muted-foreground" role="img" aria-label="Not evaluated yet" />;
  return <X className={cn("mt-px h-3.5 w-3.5 shrink-0", rule.advisory ? "text-warn" : "text-bear")} role="img" aria-label={rule.advisory ? "Warning only" : "Failed"} />;
}

function RuleRow({ rule }: { rule: DiagnosticRule }) {
  return (
    <li className="flex items-start gap-1.5 py-0.5" data-rule={rule.key} data-state={rule.state}>
      <RuleIcon rule={rule} />
      <span className="min-w-0 flex-1">{rule.label}{rule.advisory && rule.state === "fail" ? " (warning only)" : ""}</span>
      <span className="max-w-[55%] text-right tabular text-muted-foreground">
        {rule.value}
        {rule.threshold && <span> / {rule.threshold}</span>}
      </span>
    </li>
  );
}

function StrategyRules({ s }: { s: StrategyDiagnostics }) {
  const counted = s.rules.filter((r) => r.state !== "na");
  const passed = counted.filter((r) => r.state === "pass").length;
  const groups = [...new Set(s.rules.map((r) => r.group))];
  return (
    <section aria-label={`${s.label} rules`} className="rounded-md border border-border p-3" data-strategy={s.strategy}>
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold">{s.label}</h3>
        <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
          {passed} of {s.rules.length} met
          <SetupStatusBadge status={s.status} />
        </span>
      </div>
      {groups.map((g) => (
        <div key={g} className="mt-1.5">
          <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{g}</div>
          <ul className="text-xs">{s.rules.filter((r) => r.group === g).map((r) => <RuleRow key={r.key} rule={r} />)}</ul>
        </div>
      ))}
    </section>
  );
}

/** Why there is, or isn't, a signal: each strategy's rules with value against threshold. */
export function RuleChecklist({ diagnostics, closeTime, preferred }: { diagnostics: DecisionDiagnostics; closeTime?: string | null; preferred?: Direction | null }) {
  const [picked, setPicked] = useState<Direction | null>(null);
  const direction: Direction = picked ?? preferred ?? diagnostics.trend_direction ?? "LONG";
  const shown = diagnostics.strategies.filter((s) => s.direction === direction);
  return (
    <Card data-testid="rule-checklist">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          Rule checklist
          <Tabs value={direction} onValueChange={(v) => setPicked(v as Direction)}>
            <TabsList aria-label="Direction">
              <TabsTrigger value="LONG">Long</TabsTrigger>
              <TabsTrigger value="SHORT">Short</TabsTrigger>
            </TabsList>
          </Tabs>
        </CardTitle>
        <CardDescription>
          Each rule from Settings on the candle closed <Time iso={closeTime} />, as value / requirement.
          {!diagnostics.trend_direction && " The trend is mixed, so neither direction can trigger."}
          {diagnostics.trend_direction && diagnostics.trend_direction !== direction && ` The trend is ${diagnostics.trend_direction === "LONG" ? "bullish" : "bearish"}, so this side can't trigger now.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {shown.map((s) => <StrategyRules key={s.strategy} s={s} />)}
        <p className="text-[11px] text-muted-foreground">A dash means the rule can&apos;t be measured yet, such as net R:R before there is a trade plan.</p>
      </CardContent>
    </Card>
  );
}

export function TriggerLines({ diagnostics }: { diagnostics: DecisionDiagnostics }) {
  if (!diagnostics.triggers.length) return null;
  return (
    <div data-testid="trigger-lines" className="mb-4 rounded-md border border-border bg-card px-3 py-2 text-sm">
      <div className="mb-0.5 text-xs font-semibold">What would trigger a signal</div>
      {diagnostics.triggers.map((t) => <p key={t.text} className="text-xs text-muted-foreground">{t.text}</p>)}
      {diagnostics.trend_direction && <p className="mt-1 text-[11px] text-muted-foreground">The remaining rules on the checklist must also pass. Levels use the current ATR and move as candles close.</p>}
    </div>
  );
}

