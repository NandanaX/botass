"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { Ticker } from "@/lib/types";

const STALE_MS = 30_000;

/** Re-render periodically so time-based states (stale data, "x ago") stay accurate. */
export function useNow(intervalMs = 5000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function isTickerStale(t: Ticker | null | undefined, now: number): boolean {
  if (!t || !t.available || !t.updated_at) return true;
  return t.stale || now - new Date(t.updated_at).getTime() > STALE_MS;
}

export function DataStateBadge({ ticker }: { ticker: Ticker | null | undefined }) {
  const now = useNow();
  if (!ticker || !ticker.available) return <Badge variant="bear">NO DATA</Badge>;
  if (isTickerStale(ticker, now)) return <Badge variant="warn">STALE</Badge>;
  return (
    <Badge variant="bull" title={`Source: ${ticker.source}`}>
      LIVE
    </Badge>
  );
}

const SIGNAL_STATUS: Record<string, { label: string; variant: "bull" | "bear" | "warn" | "info" | "outline" | "default" }> = {
  LONG_SIGNAL: { label: "LONG SIGNAL", variant: "bull" },
  SHORT_SIGNAL: { label: "SHORT SIGNAL", variant: "bear" },
  WATCH_LONG: { label: "Watchlist long", variant: "info" },
  WATCH_SHORT: { label: "Watchlist short", variant: "info" },
  REJECTED: { label: "Setup rejected", variant: "warn" },
  FILTERED: { label: "Filtered by HTF", variant: "warn" },
  NONE: { label: "No setup", variant: "outline" },
  NOT_READY: { label: "Warming up", variant: "outline" },
  DATA_ISSUE: { label: "Data issue", variant: "warn" },
};

export function SignalStatusBadge({ status }: { status: string }) {
  const s = SIGNAL_STATUS[status] ?? { label: status, variant: "default" as const };
  return <Badge variant={s.variant}>{s.label}</Badge>;
}

const EVAL_STATUS: Record<string, { label: string; variant: "bull" | "bear" | "warn" | "info" | "outline" }> = {
  PENDING: { label: "Active", variant: "info" },
  TARGET_HIT: { label: "Target hit*", variant: "bull" },
  STOP_HIT: { label: "Stop hit*", variant: "bear" },
  AMBIGUOUS_STOP: { label: "Stop (ambiguous)*", variant: "bear" },
  EXPIRED: { label: "Expired*", variant: "outline" },
  INVALIDATED: { label: "Invalidated*", variant: "warn" },
};

export function EvaluationBadge({ status }: { status: string }) {
  const s = EVAL_STATUS[status] ?? { label: status, variant: "outline" as const };
  return (
    <Badge variant={s.variant} title={status === "PENDING" ? "Not yet evaluated" : "Hypothetical outcome (methodology v1), not a realized trade"}>
      {s.label}
    </Badge>
  );
}

const NOTIF: Record<string, "bull" | "bear" | "warn" | "outline" | "info"> = {
  SENT: "bull",
  FAILED: "bear",
  PENDING: "info",
  DISABLED: "outline",
  NOT_CONFIGURED: "outline",
  BELOW_THRESHOLD: "outline",
  SKIPPED_STALE: "warn",
};

export function NotificationBadge({ status }: { status: string }) {
  return <Badge variant={NOTIF[status] ?? "outline"}>{status.replace(/_/g, " ").toLowerCase()}</Badge>;
}

const SETUP_STATUS: Record<string, { variant: "bull" | "bear" | "warn" | "info" | "outline"; title: string }> = {
  QUALIFIED: { variant: "bull", title: "All mandatory entry conditions and risk constraints are satisfied" },
  WATCHLIST: { variant: "info", title: "Setup developing: trigger not confirmed yet" },
  REJECTED: { variant: "warn", title: "A mandatory condition or risk constraint failed" },
  EXPIRED: { variant: "outline", title: "The setup is no longer valid (no valid hypothetical entry)" },
  NO_SETUP: { variant: "outline", title: "No setup matches the configured rules" },
};

export function SetupStatusBadge({ status }: { status: string }) {
  const s = SETUP_STATUS[status] ?? { variant: "outline" as const, title: status };
  return (
    <Badge variant={s.variant} title={s.title}>
      {status.replace(/_/g, " ")}
    </Badge>
  );
}

export function StrategyLabel({ strategy }: { strategy: string | null | undefined }) {
  const label = strategy === "pullback" ? "Pullback / retest" : strategy === "breakout" ? "Breakout" : "Trend breakout (v1)";
  return <span>{label}</span>;
}

export function DirectionBadge({ direction }: { direction: string }) {
  return <Badge variant={direction === "LONG" ? "bull" : "bear"}>{direction}</Badge>;
}

export function TrendText({ value }: { value: string | null | undefined }) {
  const cls = value === "BULLISH" || value === "BULLISH_STACK" || value === "POSITIVE"
    ? "text-bull"
    : value === "BEARISH" || value === "BEARISH_STACK" || value === "NEGATIVE"
      ? "text-bear"
      : "text-muted-foreground";
  return <span className={cls}>{value ? value.replace(/_/g, " ").toLowerCase() : "—"}</span>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="rounded-md border border-bear/40 bg-bear/10 px-3 py-2 text-sm text-bear">
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="py-8 text-center text-sm text-muted-foreground">{children}</div>;
}
