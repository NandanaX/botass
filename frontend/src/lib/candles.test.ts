import { describe, expect, it } from "vitest";
import { fmtCountdown, nextCandleClose } from "./candles";

const at = (iso: string) => new Date(iso).getTime();

describe("nextCandleClose", () => {
  it("rounds up to the end of the running candle", () => {
    expect(nextCandleClose("15m", at("2026-11-04T17:07:30Z"))).toBe(at("2026-11-04T17:15:00Z"));
    expect(nextCandleClose("4h", at("2026-11-04T17:07:30Z"))).toBe(at("2026-11-04T20:00:00Z"));
    expect(nextCandleClose("1d", at("2026-11-04T17:07:30Z"))).toBe(at("2026-11-05T00:00:00Z"));
  });

  it("starts the next candle exactly on a boundary", () => {
    expect(nextCandleClose("15m", at("2026-11-04T17:15:00Z"))).toBe(at("2026-11-04T17:30:00Z"));
  });

  it("closes weekly candles on Monday 00:00 UTC", () => {
    expect(nextCandleClose("1w", at("2026-11-04T17:07:30Z"))).toBe(at("2026-11-09T00:00:00Z"));
    expect(new Date(nextCandleClose("1w", at("2026-11-09T00:00:00Z"))).getUTCDay()).toBe(1);
  });
});

describe("fmtCountdown", () => {
  it("uses the shortest readable form and never goes negative", () => {
    expect(fmtCountdown(272_000)).toBe("04:32");
    expect(fmtCountdown(3_872_000)).toBe("1:04:32");
    expect(fmtCountdown(2 * 86_400_000 + 11_564_000)).toBe("2d 03:12:44");
    expect(fmtCountdown(-5000)).toBe("00:00");
  });
});
