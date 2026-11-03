"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { fmtPrice } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { CandlesResponse, Direction, Signal, Zone } from "@/lib/types";
import { ZonesPrimitive, type ZoneBand } from "./zones-primitive";

const BULL = "#16c784";
const BEAR = "#ea3943";
const EMA_COLORS = ["#f5c542", "#3b82f6", "#c084fc"];

export interface ChartToggles {
  emas: boolean;
  zones: boolean;
  htfZones: boolean;
  brokenZones: boolean;
  swings: boolean;
  volume: boolean;
  signals: boolean;
}

export const DEFAULT_TOGGLES: ChartToggles = {
  emas: true,
  zones: true,
  htfZones: true,
  brokenZones: false,
  swings: false,
  volume: true,
  signals: true,
};

/** A trade plan to draw: entry, stop, targets, confirmation candle and invalidation level. */
export interface FocusPlan {
  direction: Direction;
  label: string;
  entry: number | null;
  stop: number | null;
  targets: { label: string; price: number; blocked: boolean }[];
  confirmationTime: number | null; // unix seconds (candle open)
  invalidation: number | null;
  entryZone: [number, number] | null;
}

interface Props {
  data: CandlesResponse;
  toggles: ChartToggles;
  focusSignal?: Signal | null;
  focusPlan?: FocusPlan | null;
  height?: number;
  /** Fill the parent's height (full-screen mode); the chart resizes itself. */
  fill?: boolean;
}

const ts = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

export function planFromSignal(s: Signal): FocusPlan {
  const plan = s.plan;
  return {
    direction: s.direction,
    label: `${s.direction} ${s.score}`,
    entry: s.entry_reference,
    stop: s.stop_loss,
    targets: plan?.targets?.length
      ? plan.targets.map((t) => ({ label: t.label, price: t.price, blocked: t.blocked }))
      : [{ label: "Take profit", price: s.take_profit, blocked: false }],
    confirmationTime: ts(s.candle_open_time),
    invalidation: plan?.zone ? (s.direction === "LONG" ? plan.zone.lower : plan.zone.upper) : null,
    entryZone: plan?.entry_zone ?? null,
  };
}

export function zoneBands(zones: Zone[], htf: Record<string, Zone[]>, toggles: ChartToggles): ZoneBand[] {
  const bands: ZoneBand[] = [];
  const nearestS = zones.find((z) => z.zone_type === "SUPPORT" && z.status !== "INVALIDATED");
  const nearestR = zones.find((z) => z.zone_type === "RESISTANCE" && z.status !== "INVALIDATED");
  const add = (z: Zone, isHtf: boolean) => {
    if (z.status !== "ACTIVE" && !toggles.brokenZones && !(z === nearestS || z === nearestR)) return;
    const support = z.zone_type === "SUPPORT";
    const emphasis = z === nearestS || z === nearestR;
    const invalid = z.status === "INVALIDATED";
    const rgb = invalid ? "138,147,163" : support ? "22,199,132" : "234,57,67";
    const alpha = (isHtf ? 0.07 : 0.13) * (z.status === "ACTIVE" ? 1 : 0.6) * (emphasis ? 1.5 : 1);
    bands.push({
      lower: z.lower,
      upper: z.upper,
      start: ts(z.first_test_time),
      fill: `rgba(${rgb},${alpha.toFixed(3)})`,
      border: `rgba(${rgb},${emphasis ? 0.95 : isHtf ? 0.45 : 0.6})`,
      label: `${support ? "S" : "R"} ${z.timeframe} ${fmtPrice(z.lower)}–${fmtPrice(z.upper)} ×${z.tests}${z.status !== "ACTIVE" ? ` ${z.status.toLowerCase()}` : ""}${emphasis ? " · nearest" : ""}`,
      dashed: z.status !== "ACTIVE",
      emphasis,
    });
  };
  if (toggles.htfZones) for (const zs of Object.values(htf)) for (const z of zs) add(z, true);
  if (toggles.zones) for (const z of zones) add(z, false);
  return bands;
}

