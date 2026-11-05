import { describe, expect, it } from "vitest";
import { changedPaths, getPath, riskyConfigurations, sameValue, setPath } from "./settings-form";
import type { RuntimeSettings } from "./types";

const base = {
  watchlist: ["BTCUSDT", "ETHUSDT"],
  timeframes: ["15m", "1h"],
  alerts: { telegram_enabled: true, min_score: 60 },
  decision: { mode: "shadow" },
  risk: { risk_per_trade_pct: 1, leverage: 5 },
  strategy: {
    breakout: { enabled: true, min_rvol: 1.2, max_extension_atr: 1.5 },
    qualification: { min_rr: 1.5, min_net_rr: 1.2, reject_outside_volatility: true },
    targets: { r_multiples: [1, 2, 3] },
  },
} as unknown as RuntimeSettings;

describe("paths", () => {
  it("reads and writes nested values without touching the original", () => {
    const next = setPath(base, "strategy.targets.r_multiples.1", 2.5);
    expect(getPath(next, "strategy.targets.r_multiples.1")).toBe(2.5);
    expect(getPath(base, "strategy.targets.r_multiples.1")).toBe(2);
  });
});

describe("change detection", () => {
  it("counts one change per setting", () => {
    let next = setPath(base, "alerts.min_score", 0);
    next = setPath(next, "strategy.targets.r_multiples.2", 4);
    expect(changedPaths(base, next).sort()).toEqual(["alerts.min_score", "strategy.targets.r_multiples.2"]);
    expect(changedPaths(base, structuredClone(base))).toEqual([]);
  });

  it("treats a list of timeframes as a set", () => {
    expect(sameValue(["15m", "1h"], ["1h", "15m"])).toBe(true);
    expect(changedPaths(base, setPath(base, "timeframes", ["1h", "15m"]))).toEqual([]);
    expect(changedPaths(base, setPath(base, "timeframes", ["1h"]))).toEqual(["timeframes"]);
  });
});

describe("risky configurations", () => {
  const paths = (s: RuntimeSettings) => riskyConfigurations(s).map((w) => w.path);

  it("warns about shadow mode and a zero minimum score with the expected wording", () => {
    const w = riskyConfigurations(setPath(base, "alerts.min_score", 0));
    expect(w.map((x) => x.text)).toContain("Minimum signal score is 0 — every qualified setup will be alerted");
    expect(w.map((x) => x.text)).toContain("Decision engine in Shadow mode — nothing is blocked");
  });

  it("flags disabled filters and aggressive sizing, and stays quiet otherwise", () => {
    expect(paths(base)).toEqual(["decision.mode"]);
    expect(paths(setPath(base, "strategy.breakout.min_rvol", 0))).toContain("strategy.breakout.min_rvol");
    expect(paths(setPath(base, "risk.leverage", 15))).toContain("risk.leverage");
    expect(paths(setPath(base, "alerts.telegram_enabled", false))).toContain("alerts.telegram_enabled");
  });
});
