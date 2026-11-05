// Phase 8 System Health. Statuses come from the backend's heartbeat and freshness checks.
export type HealthStatus = "HEALTHY" | "DEGRADED" | "DOWN" | "UNKNOWN";

export interface HealthComponent {
  key: string;
  name: string;
  status: HealthStatus;
  detail: string;
  metrics: Record<string, unknown>;
}

export interface SystemHealth {
  generated_at: string;
  overall: HealthStatus;
  components: HealthComponent[];
  started_at: string | null;
  uptime_seconds: number | null;
  api_latency_ms: { samples: number; p50: number | null; p95: number | null };
  database_read_ms: number;
  database_error: string | null;
  last_signal: string | null;
  last_paper_event: string | null;
  last_telegram_delivery: string | null;
  last_candle: { symbol: string; timeframe: string; open_time: string; close_time: string } | null;
  scanner_heartbeat: string | null;
  recent_problems: { time: string; level: string; logger: string; message: string }[];
  incidents: { id: number; key: string; status: "OPEN" | "RESOLVED"; severity: string; title: string; started_at: string; resolved_at: string | null }[];
  pauses: { from: string; to: string; duration: string }[];
  decision_mode: string;
  real_money_trading: boolean;
}

/** The page is running, so the frontend itself is verified; live updates depend on the socket. */
export function deriveFrontend(socket: "connecting" | "open" | "closed"): HealthComponent {
  const status: HealthStatus = socket === "open" ? "HEALTHY" : "DEGRADED";
  const detail = socket === "open" ? "This page is running and receiving live updates." : `This page is running, but live updates are ${socket}.`;
  return { key: "frontend", name: "Frontend", status, detail, metrics: {} };
}

/** Without the backend nothing behind it can be verified, so it reads UNKNOWN rather than healthy or down. */
export function backendDownComponents(error: string): HealthComponent[] {
  const unverified = [
    ["database", "Database"], ["bybit_rest", "Bybit REST API"], ["bybit_ws", "Bybit WebSocket"], ["market_data", "Market data freshness"],
    ["scanner", "Market scanner"], ["signal_engine", "Signal engine"], ["paper_worker", "Paper-trading worker"], ["telegram", "Telegram delivery"],
    ["notifications", "Notification service"], ["monitor", "Health monitor"],
  ];
  return [
    { key: "backend", name: "Backend API", status: "DOWN", detail: error || "No response.", metrics: {} },
    ...unverified.map(([key, name]) => ({ key, name, status: "UNKNOWN" as const, detail: "Cannot be verified while the backend is unreachable.", metrics: {} })),
  ];
}

export function formatUptime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d}d ${h}h ${m}m` : h ? `${h}h ${m}m` : `${m}m`;
}
