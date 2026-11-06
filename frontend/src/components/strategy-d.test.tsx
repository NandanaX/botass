import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { DContext, DOverview } from "@/lib/strategy-d-types";
import { BiasText, DecisionBadge, fmtR, HtfSummary, StrategyDLatestCard, StudyTable } from "./strategy-d";

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const state = (bias: string, pullback = false) => ({ timeframe: "x", bias, trend: 0, structure: 0, momentum: 0, adx: 25, rsi: 50, last_break: null, pullback, why: "because" });

const context: DContext = {
  symbol: "BTCUSDT", available: true, decision_time: "2026-11-06T00:15:00+00:00", price: 100, direction: "LONG",
  states: { "1d": state("BULLISH"), "4h": state("BEARISH"), "1h": state("BULLISH", true), "15m": state("SIDEWAYS") } as DContext["states"],
  levels: { "1h": { support: 95, resistance: 105 }, "4h": { support: 90, resistance: null } },
  alignment: { score: 70, out_of: 100, note: "Rule agreement across timeframes. Not a probability of success." },
  conflicts: ["4H is bearish"], d_decisions: [{ direction: "LONG", decision: "WATCHLIST", reason: "Alignment too low", score: 60, entry_type: "breakout" }],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("HtfSummary", () => {
  it("shows every timeframe's bias, conflicts, levels and D's decision for the candle", async () => {
    vi.spyOn(api, "strategyDContext").mockResolvedValue(context);
    wrap(<HtfSummary symbol="BTCUSDT" at="2026-11-06T00:15:00+00:00" direction="LONG" />);
    expect(await screen.findByText("Higher-timeframe context")).toBeInTheDocument();
    expect(screen.getByText("1D bias")).toBeInTheDocument();
    expect(screen.getByText("Bearish")).toBeInTheDocument();
    expect(screen.getByText("(pullback)")).toBeInTheDocument();
    expect(screen.getByText("4H is bearish")).toBeInTheDocument();
    expect(screen.getByText(/Not a probability of success/)).toBeInTheDocument();
    expect(screen.getByText(/Alignment too low/)).toBeInTheDocument();
    expect(api.strategyDContext).toHaveBeenCalledWith("BTCUSDT", "2026-11-06T00:15:00+00:00", "LONG");
  });

  it("says so when no decision was recorded and when data is unavailable", async () => {
    vi.spyOn(api, "strategyDContext").mockResolvedValue({ ...context, d_decisions: [] });
    const { unmount } = wrap(<HtfSummary symbol="BTCUSDT" />);
    expect(await screen.findByText(/None recorded for this candle/)).toBeInTheDocument();
    unmount();
    vi.spyOn(api, "strategyDContext").mockResolvedValue({ symbol: "BTCUSDT", available: false, reason: "The candle history doesn't reach this time." });
    wrap(<HtfSummary symbol="BTCUSDT" at="2020-01-01T00:00:00Z" />);
    expect(await screen.findByText(/doesn't reach this time/)).toBeInTheDocument();
  });
});

describe("StrategyDLatestCard", () => {
  const overview = { evidence: "Unvalidated", latest: [] } as unknown as DOverview;

  it("labels itself as research and waits for a candle instead of inventing a decision", async () => {
    vi.spyOn(api, "strategyDOverview").mockResolvedValue(overview);
    wrap(<StrategyDLatestCard />);
    expect(await screen.findByText("Research shadow")).toBeInTheDocument();
    expect(screen.getByText("Unvalidated")).toBeInTheDocument();
    expect(screen.getByText(/Waiting for the next 15m candle/)).toBeInTheDocument();
  });

  it("shows no trade as a decision", async () => {
    vi.spyOn(api, "strategyDOverview").mockResolvedValue({ ...overview, latest: [{ symbol: "ETHUSDT", decision_time: "x", biases: { "1d": "BULLISH", "4h": "SIDEWAYS", "1h": "CONFLICTED", "15m": "INSUFFICIENT_DATA" }, decision: "NO_TRADE", direction: null, reason: "No confirmed 15m setup on this candle." }] });
    wrap(<StrategyDLatestCard />);
    expect(await screen.findByText("No trade")).toBeInTheDocument();
    expect(screen.getByText("No data")).toBeInTheDocument();
  });
});

describe("display helpers", () => {
  it("formats R with its sign and leaves missing values empty", () => {
    expect(fmtR(0.123)).toBe("+0.12R");
    expect(fmtR(-1.5)).toBe("-1.50R");
    expect(fmtR(null)).toBe("—");
  });

  it("renders bias and decision labels", () => {
    render(<><BiasText bias="CONFLICTED" /><DecisionBadge decision="QUALIFIED" /></>);
    expect(screen.getByText("Conflicted")).toBeInTheDocument();
    expect(screen.getByText("Qualified")).toBeInTheDocument();
  });

  it("study table tolerates a rule that produced no trades", () => {
    render(<StudyTable rows={[{ key: "x", label: "Empty rule", row: { n: 0, summary: {} } }, { key: "y", label: "Missing", row: undefined }]} />);
    expect(screen.getByText("Empty rule")).toBeInTheDocument();
    expect(screen.getByText("Missing")).toBeInTheDocument();
  });
});