export function PriceChart({ data, toggles, focusSignal, focusPlan, height = 480, fill = false }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const emas = useRef<ISeriesApi<"Line">[]>([]);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const zonesPrim = useRef<ZonesPrimitive | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  const lastTime = useRef<number>(0);
  const { subscribeKlines } = useLive();

  // Create the chart once.
  useEffect(() => {
    if (!container.current) return;
    const c = createChart(container.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: "#10141b" }, textColor: "#8a93a3", fontSize: 11 },
      grid: { vertLines: { color: "#171c25" }, horzLines: { color: "#171c25" } },
      rightPriceScale: { borderColor: "#222936" },
      timeScale: { borderColor: "#222936", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
      autoSize: true,
    });
    candles.current = c.addSeries(CandlestickSeries, {
      upColor: BULL,
      downColor: BEAR,
      borderUpColor: BULL,
      borderDownColor: BEAR,
      wickUpColor: BULL,
      wickDownColor: BEAR,
    });
    zonesPrim.current = new ZonesPrimitive();
    candles.current.attachPrimitive(zonesPrim.current);
    volume.current = c.addSeries(HistogramSeries, { priceScaleId: "volume", priceFormat: { type: "volume" } });
    c.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    emas.current = EMA_COLORS.map((color) =>
      c.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
    );
    markers.current = createSeriesMarkers(candles.current, []);
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
      candles.current = null;
      volume.current = null;
      emas.current = [];
      markers.current = null;
      zonesPrim.current = null;
      lines.current = [];
    };
  }, [height]);

  // Data, overlays, zones, markers.
  useEffect(() => {
    const cs = candles.current;
    if (!cs || !volume.current || !chart.current) return;
    const bars = data.candles.map((b) => ({ ...b, time: b.time as UTCTimestamp }));
    if (data.live_candle) bars.push({ ...data.live_candle, time: data.live_candle.time as UTCTimestamp });
    cs.setData(bars);
    // Price precision follows the instrument's magnitude (e.g. 5 decimals for DOGE, 2 for BTC).
    const ref = bars.length ? bars[bars.length - 1].close : 0;
    const precision = ref > 0 ? Math.min(8, Math.max(2, Math.ceil(-Math.log10(ref)) + 4)) : 2;
    cs.applyOptions({ priceFormat: { type: "price", precision, minMove: 10 ** -precision } });
    lastTime.current = bars.length ? (bars[bars.length - 1].time as number) : 0;
    const firstTime = bars.length ? (bars[0].time as number) : 0;
    volume.current.setData(
      toggles.volume
        ? bars.map((b) => ({ time: b.time, value: b.volume, color: b.close >= b.open ? "#16c78455" : "#ea394355" }))
        : [],
    );
    const overlayKeys = Object.keys(data.overlays);
    emas.current.forEach((s, i) => {
      const key = overlayKeys[i];
      s.setData(toggles.emas && key ? data.overlays[key].map((p) => ({ time: p.time as UTCTimestamp, value: p.value })) : []);
      s.applyOptions({ title: key ? key.toUpperCase() : "" });
    });

    zonesPrim.current?.setBands(zoneBands(data.zones ?? [], data.htf_zones ?? {}, toggles), firstTime);

    lines.current.forEach((l) => cs.removePriceLine(l));
    lines.current = [];
    const priceLine = (price: number, color: string, title: string, style: LineStyle = LineStyle.Solid) =>
      lines.current.push(cs.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, title }));

    // Exact prices of the nearest valid zone boundaries on the price axis.
    if (toggles.zones) {
      const s = data.zones?.find((z) => z.zone_type === "SUPPORT" && z.status !== "INVALIDATED");
      const r = data.zones?.find((z) => z.zone_type === "RESISTANCE" && z.status !== "INVALIDATED");
      if (s) priceLine(s.upper, "#16c78466", "", LineStyle.Dotted);
      if (r) priceLine(r.lower, "#ea394366", "", LineStyle.Dotted);
    }

    const plans: FocusPlan[] = focusPlan
      ? [focusPlan]
      : focusSignal
        ? [planFromSignal(focusSignal)]
        : data.signals.filter((s) => s.evaluation_status === "PENDING").map(planFromSignal);
    if (toggles.signals) {
      for (const p of plans) {
        if (p.label.includes("rejected")) continue; // rejected setups are not trade plans: no lines on the chart
        if (p.entry != null) priceLine(p.entry, "#8a93a3", `Entry ref ${p.direction}`, LineStyle.Dashed);
        if (p.stop != null) priceLine(p.stop, BEAR, "Stop loss");
        for (const t of p.targets) priceLine(t.price, t.blocked ? "#f5a524" : BULL, t.blocked ? `${t.label} (beyond zone)` : t.label);
        if (p.invalidation != null) priceLine(p.invalidation, "#f5a52499", "Invalidation", LineStyle.LargeDashed);
      }
    }

    const m: SeriesMarker<Time>[] = [];
    if (toggles.signals) {
      for (const s of data.signals) {
        if (ts(s.candle_open_time) < firstTime) continue;
        m.push({
          time: ts(s.candle_open_time),
          position: s.direction === "LONG" ? "belowBar" : "aboveBar",
          color: s.direction === "LONG" ? BULL : BEAR,
          shape: s.direction === "LONG" ? "arrowUp" : "arrowDown",
          text: `${s.direction} ${s.score}`,
        });
      }
      if (focusPlan?.confirmationTime && focusPlan.confirmationTime >= firstTime) {
        m.push({
          time: focusPlan.confirmationTime as UTCTimestamp,
          position: focusPlan.direction === "LONG" ? "belowBar" : "aboveBar",
          color: "#f5c542",
          shape: "circle",
          text: `${focusPlan.label} confirmation`,
        });
      }
    }
    if (toggles.swings) {
      for (const sw of data.swings ?? []) {
        if (sw.time < firstTime) continue;
        m.push({
          time: sw.time as UTCTimestamp,
          position: sw.kind === "high" ? "aboveBar" : "belowBar",
          color: sw.kind === "high" ? "#ea394399" : "#16c78499",
          shape: "circle",
          size: 0.5,
        });
      }
    }
    m.sort((a, b) => (a.time as number) - (b.time as number));
    markers.current?.setMarkers(m);
  }, [data, toggles, focusSignal, focusPlan]);

  useEffect(() => {
    chart.current?.timeScale().fitContent();
  }, [data.symbol, data.timeframe]);

  // Live candle updates from the backend WebSocket.
  useEffect(
    () =>
      subscribeKlines((k) => {
        if (k.symbol !== data.symbol || k.timeframe !== data.timeframe || !candles.current) return;
        const t = ts(k.open_time);
        if (t < lastTime.current) return;
        lastTime.current = t;
        candles.current.update({ time: t, open: k.open, high: k.high, low: k.low, close: k.close });
        if (toggles.volume)
          volume.current?.update({ time: t, value: k.volume, color: k.close >= k.open ? "#16c78455" : "#ea394355" });
      }),
    [subscribeKlines, data.symbol, data.timeframe, toggles.volume],
  );

  return <div ref={container} className="w-full" style={{ height: fill ? "100%" : height }} />;
}

