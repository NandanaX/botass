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

/** UTC timestamp, e.g. "2026-11-03 10:15 UTC". */
export function fmtTime(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const s = d.toISOString().replace("T", " ");
  return `${withSeconds ? s.slice(0, 19) : s.slice(0, 16)} UTC`;
}

/** Charts show time in Sri Lanka time; candle data itself stays in UTC. */
export const CHART_TZ = "Asia/Colombo";
export const CHART_TZ_LABEL = "Sri Lanka time (UTC+05:30)";

const tzParts = (unix: number) =>
  Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: CHART_TZ,
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(new Date(unix * 1000))
      .map((p) => [p.type, p.value]),
  ) as Record<string, string>;

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
