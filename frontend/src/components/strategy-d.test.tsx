import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { DContext, DOverview } from "@/lib/strategy-d-types";
import { BiasText, DecisionBadge, DrawdownReset, fmtR, HtfSummary, SegmentsTable, StrategyDLatestCard, StudyTable } from "./strategy-d";

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

describe("manual drawdown reset", () => {
  const segment = (n: number, extra = {}) => ({ segment: n, evaluation_version: `D-1/segment-${n}`, started_at: "2026-11-06T00:00:00+00:00", ended_at: null, start_equity: 10000, end_equity: null, reset_reason: null, reset_by: null, halted_at: null, halted_equity: null, halted_drawdown_pct: null, taken: 3, open: 0, closed: 3, avg_net_r: -1, total_net_r: -3, win_rate_pct: 0, ...extra });
  const overview = (tripped: boolean, extra = {}) => ({ segment: 1, reset_confirm_phrase: "RESET D SEGMENT 1", carried_over_open: 0, breakers: { max_drawdown: { tripped, value_pct: tripped ? 11 : 1, limit_pct: 10, reset_required: tripped } }, segments: [segment(1)], ...extra }) as unknown as DOverview;
  const type = (label: RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

  it("offers nothing while the breaker hasn't tripped", () => {
    wrap(<DrawdownReset overview={overview(false)} />);
    expect(screen.queryByTestId("strategy-d-halted")).toBeNull();
  });

  it("stays disabled until a reason, a reviewer and the exact phrase are given, then sends them once", async () => {
    const reset = vi.spyOn(api, "strategyDResetDrawdown").mockResolvedValue({ reset: true, segment: 2, evaluation_version: "D-1/segment-2", reset_at: "x", start_equity: 8900 });
    wrap(<DrawdownReset overview={overview(true)} />);
    const button = screen.getByRole("button", { name: /Reset and start segment 2/ });
    expect(button).toBeDisabled();
    type(/Reason/, "short");
    type(/Reviewer/, "Nandana");
    type(/to confirm/, "RESET D SEGMENT 1");
    expect(button).toBeDisabled();
    type(/Reason/, "reviewed the losses");
    type(/to confirm/, "reset d segment 1");
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(reset).not.toHaveBeenCalled();
    type(/to confirm/, "RESET D SEGMENT 1");
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(reset).toHaveBeenCalledTimes(1));
    expect(reset).toHaveBeenCalledWith({ reason: "reviewed the losses", reviewer: "Nandana", confirm: "RESET D SEGMENT 1" });
  });

  it("shows a refusal from the server", async () => {
    vi.spyOn(api, "strategyDResetDrawdown").mockRejectedValue(new Error("The drawdown breaker hasn't tripped"));
    wrap(<DrawdownReset overview={overview(true)} />);
    type(/Reason/, "reviewed the losses");
    type(/Reviewer/, "Nandana");
    type(/to confirm/, "RESET D SEGMENT 1");
    fireEvent.click(screen.getByRole("button", { name: /Reset and start/ }));
    expect(await screen.findByText(/hasn't tripped/)).toBeInTheDocument();
  });

  it("lists each segment on its own row, with the halt and the reason, and never a combined total", () => {
    wrap(<SegmentsTable overview={overview(false, { segment: 2, carried_over_open: 1, segments: [segment(1, { halted_at: "2026-11-05T00:00:00+00:00", halted_drawdown_pct: 10.4 }), segment(2, { reset_reason: "reviewed the losses", reset_by: "Nandana", closed: 1, total_net_r: 0.5, avg_net_r: 0.5 })] })} />);
    expect(screen.getByText("2 (current)")).toBeInTheDocument();
    expect(screen.getByText(/at 10.4%/)).toBeInTheDocument();
    expect(screen.getByText("reviewed the losses (Nandana)")).toBeInTheDocument();
    expect(screen.getByText("-3.00R")).toBeInTheDocument();
    expect(screen.getAllByText("+0.50R")).toHaveLength(2);
    expect(screen.queryByText("-2.50R")).toBeNull();
    expect(screen.getByText(/entered before the last reset/)).toBeInTheDocument();
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