export function ChartToggleBar({ toggles, onChange }: { toggles: ChartToggles; onChange: (t: ChartToggles) => void }) {
  const labels: Record<keyof ChartToggles, string> = {
    emas: "EMA 20/50/200",
    zones: "S/R zones",
    htfZones: "1h/4h zones",
    brokenZones: "Broken zones",
    swings: "Swing points",
    volume: "Volume",
    signals: "Signals & SL/TP",
  };
  return (
    <div className="flex flex-wrap gap-3 text-xs">
      {(Object.keys(labels) as (keyof ChartToggles)[]).map((k) => (
        <label key={k} className="flex cursor-pointer items-center gap-1.5 text-muted-foreground">
          <input type="checkbox" className="accent-primary" checked={toggles[k]} onChange={(e) => onChange({ ...toggles, [k]: e.target.checked })} />
          {labels[k]}
        </label>
      ))}
      <span className="text-muted-foreground/70">Green = support, red = resistance (technical classification, not a guarantee). Dashed = broken.</span>
    </div>
  );
}

export function EquityChart({ points, height = 220 }: { points: { time: string; equity: number }[]; height?: number }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current || points.length === 0) return;
    const c = createChart(container.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: "#10141b" }, textColor: "#8a93a3", fontSize: 11 },
      grid: { vertLines: { color: "#171c25" }, horzLines: { color: "#171c25" } },
      rightPriceScale: { borderColor: "#222936" },
      timeScale: { borderColor: "#222936", timeVisible: true },
      autoSize: true,
    });
    const s = c.addSeries(LineSeries, { color: "#3b82f6", lineWidth: 2 });
    // Equity points can share a timestamp; keep the last value per second (chart requires ascending times).
    const byTime = new Map<number, number>();
    for (const p of points) byTime.set(ts(p.time), p.equity);
    s.setData([...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time: time as UTCTimestamp, value })));
    s.createPriceLine({ price: points[0].equity, color: "#8a93a3", lineStyle: LineStyle.Dashed, lineWidth: 1, title: "Start" });
    c.timeScale().fitContent();
    return () => c.remove();
  }, [points, height]);
  return <div ref={container} className="w-full" style={{ height }} />;
}
