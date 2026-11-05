import { describe, expect, it } from "vitest";
import { changeClass, fmtClock, fmtCompact, fmtPct, fmtPrice, fmtTime, fmtUsd, otherTime } from "./format";
import { LOCAL_TZ, setTzChoice, tzLabel } from "./timezone";

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

  it("formats timestamps in the display time zone, with the other time available", () => {
    setTzChoice("Asia/Colombo");
    expect(fmtTime("2026-11-03T10:15:00Z")).toBe("2026-11-03 15:45");
    expect(otherTime("2026-11-03T10:15:00Z")).toBe("2026-11-03 10:15:00 UTC");
    expect(tzLabel()).toBe("Asia/Colombo (UTC+05:30)");
    setTzChoice("UTC");
    expect(fmtTime("2026-11-03T10:15:00Z")).toBe("2026-11-03 10:15");
    expect(fmtClock("2026-11-03T10:15:00Z", true)).toBe("03 Nov, 10:15");
    expect(tzLabel()).toBe("UTC");
    setTzChoice("Not/AZone"); // ignored
    expect(tzLabel()).toBe("UTC");
    setTzChoice(LOCAL_TZ);
  });

  it("colours changes", () => {
    expect(changeClass(1)).toBe("text-bull");
    expect(changeClass(-1)).toBe("text-bear");
    expect(changeClass(0)).toBe("text-muted-foreground");
  });
});
