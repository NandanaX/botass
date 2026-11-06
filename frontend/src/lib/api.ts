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
import type {
  AnalysisResult,
  MtfResponse,
  OverlaysResponse,
  ReportResponse,
  ValidationResponse,
} from "./analysis-types";
import type { Forensics, StudyResponse, StudyTrade, TodayResponse } from "./research-types";
import type { DContext, DDecision, DOverview, DResetBody, DResetResult, DStudy } from "./strategy-d-types";
import type { ForwardTests, HistoricalRobustness, PaperTrade, ValidationOverview } from "./validation-types";
import type { Phase9Study, PositionInput, PositionPlan, TradePlanResponse } from "./planning-types";
import type { SystemHealth } from "./system-health";
import type {
  Incident,
  NotificationDetail,
  NotificationFilters,
  NotificationList,
  NotificationPrefs,
  NotificationPrefsResponse,
  UnreadCount,
} from "./notification-types";

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
  saveSettings: (s: RuntimeSettings, confirmNewForwardPeriod = false) =>
    request<SettingsResponse>(`/api/settings${qs({ confirm_new_forward_period: confirmNewForwardPeriod || undefined })}`, { method: "PUT", body: JSON.stringify(s) }),
  priceAlerts: (status?: string) => request<PriceAlert[]>(`/api/price-alerts${qs({ status })}`),
  createPriceAlert: (body: { symbol: string; price: number; condition?: "above" | "below"; note?: string }) =>
    request<PriceAlert>(`/api/price-alerts`, { method: "POST", body: JSON.stringify(body) }),
  cancelPriceAlert: (id: number) => request<PriceAlert>(`/api/price-alerts/${id}`, { method: "DELETE" }),
  analysis: (symbol: string, timeframe: string) =>
    request<AnalysisResult>(`/api/analysis/${symbol}${qs({ timeframe })}`),
  mtf: (symbol: string) => request<MtfResponse>(`/api/analysis/${symbol}/mtf`),
  overlays: (symbol: string, timeframe: string, limit = 500) =>
    request<OverlaysResponse>(`/api/analysis/${symbol}/overlays${qs({ timeframe, limit })}`),
  report: (symbol: string, timeframe: string) => request<ReportResponse>(`/api/analysis/${symbol}/report${qs({ timeframe })}`),
  validation: (symbol: string, timeframe: string, days: number, ablation: boolean) =>
    request<ValidationResponse>(`/api/analysis/${symbol}/validation${qs({ timeframe, days, include_ablation: ablation })}`),
  researchToday: (day?: string) => request<TodayResponse>(`/api/research/today${qs({ day, tz: "Asia/Colombo" })}`),
  forensics: (id: string) => request<Forensics>(`/api/research/signals/${id}/forensics`),
  study: () => request<StudyResponse>(`/api/research/study`),
  studyTrades: (limit = 200) => request<StudyTrade[]>(`/api/research/study/trades${qs({ limit })}`),
  startStudy: (rebuild: boolean) =>
    request<{ state: string }>(`/api/research/study`, { method: "POST", body: JSON.stringify({ rebuild }) }),
  studyStatus: () => request<{ state: string; step: string | null; error: string | null }>(`/api/research/study/status`),
  validationOverview: () => request<ValidationOverview>(`/api/validation/overview`),
  forwardTests: () => request<ForwardTests>(`/api/validation/forward-tests`),
  strategyDOverview: () => request<DOverview>(`/api/strategy-d/overview`),
  strategyDDecisions: (decision?: string) => request<DDecision[]>(`/api/strategy-d/decisions?limit=200${decision ? `&decision=${decision}` : ""}`),
  strategyDContext: (symbol: string, at?: string, direction?: string) => {
    const q = new URLSearchParams();
    if (at) q.set("at", at);
    if (direction) q.set("direction", direction);
    return request<DContext>(`/api/strategy-d/context/${symbol}?${q}`);
  },
  strategyDStudy: () => request<DStudy>(`/api/strategy-d/study`),
  strategyDResetDrawdown: (body: DResetBody) => request<DResetResult>(`/api/strategy-d/reset-drawdown`, { method: "POST", body: JSON.stringify(body) }),
  tradePlan: (symbol: string, timeframe: string, stopMethod: string, tp: number[]) =>
    request<TradePlanResponse>(`/api/planning/${symbol}/trade-plan?timeframe=${timeframe}&stop_method=${stopMethod}${tp.map((x) => `&tp=${x}`).join("")}`),
  positionPlan: (body: PositionInput) => request<PositionPlan>(`/api/planning/position`, { method: "POST", body: JSON.stringify(body) }),
  phase9Study: () => request<Phase9Study>(`/api/planning/study`),
  validationTrades: (variant?: string, status?: string, limit = 300) =>
    request<PaperTrade[]>(`/api/validation/trades${qs({ variant, status, limit })}`),
  validationHistorical: () => request<HistoricalRobustness>(`/api/validation/historical`),
  runValidationHistorical: () => request<{ state: string }>(`/api/validation/historical`, { method: "POST" }),
  promotionDecision: (key: string, body: { decision: string; note: string; reviewer: string }) =>
    request<{ recorded: boolean; production_changed: boolean; note: string }>(`/api/validation/promotion/${key}/decision`, { method: "POST", body: JSON.stringify(body) }),
  validationTrade: (id: string) => request<PaperTrade & Record<string, unknown>>(`/api/validation/trades/${id}`),
  notificationEvents: (f: NotificationFilters) => request<NotificationList>(`/api/notification-center/events${qs({ ...f })}`),
  notificationEvent: (id: number) => request<NotificationDetail>(`/api/notification-center/events/${id}`),
  notificationUnread: () => request<UnreadCount>(`/api/notification-center/unread-count`),
  notificationFacets: () => request<{ symbols: string[]; timeframes: string[] }>(`/api/notification-center/facets`),
  notificationRead: (id: number, read = true) =>
    request<{ id: number; read: boolean; unread: UnreadCount }>(`/api/notification-center/events/${id}/read`, { method: "POST", body: JSON.stringify({ read }) }),
  notificationReadAll: () => request<{ updated: number; unread: UnreadCount }>(`/api/notification-center/read-all`, { method: "POST", body: "{}" }),
  notificationIncidents: () => request<Incident[]>(`/api/notification-center/incidents`),
  notificationPrefs: () => request<NotificationPrefsResponse>(`/api/notification-center/preferences`),
  saveNotificationPrefs: (p: NotificationPrefs) =>
    request<NotificationPrefsResponse>(`/api/notification-center/preferences`, { method: "PUT", body: JSON.stringify(p) }),
  systemHealth: () => request<SystemHealth>(`/api/system/health`),
  telegramTest: () => request<{ ok: boolean }>(`/api/alerts/telegram/test`, { method: "POST" }),
};
