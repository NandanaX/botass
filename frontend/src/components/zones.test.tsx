import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Zone } from "@/lib/types";
import { DEFAULT_TOGGLES, zoneBands } from "./price-chart";
import { SetupStatusBadge } from "./status";

const zone = (o: Partial<Zone>): Zone => ({
  id: "z", symbol: "BTCUSDT", timeframe: "15m", lower: 100, upper: 101, mid: 100.5, zone_type: "SUPPORT",
  origin: "swing_lows", status: "ACTIVE", tests: 3, rejections: 2, crossings: 0, strength: 70, price_inside: false,
  first_test_time: "2026-11-01T00:00:00Z", last_test_time: "2026-11-02T00:00:00Z", explanation: "", ...o,
});

describe("zoneBands", () => {
  const zones = [
    zone({ id: "s1", lower: 100, upper: 101 }),
    zone({ id: "s2", lower: 95, upper: 96, status: "BROKEN" }),
    zone({ id: "r1", lower: 110, upper: 111, zone_type: "RESISTANCE" }),
  ];

  it("colours support green and resistance red and emphasises the nearest zones", () => {
    const bands = zoneBands(zones, {}, DEFAULT_TOGGLES);
    expect(bands).toHaveLength(2); // the broken (non-nearest) zone is hidden by default
    expect(bands[0].fill).toContain("22,199,132");
    expect(bands[1].fill).toContain("234,57,67");
    expect(bands.every((b) => b.emphasis)).toBe(true);
    expect(bands[0].label).toContain("100.00");
    expect(bands[0].label).toContain("nearest");
  });

  it("shows broken zones dashed when enabled and respects visibility toggles", () => {
    const withBroken = zoneBands(zones, {}, { ...DEFAULT_TOGGLES, brokenZones: true });
    const broken = withBroken.find((b) => b.lower === 95)!;
    expect(broken.dashed).toBe(true);
    expect(broken.label).toContain("broken");
    expect(zoneBands(zones, { "4h": [zone({ id: "h", timeframe: "4h" })] }, { ...DEFAULT_TOGGLES, zones: false })).toHaveLength(1);
    expect(zoneBands(zones, {}, { ...DEFAULT_TOGGLES, zones: false, htfZones: false })).toHaveLength(0);
  });
});

describe("SetupStatusBadge", () => {
  it("renders the qualification status", () => {
    render(<SetupStatusBadge status="NO_SETUP" />);
    expect(screen.getByText("NO SETUP")).toBeInTheDocument();
  });
});
