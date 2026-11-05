import { browserTz, displayTz, tzLabel } from "./timezone";

export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 6;
  return v.toLocaleString("en-US", { minimumFractionDigits: Math.min(digits, 2), maximumFractionDigits: digits });
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtCompact(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v);
}

export function fmtPct(v: number | null | undefined, digits = 2, signed = true): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const s = v.toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

export function fmtUsd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

const formatters = new Map<string, Intl.DateTimeFormat>();

function parts(d: Date, tz: string, month: "2-digit" | "short" = "2-digit"): Record<string, string> {
  const key = `${tz}|${month}`;
  let f = formatters.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-GB", { timeZone: tz, year: "numeric", month, day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
    formatters.set(key, f);
  }
  return Object.fromEntries(f.formatToParts(d).map((p) => [p.type, p.value]));
}

function formatIn(d: Date, tz: string, withSeconds = false): string {
  const p = parts(d, tz);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}${withSeconds ? `:${p.second}` : ""}`;
}

/** Timestamp in the display time zone, e.g. "2026-11-03 15:45". The header names the zone. */
export function fmtTime(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return formatIn(d, displayTz(), withSeconds);
}

/** The same moment in the other useful zone: UTC, or the browser's zone when showing UTC. Null if they're the same. */
export function otherTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const other = displayTz() === "UTC" ? browserTz() : "UTC";
  return other === displayTz() ? null : `${formatIn(d, other, true)} ${tzLabel(other)}`;
}

/** Time of day in the display zone, optionally with the date: "15:45" or "03 Nov, 15:45". */
export function fmtClock(iso: string | null | undefined, withDate = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const p = parts(d, displayTz(), "short");
  return withDate ? `${p.day} ${p.month}, ${p.hour}:${p.minute}` : `${p.hour}:${p.minute}`;
}

/** Calendar date in the display zone, e.g. "04 Nov 2026". */
export function fmtDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const p = parts(new Date(iso), displayTz(), "short");
  return `${p.day} ${p.month} ${p.year}`;
}

/** Sri Lanka's calendar day still defines the Intelligence Lab's "today" (the backend groups results by it). */
export const CHART_TZ = "Asia/Colombo";

const tzParts = (unix: number) => parts(new Date(unix * 1000), displayTz(), "short");

/** Crosshair label, e.g. "03 Nov 2026 15:45". */
export function chartTime(unix: number): string {
  const p = tzParts(unix);
  return `${p.day} ${p.month} ${p.year} ${p.hour}:${p.minute}`;
}

/** Time-axis tick label. tickType follows Lightweight Charts' TickMarkType (0 year, 1 month, 2 day, 3+ time). */
export function chartTick(unix: number, tickType: number): string {
  const p = tzParts(unix);
  if (tickType === 0) return p.year;
  if (tickType === 1) return p.month;
  if (tickType === 2) return `${p.day} ${p.month}`;
  return `${p.hour}:${p.minute}`;
}

/** Notifications default to Sri Lanka time unless the user chose another zone. */
export const DEFAULT_TZ = "Asia/Colombo";

/** e.g. "04 Nov 2026 13:30 Asia/Colombo". Stored timestamps stay in UTC. */
export function fmtZoned(iso: string | null | undefined, tz: string = DEFAULT_TZ): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  try {
    const p = Object.fromEntries(
      new Intl.DateTimeFormat("en-GB", { timeZone: tz, year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
        .formatToParts(d)
        .map((x) => [x.type, x.value]),
    ) as Record<string, string>;
    return `${p.day} ${p.month} ${p.year} ${p.hour}:${p.minute} ${tz}`;
  } catch {
    return fmtTime(iso);
  }
}

/** UTC instant of the start (or end) of a calendar day in a time zone, for date-range filters. */
export function zonedDayBoundary(day: string, tz: string = DEFAULT_TZ, end = false): string {
  const local = new Date(`${day}T${end ? "23:59:59.999" : "00:00:00.000"}Z`);
  // The difference between how UTC and the zone display the same instant is the zone's offset.
  const shown = (zone: string) => new Date(local.toLocaleString("en-US", { timeZone: zone })).getTime();
  return new Date(local.getTime() - (shown(tz) - shown("UTC"))).toISOString();
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const sec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export function trendClass(trend: string | null | undefined): string {
  if (trend === "BULLISH" || trend === "BULLISH_STACK" || trend === "POSITIVE" || trend === "LONG") return "text-bull";
  if (trend === "BEARISH" || trend === "BEARISH_STACK" || trend === "NEGATIVE" || trend === "SHORT") return "text-bear";
  return "text-muted-foreground";
}

export function changeClass(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v) || v === 0) return "text-muted-foreground";
  return v > 0 ? "text-bull" : "text-bear";
}
