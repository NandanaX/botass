import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import type { OverlaysResponse } from "@/lib/analysis-types";
import type { Shape } from "./shapes-primitive";

export interface AnalysisToggles {
  structure: boolean;
  patterns: boolean;
  candles: boolean;
  trendlines: boolean;
  fibonacci: boolean;
  liquidity: boolean;
  movingAverages: boolean;
  plan: boolean;
}

/** Only the most relevant annotations by default, to avoid clutter. */
export const DEFAULT_ANALYSIS_TOGGLES: AnalysisToggles = {
  structure: true,
  patterns: true,
  candles: false,
  trendlines: false,
  fibonacci: false,
  liquidity: false,
  movingAverages: false,
  plan: true,
};

export const ANALYSIS_TOGGLE_LABELS: Record<keyof AnalysisToggles, string> = {
  structure: "Market structure",
  patterns: "Chart patterns",
  candles: "Candlestick patterns",
  trendlines: "Trendlines",
  fibonacci: "Fibonacci",
  liquidity: "Liquidity / imbalance",
  movingAverages: "EMA 9/100 · SMA 50/200",
  plan: "Entry / SL / TP",
};

const MAX_EVENTS = 8;
const MAX_SWINGS = 8;
const MAX_PATTERNS = 3;
const MAX_CANDLES = 15;
const BULL = "#16c784";
const BEAR = "#ea3943";
const NEUTRAL = "#8a93a3";
const MA_COLORS: Record<string, string> = { ema9: "#f97316", ema100: "#22d3ee", sma50: "#a3e635", sma200: "#f472b6" };

const short = (n: string) =>
  n.replace("Bullish ", "Bull ").replace("Bearish ", "Bear ").replace("Three white soldiers", "3 soldiers")
    .replace("Three black crows", "3 crows").replace("Morning star", "M.star").replace("Evening star", "E.star");

export function buildAnalysisOverlays(o: OverlaysResponse | undefined, t: AnalysisToggles) {
  const shapes: Shape[] = [];
  const markers: SeriesMarker<Time>[] = [];
  const lines: { name: string; color: string; data: { time: number; value: number }[] }[] = [];
  if (!o) return { shapes, markers, lines };
  if (t.structure) {
    // Only the most recent breaks and swings, so the chart stays readable.
    for (const e of o.structure_events.slice(-MAX_EVENTS)) {
      if (e.unix == null) continue;
      markers.push({
        time: e.unix as UTCTimestamp,
        position: e.direction === "BULLISH" ? "belowBar" : "aboveBar",
        color: e.direction === "BULLISH" ? BULL : BEAR,
        shape: e.kind === "BOS" ? "square" : "circle",
        text: e.kind === "CHOCH" ? "CHoCH" : "BOS",
      });
    }
    for (const s of o.swings.slice(-MAX_SWINGS)) {
      markers.push({
        time: s.unix as UTCTimestamp,
        position: s.kind === "high" ? "aboveBar" : "belowBar",
        color: "#c0c7d4",
        shape: "circle",
        size: 0.3,
        text: s.label,
      });
    }
  }
  if (t.patterns) {
    const recent = [...o.chart_patterns].sort((a, b) => b.detected_index - a.detected_index).slice(0, MAX_PATTERNS);
    for (const p of recent) {
      const col = p.status === "CONFIRMED" ? (p.breakout_direction === "BULLISH" ? BULL : BEAR)
        : p.expected === "BULLISH" ? "#16c784aa" : p.expected === "BEARISH" ? "#ea3943aa" : "#f5c542";
      p.lines.forEach((ln, k) =>
        shapes.push({
          kind: "segment", t1: ln.t1, p1: ln.p1, t2: ln.t2, p2: ln.p2, color: col, width: 1.5,
          dashed: ln.dashed || p.status === "DEVELOPING",
          label: k === 0 ? `${p.name} (${p.status.toLowerCase()})` : undefined,
        }),
      );
      if (p.status === "DEVELOPING") for (const pv of p.pivots) {
        markers.push({ time: pv.time as UTCTimestamp, position: pv.kind === "high" ? "aboveBar" : "belowBar", color: col, shape: "circle", size: 0.5 });
      }
      if (p.target != null && p.status !== "INVALIDATED" && p === recent[0]) {
        const last = p.lines[0]?.t2 ?? p.pivots[p.pivots.length - 1]?.time;
        if (last) shapes.push({ kind: "segment", t1: last, p1: p.target, t2: last, p2: p.target, color: col, dashed: true, extend: true, label: `${p.name} target (theoretical)` });
      }
    }
  }
  if (t.candles) {
    for (const c of o.candlestick_patterns.slice(-MAX_CANDLES)) {
      if (c.unix == null) continue;
      markers.push({
        time: c.unix as UTCTimestamp,
        position: c.direction === "BULLISH" ? "belowBar" : "aboveBar",
        color: c.direction === "BULLISH" ? "#4ade80" : "#f87171",
        shape: c.direction === "BULLISH" ? "arrowUp" : "arrowDown",
        size: 0.6,
        text: short(c.name),
      });
    }
  }
  if (t.trendlines) {
    for (const tl of o.trendlines) {
      shapes.push({ kind: "segment", t1: tl.t1, p1: tl.p1, t2: tl.t2, p2: tl.p2, extend: true, color: tl.kind === "support" ? "#38bdf8" : "#fb923c", dashed: tl.broken, label: tl.broken ? `${tl.kind} trendline (broken)` : `${tl.kind} trendline` });
    }
  }
  if (t.fibonacci && o.fibonacci) {
    const f = o.fibonacci;
    const t0 = Math.floor(new Date(f.direction === "UP" ? f.low_time : f.high_time).getTime() / 1000);
    for (const lv of f.levels) {
      shapes.push({ kind: "segment", t1: t0, p1: lv.price, t2: t0, p2: lv.price, extend: true, color: lv.kind === "extension" ? "#c084fc99" : "#facc1599", dashed: true, label: `Fib ${(lv.ratio * 100).toFixed(1)}%` });
    }
  }
  if (t.liquidity) {
    for (const g of o.fair_value_gaps) {
      shapes.push({ kind: "box", t1: g.time, t2: null, lower: g.lower, upper: g.upper, fill: g.direction === "BULLISH" ? "rgba(56,189,248,0.10)" : "rgba(251,146,60,0.10)", border: g.direction === "BULLISH" ? "#38bdf8aa" : "#fb923caa", label: "FVG (experimental)" });
    }
    for (const e of o.equal_levels) {
      const first = o.swings[0]?.unix ?? 0;
      shapes.push({ kind: "segment", t1: first, p1: e.price, t2: first, p2: e.price, extend: true, color: NEUTRAL, dashed: true, label: `${e.kind} ×${e.touches}` });
    }
  }
  if (t.movingAverages) {
    for (const [name, data] of Object.entries(o.moving_averages)) lines.push({ name: name.toUpperCase(), color: MA_COLORS[name] ?? NEUTRAL, data });
  }
  return { shapes, markers, lines };
}
