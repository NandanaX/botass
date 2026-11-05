const SECONDS: Record<string, number> = { "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800 };

// Weekly candles open on Monday 00:00 UTC, 4 days after the Thursday the epoch fell on (same as the backend's TF_OFFSETS).
const OFFSET: Record<string, number> = { "1w": 4 * 86400 };

/** Time (ms since the epoch) the candle running at ``nowMs`` closes. */
export function nextCandleClose(timeframe: string, nowMs: number): number {
  const step = (SECONDS[timeframe] ?? 900) * 1000;
  const offset = (OFFSET[timeframe] ?? 0) * 1000;
  return Math.floor((nowMs - offset) / step) * step + offset + step;
}

/** e.g. "04:32", "1:04:32", "2d 03:12:44". */
export function fmtCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  if (d > 0) return `${d}d ${pad(h)}:${pad(m)}:${pad(s)}`;
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}
