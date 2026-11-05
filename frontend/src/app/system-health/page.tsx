"use client";

import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/app-shell";
import { useNotificationTz } from "@/components/notifications";
import { Empty } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { fmtZoned, timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";
import { backendDownComponents, deriveFrontend, formatUptime, type HealthComponent, type HealthStatus } from "@/lib/system-health";

const VARIANT: Record<HealthStatus, "bull" | "warn" | "bear" | "outline"> = { HEALTHY: "bull", DEGRADED: "warn", DOWN: "bear", UNKNOWN: "outline" };

function StatusBadge({ status }: { status: HealthStatus }) {
  return <Badge variant={VARIANT[status]}>{status}</Badge>;
}

function ComponentCard({ c }: { c: HealthComponent }) {
  return (
    <div data-testid={`component-${c.key}`} className={`rounded-md border p-3 ${c.status === "DOWN" ? "border-bear/50 bg-bear/10" : ""}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{c.name}</span>
        <StatusBadge status={c.status} />
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{c.detail}</p>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/50 py-1 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular text-right">{children}</span>
    </div>
  );
}

function When({ iso, tz }: { iso: string | null | undefined; tz: string }) {
  if (!iso) return <>never</>;
  return <time dateTime={iso} title={fmtZoned(iso, tz)}>{timeAgo(iso)}</time>;
}

export default function SystemHealthPage() {
  const tz = useNotificationTz();
  const { socket } = useLive();
  const q = useQuery({ queryKey: ["system-health"], queryFn: api.systemHealth, refetchInterval: 15_000, retry: false });
  const d = q.data;
  const unreachable = !!q.error;
  const components = unreachable ? backendDownComponents(String((q.error as Error)?.message ?? "")) : d?.components;
  const metric = (key: string, name: string) => d?.components.find((c) => c.key === key)?.metrics[name];
  const overall: HealthStatus | undefined = unreachable ? "DOWN" : d?.overall;
  return (
    <>
      <PageHeader
        title="System Health"
        description="Status comes from heartbeats and data-freshness checks, not from the process being alive. UNKNOWN means a component could not be verified."
        actions={overall && <span className="flex items-center gap-2 text-sm">Overall <StatusBadge status={overall} /></span>}
      />
      {!components ? <Empty>Checking services…</Empty> : (
        <div className="space-y-4">
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            <ComponentCard c={deriveFrontend(socket)} />
            {unreachable ? null : <ComponentCard c={{ key: "backend", name: "Backend API", status: "HEALTHY", detail: "Answered this page's last request.", metrics: {} }} />}
            {components.map((c) => <ComponentCard key={c.key} c={c} />)}
          </div>
          {d && (
            <div className="grid items-start gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader><CardTitle>Latest activity</CardTitle></CardHeader>
                <CardContent>
                  <Fact label="Last market update"><When iso={metric("market_data", "last_update") as string} tz={tz} /></Fact>
                  <Fact label="Last candle processed">
                    {d.last_candle ? <>{d.last_candle.symbol} {d.last_candle.timeframe} · <When iso={d.last_candle.close_time} tz={tz} /></> : "none since start"}
                  </Fact>
                  <Fact label="Scanner heartbeat"><When iso={d.scanner_heartbeat} tz={tz} /></Fact>
                  <Fact label="Last signal"><When iso={d.last_signal} tz={tz} /></Fact>
                  <Fact label="Last paper-trading event"><When iso={d.last_paper_event} tz={tz} /></Fact>
                  <Fact label="Last Telegram delivery"><When iso={d.last_telegram_delivery} tz={tz} /></Fact>
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle>Performance</CardTitle></CardHeader>
                <CardContent>
                  <Fact label="Uptime">{formatUptime(d.uptime_seconds)}</Fact>
                  <Fact label="API latency (median / 95th)">{d.api_latency_ms.p50 ?? "—"} / {d.api_latency_ms.p95 ?? "—"} ms</Fact>
                  <Fact label="Database write check">{(metric("database", "latency_ms") as number) ?? "—"} ms</Fact>
                  <Fact label="Database read">{d.database_read_ms} ms</Fact>
                  <Fact label="Bybit REST round trip">{(metric("bybit_rest", "latency_ms") as number) ?? "—"} ms</Fact>
                  <Fact label="Clock difference from Bybit">{(metric("bybit_rest", "clock_skew_s") as number) ?? "—"} s</Fact>
                  <Fact label="Last scan duration">{(metric("scanner", "cycle_ms") as number) ?? "—"} ms</Fact>
                  <Fact label="Decision engine">{d.decision_mode}</Fact>
                  <Fact label="Real-money trading">{d.real_money_trading ? "ON" : "Disabled"}</Fact>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Incidents and recoveries</CardTitle>
                  <CardDescription>Each outage is one entry, however many retries it took.</CardDescription>
                </CardHeader>
                <CardContent>
                  {d.incidents.length === 0 ? <Empty>No incidents recorded.</Empty> : (
                    <ul className="space-y-2 text-sm">
                      {d.incidents.slice(0, 15).map((i) => (
                        <li key={i.id} className="border-b border-border/50 pb-2 last:border-0">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <Badge variant={i.status === "OPEN" ? "warn" : "bull"}>{i.status === "OPEN" ? "OPEN" : "RECOVERED"}</Badge>
                            <span className="font-medium">{i.title}</span>
                          </div>
                          <div className="mt-0.5 text-xs text-muted-foreground">
                            {fmtZoned(i.started_at, tz)}{i.resolved_at ? ` → ${fmtZoned(i.resolved_at, tz)}` : ""}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Latest errors and warnings</CardTitle>
                  <CardDescription>Since this backend started. Credentials are removed.</CardDescription>
                </CardHeader>
                <CardContent>
                  {d.recent_problems.length === 0 ? <Empty>None since the backend started.</Empty> : (
                    <ul className="max-h-96 space-y-1.5 overflow-y-auto text-xs">
                      {d.recent_problems.map((p, k) => (
                        <li key={`${p.time}-${k}`}>
                          <Badge variant={p.level === "WARNING" ? "warn" : "bear"}>{p.level}</Badge>{" "}
                          <span className="text-muted-foreground">{fmtZoned(p.time, tz)}</span> {p.message}
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            </div>
          )}
        </div>
      )}
    </>
  );
}
