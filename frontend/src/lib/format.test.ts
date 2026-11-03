import { describe, expect, it } from "vitest";
import { changeClass, fmtCompact, fmtPct, fmtPrice, fmtTime, fmtUsd } from "./format";

describe("formatting", () => {
  it("formats prices with magnitude-appropriate precision", () => {
    expect(fmtPrice(68500)).toBe("68,500.00");
    expect(fmtPrice(2.34567)).toBe("2.3457");
    expect(fmtPrice(0.000123456)).toBe("0.000123");
  });

  it("never invents values for missing data", () => {
    expect(fmtPrice(null)).toBe("—");
    expect(fmtPrice(Number.NaN)).toBe("—");
    expect(fmtPct(undefined)).toBe("—");
    expect(fmtCompact(null)).toBe("—");
    expect(fmtTime(null)).toBe("—");
  });

  it("formats percentages and currency with sign", () => {
    expect(fmtPct(1.234)).toBe("+1.23%");
    expect(fmtPct(-0.5)).toBe("-0.50%");
    expect(fmtPct(3, 1, false)).toBe("3.0%");
    expect(fmtUsd(-12.5)).toBe("-$12.50");
    expect(fmtCompact(3_395_018_808)).toBe("3.4B");
  });

  it("formats timestamps in UTC", () => {
    expect(fmtTime("2026-11-03T10:15:00Z")).toBe("2026-11-03 10:15 UTC");
  });

  it("colours changes", () => {
    expect(changeClass(1)).toBe("text-bull");
    expect(changeClass(-1)).toBe("text-bear");
    expect(changeClass(0)).toBe("text-muted-foreground");
  });
});
