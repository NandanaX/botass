import { describe, expect, it } from "vitest";
import type { OverlaysResponse } from "@/lib/analysis-types";
import { buildAnalysisOverlays, DEFAULT_ANALYSIS_TOGGLES } from "./analysis-overlays";

const ev = (k: number) => ({ kind: "BOS" as const, direction: "BULLISH" as const, index: k, time: "", level: 100 + k, swing_time: "", label: "x", unix: 1000 + k * 60 });
const pattern = (k: number, status: "DEVELOPING" | "CONFIRMED") => ({
  id: `p${k}`, name: `P${k}`, category: "continuation", expected: "BULLISH", status, breakout_direction: status === "CONFIRMED" ? "BULLISH" : null,
  detected_at: "", detected_index: k, resolved_at: null, pivots: [{ time: 1000 + k, price: 1, kind: "high" }],
  lines: [{ t1: 1000, p1: 1, t2: 2000, p2: 2, label: "upper" }], breakout_level: 2, invalidation_level: 1, target: 3, height: 1, explanation: "",
});
const o: OverlaysResponse = {
  symbol: "BTCUSDT", timeframe: "1h", structure_events: Array.from({ length: 20 }, (_, k) => ev(k)), swings: [],
  chart_patterns: [pattern(1, "CONFIRMED"), pattern(2, "CONFIRMED"), pattern(3, "DEVELOPING"), pattern(4, "CONFIRMED")],
  candlestick_patterns: [], trendlines: [], fibonacci: null, fair_value_gaps: [], equal_levels: [], moving_averages: { ema9: [{ time: 1, value: 1 }] },
};

describe("buildAnalysisOverlays", () => {
  it("limits annotations to the most recent ones by default", () => {
    const r = buildAnalysisOverlays(o, DEFAULT_ANALYSIS_TOGGLES);
    expect(r.markers.filter((m) => m.text === "BOS")).toHaveLength(8);
    const labelled = r.shapes.filter((s) => s.kind === "segment" && s.label?.includes("(") && !s.label.includes("target"));
    expect(labelled.map((s) => (s as { label?: string }).label)).toEqual(["P4 (confirmed)", "P3 (developing)", "P2 (confirmed)"]);
    expect(r.lines).toHaveLength(0); // extra moving averages are off by default
  });

  it("respects toggles", () => {
    const none = buildAnalysisOverlays(o, { ...DEFAULT_ANALYSIS_TOGGLES, structure: false, patterns: false });
    expect(none.markers).toHaveLength(0);
    expect(none.shapes).toHaveLength(0);
    expect(buildAnalysisOverlays(o, { ...DEFAULT_ANALYSIS_TOGGLES, movingAverages: true }).lines[0].name).toBe("EMA9");
  });
});
