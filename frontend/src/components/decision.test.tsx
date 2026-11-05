import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Field, Input, Switch } from "@/components/ui/form";
import { setTzChoice } from "@/lib/timezone";
import type { DecisionDiagnostics, ScannerRow, SetupResponse } from "@/lib/types";
import { decisionState, RuleChecklist, TriggerLines } from "./decision";
import { closestToTriggering } from "./signals-empty";

afterEach(cleanup);

const response = (decision: string, direction?: "LONG" | "SHORT", ready = true) =>
  ({ decision, primary_setup: direction ? { direction } : null, market: { ready, not_ready_reason: "insufficient history" } }) as unknown as SetupResponse;

describe("decisionState", () => {
  it("gives each engine decision its own headline", () => {
    expect(decisionState(response("NO_SETUP")).title).toBe("No setup");
    expect(decisionState(response("WATCHLIST", "LONG")).title).toBe("Watchlist long");
    expect(decisionState(response("WATCHLIST", "SHORT")).title).toBe("Watchlist short");
    expect(decisionState(response("QUALIFIED", "LONG"))).toMatchObject({ title: "Long", tone: "bull" });
    expect(decisionState(response("QUALIFIED", "SHORT"))).toMatchObject({ title: "Short", tone: "bear" });
    expect(decisionState(response("REJECTED", "LONG"))).toMatchObject({ title: "Rejected long", tone: "warn" });
    expect(decisionState(response("NO_SETUP", undefined, false)).title).toBe("Warming up");
  });

  it("never calls a signal an open position or a rejected setup a signal", () => {
    expect(decisionState(response("QUALIFIED", "LONG")).note).toMatch(/not an open position/);
    expect(decisionState(response("REJECTED", "LONG")).note).toMatch(/not a trade signal/);
  });
});

const rule = (key: string, state: "pass" | "fail" | "na", value: string, threshold = "", advisory = false) =>
  ({ key, group: "Trigger", label: key, state, value, threshold, advisory });

const diagnostics: DecisionDiagnostics = {
  ready: true,
  reason: null,
  trend_direction: "SHORT",
  triggers: [{ strategy: "pullback", direction: "SHORT", text: "Short pullback if price retests 100–101 and a 15m candle closes back below it." }],
  strategies: [
    { strategy: "breakout", label: "A · Breakout", direction: "LONG", enabled: true, status: "NO_SETUP", rules: [rule("long_only", "fail", "x")] },
    { strategy: "breakout", label: "A · Breakout", direction: "SHORT", enabled: true, status: "NO_SETUP", rules: [rule("rvol", "fail", "0.80x", "min 1.2x"), rule("net_rr", "na", "needs a trigger candle", "min 1.2"), rule("ema", "pass", "1 vs 2")] },
    { strategy: "pullback", label: "B · Pullback / retest", direction: "LONG", enabled: true, status: "NO_SETUP", rules: [] },
    { strategy: "pullback", label: "B · Pullback / retest", direction: "SHORT", enabled: true, status: "WATCHLIST", rules: [rule("vol", "fail", "4%", "0.05–3%", true)] },
  ],
};

describe("RuleChecklist", () => {
  it("opens on the trend's direction and shows value against requirement for each strategy", () => {
    render(<RuleChecklist diagnostics={diagnostics} />);
    const a = screen.getByRole("region", { name: "A · Breakout rules" });
    expect(within(a).getByText(/0\.80x/)).toHaveTextContent("0.80x / min 1.2x");
    expect(within(a).getByRole("img", { name: "Failed" })).toBeInTheDocument();
    expect(within(a).getByRole("img", { name: "Passed" })).toBeInTheDocument();
    expect(within(a).getByRole("img", { name: "Not evaluated yet" })).toBeInTheDocument();
    expect(within(a).getByText("1 of 3 met")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "B · Pullback / retest rules" })).toHaveTextContent("WATCHLIST");
    expect(screen.queryByText("long_only")).toBeNull();
  });

  it("marks warnings apart from failures and switches direction", () => {
    render(<RuleChecklist diagnostics={diagnostics} />);
    expect(screen.getByRole("img", { name: "Warning only" })).toBeInTheDocument();
    // Radix tabs select on mouse down.
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Long" }), { button: 0 });
    expect(screen.getByText("long_only")).toBeInTheDocument();
    expect(screen.getByText(/so this side can't trigger now/)).toBeInTheDocument();
  });

  it("states the times in the display zone", () => {
    setTzChoice("UTC");
    render(<RuleChecklist diagnostics={diagnostics} closeTime="2026-11-04T17:15:00Z" />);
    expect(screen.getByText("2026-11-04 17:15")).toHaveAttribute("datetime", "2026-11-04T17:15:00Z");
  });
});

describe("TriggerLines", () => {
  it("shows the engine's plain-language triggers", () => {
    render(<TriggerLines diagnostics={diagnostics} />);
    expect(screen.getByText(/Short pullback if price retests 100–101/)).toBeInTheDocument();
  });
});

describe("closestToTriggering", () => {
  const row = (symbol: string, signal_status: string, trend: string, lower: number) =>
    ({ symbol, timeframe: "15m", signal_status, trend, close: 100, nearest_support: { lower, upper: lower + 1, tests: 2, status: "ACTIVE" }, nearest_resistance: null }) as unknown as ScannerRow;

  it("puts forming setups first, then the nearest trend-aligned market, and skips mixed trends", () => {
    const out = closestToTriggering([row("FAR", "NONE", "BULLISH", 50), row("NEAR", "NONE", "BEARISH", 98), row("WATCH", "WATCH_LONG", "BULLISH", 70), row("MIXED", "NONE", "NEUTRAL", 99)], () => 100);
    expect(out.map((r) => r.row.symbol)).toEqual(["WATCH", "NEAR", "FAR"]);
    expect(out[1].distancePct).toBeCloseTo(1);
    expect(out[1].zone?.kind).toBe("support");
  });
});

describe("Field", () => {
  it("names its control from the visible label", () => {
    render(
      <>
        <Field label="Risk %"><Input /></Field>
        <Field label="Include funding"><Switch /></Field>
      </>,
    );
    expect(screen.getByLabelText("Risk %").tagName).toBe("INPUT");
    expect(screen.getByRole("switch", { name: "Include funding" })).toBeInTheDocument();
  });

  it("toggles a switch from its label", () => {
    render(<Field label="Include funding"><Switch /></Field>);
    const sw = screen.getByRole("switch", { name: "Include funding" });
    fireEvent.click(screen.getByText("Include funding"));
    expect(sw).toHaveAttribute("data-state", "checked");
  });
});
