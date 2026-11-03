import type {
  BacktestRun,
  CandlesResponse,
  Explanation,
  Notification,
  Overview,
  PriceAlert,
  RiskResult,
  RuntimeSettings,
  ScannerRow,
  ScannerStatus,
  SettingsResponse,
  SetupResponse,
  Signal,
  SignalStats,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
export const WS_URL = API_URL.replace(/^http/, "ws") + "/ws";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function detailToMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const loc = Array.isArray(d?.loc) ? d.loc.filter((x: unknown) => x !== "body").join(".") : "";
        return loc ? `${loc}: ${d?.msg}` : String(d?.msg ?? d);
      })
      .join("; ");
  }
  return "request failed";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, `Cannot reach the backend at ${API_URL}`);
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, detailToMessage(body?.detail) || res.statusText);
  return body as T;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  overview: (timeframe: string) => request<Overview>(`/api/market/overview${qs({ timeframe })}`),
  candles: (symbol: string, timeframe: string, limit = 500) =>
    request<CandlesResponse>(`/api/market/${symbol}/candles${qs({ timeframe, limit })}`),
  setup: (symbol: string, timeframe: string, sizing: { account_balance?: number; risk_pct?: number; leverage?: number } = {}) =>
    request<SetupResponse>(`/api/market/${symbol}/setup${qs({ timeframe, ...sizing })}`),
  explanation: (symbol: string, timeframe: string) =>
    request<{ explanation: Explanation }>(`/api/market/${symbol}/explanation${qs({ timeframe })}`),
  scanner: (params: { symbol?: string; timeframe?: string; trend?: string; signal_status?: string }) =>
    request<{ rows: ScannerRow[]; status: ScannerStatus }>(`/api/scanner${qs(params)}`),
  signals: (params: Record<string, string | number | undefined>) =>
    request<{ total: number; items: Signal[] }>(`/api/signals${qs(params)}`),
  signal: (id: string) => request<Signal & { notifications: Notification[] }>(`/api/signals/${id}`),
  signalStats: () => request<SignalStats>(`/api/signals/stats`),
  notifications: (limit = 50, offset = 0) =>
    request<{ total: number; items: Notification[] }>(`/api/notifications${qs({ limit, offset })}`),
  risk: (body: unknown) => request<RiskResult>(`/api/risk/calculate`, { method: "POST", body: JSON.stringify(body) }),
  backtests: () => request<BacktestRun[]>(`/api/backtests`),
  backtest: (id: string) => request<BacktestRun>(`/api/backtests/${id}`),
  compareBacktests: (ids: string[]) => request<BacktestRun[]>(`/api/backtests/compare${qs({ ids: ids.join(",") })}`),
  createBacktest: (body: unknown) =>
    request<BacktestRun>(`/api/backtests`, { method: "POST", body: JSON.stringify(body) }),
  deleteBacktest: (id: string) => request<void>(`/api/backtests/${id}`, { method: "DELETE" }),
  settings: () => request<SettingsResponse>(`/api/settings`),
  saveSettings: (s: RuntimeSettings) =>
    request<SettingsResponse>(`/api/settings`, { method: "PUT", body: JSON.stringify(s) }),
  priceAlerts: (status?: string) => request<PriceAlert[]>(`/api/price-alerts${qs({ status })}`),
  createPriceAlert: (body: { symbol: string; price: number; condition?: "above" | "below"; note?: string }) =>
    request<PriceAlert>(`/api/price-alerts`, { method: "POST", body: JSON.stringify(body) }),
  cancelPriceAlert: (id: number) => request<PriceAlert>(`/api/price-alerts/${id}`, { method: "DELETE" }),
  telegramTest: () => request<{ ok: boolean }>(`/api/alerts/telegram/test`, { method: "POST" }),
};
