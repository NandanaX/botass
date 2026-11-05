import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Zone } from "@/lib/types";
import { zoomRange } from "./chart-wheel";
import { LABEL_HEIGHT, visibleLabels } from "./zones-primitive";
import { nearestActive, zoneDistancePct, ZoneList } from "./zones";

const zone = (o: Partial<Zone>): Zone => ({
  id: "z", symbol: "BTCUSDT", timeframe: "15m", lower: 100, upper: 101, mid: 100.5, zone_type: "SUPPORT",
  origin: "swing_lows", status: "ACTIVE", tests: 3, rejections: 2, crossings: 0, strength: 70, price_inside: false,
  first_test_time: "2026-11-01T00:00:00Z", last_test_time: "2026-11-02T00:00:00Z", explanation: "", ...o,
});

describe("visibleLabels", () => {
  const item = (y: number, emphasis = false, dashed = false) => ({ y, emphasis, dashed });

  it("hides labels that would overlap and keeps those far apart", () => {
    expect([...visibleLabels([item(100), item(105), item(100 + LABEL_HEIGHT)])].sort()).toEqual([0, 2]);
  });

  it("gives priority to the nearest zones, then to solid over dashed", () => {
    expect([...visibleLabels([item(100), item(102, true)])]).toEqual([1]);
    expect([...visibleLabels([item(100, false, true), item(102)])]).toEqual([1]);
  });
});

describe("zoomRange", () => {
  it("zooms in around the cursor and keeps it in place", () => {
    const r = zoomRange({ from: 0, to: 100 }, 80, -100);
    expect(r.to - r.from).toBeLessThan(100);
    expect((80 - r.from) / (r.to - r.from)).toBeCloseTo(0.8);
  });

  it("zooms out on a positive delta and stops at a minimum number of bars", () => {
    const r = zoomRange({ from: 0, to: 100 }, 50, 100);
    expect(r.to - r.from).toBeGreaterThan(100);
    expect(zoomRange({ from: 0, to: 10 }, 5, -100)).toEqual({ from: 0, to: 10 });
  });
});

describe("zone list", () => {
  const zones = [
    zone({ id: "far", lower: 90, upper: 91, status: "ACTIVE" }),
    zone({ id: "near", lower: 110, upper: 111, zone_type: "RESISTANCE", status: "ACTIVE" }),
    zone({ id: "old", lower: 104, upper: 105, status: "BROKEN" }),
    zone({ id: "dead", lower: 106, upper: 107, zone_type: "RESISTANCE", status: "INVALIDATED" }),
  ];

  it("lists active zones nearest first with an S or R badge, and history collapsed", () => {
    const { container } = render(<ZoneList zones={zones} price={103} />);
    const active = [...container.querySelectorAll("[data-zone-status=ACTIVE]")];
    expect(active.map((e) => e.textContent?.includes("110.00"))).toEqual([true, false]);
    expect(screen.getByText("History (2)")).toBeInTheDocument();
    expect(container.querySelector("details")?.open).toBe(false);
    expect(screen.getAllByLabelText("Resistance").length).toBeGreaterThan(0);
    expect(screen.getByTestId("zone-list-price")).toHaveTextContent("103");
  });

  it("says when none are active and reports distance", () => {
    render(<ZoneList zones={[zones[2]]} price={103} />);
    expect(screen.getByText("None active.")).toBeInTheDocument();
    expect(zoneDistancePct({ lower: 100, upper: 102 }, 101)).toBe(0);
    expect(zoneDistancePct({ lower: 110, upper: 111 }, 100)).toBeCloseTo(10);
  });

  it("uses only active zones for nearest", () => {
    expect(nearestActive([zone({ status: "BROKEN", id: "a" }), zone({ id: "b" })], "SUPPORT")?.id).toBe("b");
    expect(nearestActive([zone({ status: "BROKEN" })], "SUPPORT")).toBeNull();
  });
});
