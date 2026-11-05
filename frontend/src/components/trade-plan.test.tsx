import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { PlannedSetup, PositionInput, PositionPlan, TradePlanResponse } from "@/lib/planning-types";
import { fmtPlanPrice, fmtQuantity, readMultiples, readPositive, TradePlanCard } from "./trade-plan";

vi.mock("@/lib/live", () => ({ useTicker: () => ({ available: true, last_price: 100.5 }) }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("formatting helpers", () => {
  it("uses exchange precision for prices", () => {
    expect(fmtPlanPrice(97123.4, 1)).toBe("97,123.4");
    expect(fmtPlanPrice(0.91, 4)).toBe("0.9100");
    expect(fmtPlanPrice(null, 2)).toBe("—");
  });

  it("truncates quantities so they never show more than the real size", () => {
    expect(fmtQuantity(0.12399, 3)).toBe("0.123");
    expect(fmtQuantity(25, 0)).toBe("25");
    expect(fmtQuantity(0.3, 1)).toBe("0.3");
  });

  it("treats blank or non-positive input as not entered", () => {
    expect(readPositive("")).toBeNull();
    expect(readPositive("-1")).toBeNull();
    expect(readPositive("0")).toBeNull();
    expect(readPositive("2.5")).toBe(2.5);
  });

  it("needs increasing targets within range", () => {
    expect(readMultiples(["1", "2", "3"])).toEqual([1, 2, 3]);
    expect(readMultiples(["2", "2", "3"])).toBeNull();
    expect(readMultiples(["1", "", "3"])).toBeNull();
    expect(readMultiples(["1", "2", "21"])).toBeNull();
  });
});

const gate = (status: PlannedSetup["gate"]["status"], reasons: { code: string; message: string }[] = []): PlannedSetup["gate"] => ({
  status, definition: "x", reasons, room_r: 3, net_r_at_primary: 1.8,
  checks: [{ key: "room", label: "Room of at least 1.5R", passed: true, detail: "3.00R" }],
});

const setup = (over: Partial<PlannedSetup> = {}): PlannedSetup => ({
  direction: "LONG", strategy: "pullback", production_status: "QUALIFIED", research_version: "Original production stop", stop_method: "A_original",
  entry_reference: 100, entry_zone: [99.5, 100], atr: 2,
  stop: { method: "A_original", label: "Original production stop", valid: true, price: 98, distance: 2, distance_pct: 2, distance_atr: 1, level: null, justification: "Zone boundary." },
  stop_variants: [{ method: "A_original", label: "Original production stop", valid: true, price: 98, distance: 2, distance_pct: 2, distance_atr: 1, level: null, justification: "Zone", gate_status: "QUALIFIED", room_r: 3, net_r_at_primary: 1.8 }],
  targets: [
    { label: "TP1", r_multiple: 1, price: 102, valid: true, feasibility: "clear", note: null, distance: 2, distance_pct: 2, distance_atr: 1, gross_r: 1, net_r: 0.9 },
    { label: "TP2", r_multiple: 2, price: 104, valid: true, feasibility: "clear", note: null, distance: 4, distance_pct: 4, distance_atr: 2, gross_r: 2, net_r: 1.8 },
    { label: "TP3", r_multiple: 3, price: 106, valid: true, feasibility: "blocked", note: "Beyond resistance", distance: 6, distance_pct: 6, distance_atr: 3, gross_r: 3, net_r: 2.7 },
  ],
  structural_levels: [], gate: gate("QUALIFIED"), invalidation: ["A candle closes below 99."],
  reasoning: { technical_evidence: ["Setup: pullback long."], entry_quality: "x", stop_justification: "Zone boundary.", take_profit_feasibility: "TP3 beyond.", net_risk_reward: "TP2 pays 1.80R.", correlation_exposure: "None open.", rejection_reasons: [] },
  evidence: { status: "Unvalidated", note: "Unvalidated: no forward results.", gate_support: "On past data, skipping this class did not reliably improve results.", historical: { label: "Historical simulation", trades: 1000, avg_net_r: -0.2, avg_net_r_ci95: [-0.3, -0.1], win_rate_pct: 30, max_drawdown_r: 100, stop_out_pct: 60 } },
  ...over,
});

const response = (setups: PlannedSetup[]): TradePlanResponse => ({
  symbol: "BTCUSDT", timeframe: "1h", candle_close_time: "2026-11-05T14:00:00Z", ready: true, not_ready_reason: null, setups, price_decimals: 1,
  gate_definitions: {} as TradePlanResponse["gate_definitions"], stop_methods: { A_original: "Original production stop", B_atr_2_5: "2.5 ATR" },
  label: "Experimental research plan.", support: { lower: 95, upper: 96, timeframe: "1h" }, resistance: { lower: 110, upper: 111, timeframe: "1h" },
  ticker: { available: true, last_price: 100.5 },
  defaults: { equity: 10000, risk_pct: 0.5, leverage: 5, max_leverage: 20, entry_fee_pct: 0.055, exit_fee_pct: 0.055, entry_slippage_pct: 0.02, stop_slippage_pct: 0.05, maintenance_margin_pct: 0.5, max_open_risk_pct: 2, open_risk_usdt: 0 },
  open_positions: [],
});

/** Mirrors the backend closely enough to prove that inputs reach the calculation. */
const position = (b: PositionInput): PositionPlan => {
  const budget = b.equity * b.risk_pct / 100;
  const qty = budget / Math.abs(b.entry - b.stop);
  return {
    feasible: true, risk_budget: budget, risk_pct: b.risk_pct, quantity: qty, quantity_decimals: 3, base_coin: "BTC", min_order_quantity: 0.001, notional: qty * b.entry, leverage: b.leverage,
    initial_margin: qty * b.entry / b.leverage, effective_exposure_x: qty * b.entry / b.equity, price_risk: budget, planned_loss: budget, planned_loss_pct_of_equity: b.risk_pct,
    estimated_entry_fee: 1, estimated_stop_fee: 1, liquidation_price: 81, max_safe_leverage: 40,
    targets: b.targets.map((t) => ({ label: t.label, price: t.price, gross_profit: qty * (t.price - b.entry), fees: 1, net_profit: qty * (t.price - b.entry) - 1, net_rr: 1.8 })),
    stop_gap_stress: [{ gap_pct: 1, loss: budget * 1.5, pct_of_equity: 0.75 }], issues: [], warnings: [], note: "Planned loss is an estimate, not a maximum.",
  };
};

function show(setups: PlannedSetup[]) {
  vi.spyOn(api, "tradePlan").mockResolvedValue(response(setups));
  const calc = vi.spyOn(api, "positionPlan").mockImplementation(async (b) => position(b));
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TradePlanCard symbol="BTCUSDT" timeframe="1h" /></QueryClientProvider>);
  return calc;
}

describe("TradePlanCard", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the plan with the paper default of 0.5% risk and never a probability", async () => {
    const calc = show([setup()]);
    expect(await screen.findByText("Suggested quantity")).toBeTruthy();
    expect(calc.mock.calls[0][0]).toMatchObject({ equity: 10000, risk_pct: 0.5, leverage: 5, stop: 98, entry: 100 });
    const pos = within(screen.getByTestId("plan-position"));
    expect(pos.getByText("25.000 BTC")).toBeTruthy();
    expect(pos.getByText("$2,500.00")).toBeTruthy();
    expect(pos.getByText("$50.00", { selector: ".text-bear" })).toBeTruthy();
    const text = screen.getByTestId("trade-plan").textContent ?? "";
    expect(text).not.toMatch(/probability|win chance|confidence|score/i);
    expect(screen.getByTestId("plan-evidence").textContent).toContain("Unvalidated");
  });

  it("recalculates when equity or risk changes", async () => {
    const calc = show([setup()]);
    await screen.findByText("Suggested quantity");
    fireEvent.change(screen.getByLabelText("Equity (USDT)"), { target: { value: "20000" } });
    fireEvent.change(screen.getByLabelText("Risk %"), { target: { value: "1" } });
    await waitFor(() => expect(calc.mock.calls.at(-1)?.[0]).toMatchObject({ equity: 20000, risk_pct: 1 }));
    expect(await within(screen.getByTestId("plan-position")).findByText("100.000 BTC")).toBeTruthy();
  });

  it("doesn't apply leverage over the maximum and says so", async () => {
    const calc = show([setup()]);
    await screen.findByText("Suggested quantity");
    fireEvent.change(screen.getByLabelText(/Leverage/), { target: { value: "50" } });
    expect((await screen.findByRole("alert")).textContent).toMatch(/above your maximum/);
    expect(calc.mock.calls.every(([b]) => b.leverage <= 20)).toBe(true);
  });

  it("keeps the last valid targets and warns when they don't increase", async () => {
    show([setup()]);
    await screen.findByText("Suggested quantity");
    const before = vi.mocked(api.tradePlan).mock.calls.length;
    fireEvent.change(screen.getByLabelText("TP2 (R)"), { target: { value: "0.5" } });
    expect((await screen.findByRole("alert")).textContent).toMatch(/must increase/);
    expect(vi.mocked(api.tradePlan).mock.calls.length).toBe(before);
  });

  it("asks for a new plan when the stop method changes", async () => {
    show([setup()]);
    await screen.findByText("Suggested quantity");
    fireEvent.change(screen.getByLabelText("Stop method"), { target: { value: "B_atr_2_5" } });
    await waitFor(() => expect(vi.mocked(api.tradePlan).mock.calls.at(-1)?.[2]).toBe("B_atr_2_5"));
  });

  it("gives no quantity for a setup production rejected", async () => {
    const calc = show([setup({ production_status: "REJECTED", gate: gate("INSUFFICIENT_REWARD", [{ code: "room_too_small", message: "Only 0.80R of room." }]) })]);
    expect(await screen.findByText(/Not a trade signal/)).toBeTruthy();
    expect(screen.getByTestId("plan-rejections").textContent).toContain("Only 0.80R of room");
    expect(screen.queryByTestId("plan-position")).toBeNull();
    expect(calc).not.toHaveBeenCalled();
  });

  it("marks a skipped setup as experimental and shows why", async () => {
    show([setup({ gate: gate("OVEREXTENDED", [{ code: "overextended", message: "Price is 2.00 ATR past EMA-fast." }]) })]);
    expect(await screen.findByText(/experimental gate would skip/)).toBeTruthy();
    expect(screen.getByTestId("plan-rejections").textContent).toContain("2.00 ATR");
    expect(screen.getByTestId("plan-gate-support").textContent).toMatch(/did not reliably improve/);
  });

  it("explains a refused stop instead of sizing it", async () => {
    const calc = show([setup({ stop: { method: "E_structural", label: "Structural", valid: false, price: null, distance: null, distance_pct: null, distance_atr: null, level: null, justification: "No confirmed structure." }, targets: [] })]);
    expect(await screen.findByText("No confirmed structure.")).toBeTruthy();
    expect(calc).not.toHaveBeenCalled();
  });

  it("works for a short", async () => {
    const calc = show([setup({ direction: "SHORT", stop: { ...setup().stop, price: 102 }, targets: [{ label: "TP1", r_multiple: 1, price: 98, valid: true, feasibility: "clear", note: null, distance_pct: 2, distance_atr: 1, gross_r: 1, net_r: 0.9 }] })]);
    await screen.findByText("Suggested quantity");
    expect(calc.mock.calls[0][0]).toMatchObject({ direction: "SHORT", stop: 102 });
  });

  it("forces nothing when there is no setup", async () => {
    show([]);
    expect((await screen.findByTestId("plan-empty")).textContent).toMatch(/none is forced/);
  });
});
