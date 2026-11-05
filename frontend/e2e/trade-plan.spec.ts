import { existsSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";

/** axe-core arrives with the ESLint plugins, so it isn't a direct dependency. */
const AXE = "node_modules/axe-core/axe.min.js";

async function axeViolations(page: Page, selector: string): Promise<string[]> {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async (sel) => {
    const axe = (window as unknown as { axe: { run: (c: string, o: object) => Promise<{ violations: { id: string; nodes: { target: unknown[] }[] }[] }> } }).axe;
    const res = await axe.run(sel, { runOnly: ["wcag2a", "wcag2aa"] });
    return res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  }, selector);
}

/** The plan is fixed so the test doesn't depend on a setup existing right now. The position calculator
 * and the page are real. */
const planned = {
  direction: "SHORT", strategy: "pullback", production_status: "QUALIFIED", research_version: "Original production stop", stop_method: "A_original",
  entry_reference: 80000, entry_zone: [79900, 80000], atr: 800,
  stop: { method: "A_original", label: "Original production stop", valid: true, price: 81000, distance: 1000, distance_pct: 1.25, distance_atr: 1.25, level: null, justification: "The stop the production signal uses." },
  stop_variants: [
    { method: "A_original", label: "Original production stop", valid: true, price: 81000, distance: 1000, distance_pct: 1.25, distance_atr: 1.25, level: null, justification: "Zone.", gate_status: "OVEREXTENDED", room_r: 4, net_r_at_primary: 1.78 },
    { method: "E_structural", label: "Structural + 0.5 ATR buffer", valid: false, price: null, distance: null, distance_pct: null, distance_atr: null, level: null, justification: "No confirmed structure.", gate_status: "REJECTED", room_r: null, net_r_at_primary: null },
  ],
  targets: [
    { label: "TP1", r_multiple: 1, price: 79000, valid: true, feasibility: "clear", note: "In front of the nearest support.", distance: 1000, distance_pct: 1.25, distance_atr: 1.25, gross_r: 1, net_r: 0.89 },
    { label: "TP2", r_multiple: 2, price: 78000, valid: true, feasibility: "clear", note: "In front of the nearest support.", distance: 2000, distance_pct: 2.5, distance_atr: 2.5, gross_r: 2, net_r: 1.78 },
    { label: "TP3", r_multiple: 3, price: 77000, valid: true, feasibility: "blocked", note: "Beyond the nearest support.", distance: 3000, distance_pct: 3.75, distance_atr: 3.75, gross_r: 3, net_r: 2.77 },
  ],
  structural_levels: [{ kind: "opposing_zone", label: "Nearest opposing zone (1h)", price: 77500, distance: 2500, distance_pct: 3.13, distance_atr: 3.13, gross_r: 2.5, net_r: 2.28 }],
  gate: { status: "OVEREXTENDED", definition: "x", reasons: [{ code: "overextended", message: "Price is 1.80 ATR past EMA-fast." }], checks: [{ key: "room", label: "Room of at least 1.5R", passed: true, detail: "2.50R" }, { key: "extension", label: "Under 1.5 ATR past EMA-fast", passed: false, detail: "1.80 ATR" }], room_r: 2.5, net_r_at_primary: 1.78 },
  invalidation: ["A candle closes above 80,500."],
  reasoning: { technical_evidence: ["Setup: pullback short on a confirmed closed candle."], entry_quality: "x", stop_justification: "The stop the production signal uses.", take_profit_feasibility: "TP3 lies beyond the nearest opposing zone.", net_risk_reward: "TP2 pays about 1.78R after estimated fees and slippage.", correlation_exposure: "No same-direction paper positions open in other coins.", rejection_reasons: [] },
  evidence: { status: "Unvalidated", note: "Unvalidated: this variant has no forward paper-trading results.", gate_support: "On past data, skipping this class did not reliably improve results.", historical: { label: "Historical simulation, not forward results", trades: 2719, avg_net_r: -0.223, avg_net_r_ci95: [-0.31, -0.14], win_rate_pct: 33.1, max_drawdown_r: 605, stop_out_pct: 67 } },
};
const plan = {
  symbol: "BTCUSDT", timeframe: "1h", candle_close_time: "2026-11-05T14:00:00Z", ready: true, not_ready_reason: null, setups: [planned], price_decimals: 1,
  gate_definitions: {}, stop_methods: { A_original: "Original production stop", B_atr_2_5: "2.5 ATR" }, label: "Experimental research plan.",
  support: { lower: 77400, upper: 77500, timeframe: "1h" }, resistance: { lower: 82000, upper: 82100, timeframe: "1h" }, ticker: { available: true, last_price: 80050 },
  defaults: { equity: 10000, risk_pct: 0.5, leverage: 5, max_leverage: 20, entry_fee_pct: 0.055, exit_fee_pct: 0.055, entry_slippage_pct: 0.02, stop_slippage_pct: 0.05, maintenance_margin_pct: 0.5, max_open_risk_pct: 2, open_risk_usdt: 0 },
  open_positions: [],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/planning/*/trade-plan*", (r) => r.fulfill({ json: plan }));
});

test("sizes a short at 0.5% by default and recalculates when equity and risk change", async ({ page }) => {
  await page.goto("/setup/BTCUSDT?tf=1h");
  const card = page.getByTestId("trade-plan");
  const pos = page.getByTestId("plan-position");
  await expect(pos.getByText("0.043 BTC")).toBeVisible({ timeout: 30_000 });
  await expect(pos.getByText("$3,440.00")).toBeVisible();
  await expect(card.getByText("Overextended", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("plan-evidence")).toContainText("Unvalidated");
  await expect(page.getByTestId("plan-gate-support")).toContainText("did not reliably improve");
  await card.getByLabel("Equity (USDT)").fill("20000");
  await card.getByLabel("Risk %").fill("1");
  await expect(pos.getByText("0.174 BTC")).toBeVisible();
  await expect(card).not.toContainText(/probability|confidence/i);
});

test("refuses a trade the risk budget can't pay for", async ({ page }) => {
  await page.goto("/setup/BTCUSDT?tf=1h");
  const card = page.getByTestId("trade-plan");
  await expect(page.getByTestId("plan-position").getByText("0.043 BTC")).toBeVisible({ timeout: 30_000 });
  await card.getByLabel("Equity (USDT)").fill("100");
  await expect(page.getByTestId("plan-position").getByText("Refused")).toBeVisible();
  await expect(page.getByTestId("plan-position").getByRole("alert")).toContainText("smallest order");
});

for (const width of [375, 768, 1280]) {
  test(`fits at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/setup/BTCUSDT?tf=1h");
    await expect(page.getByTestId("plan-position").getByText("0.043 BTC")).toBeVisible({ timeout: 30_000 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test("has no accessibility violations in the card", async ({ page }) => {
  test.skip(!existsSync(AXE), "axe-core isn't installed");
  await page.goto("/setup/BTCUSDT?tf=1h");
  await expect(page.getByTestId("plan-position").getByText("0.043 BTC")).toBeVisible({ timeout: 30_000 });
  expect(await axeViolations(page, '[data-testid="trade-plan"]')).toEqual([]);
});

test("the Lab shows the study as unvalidated research", async ({ page }) => {
  await page.goto("/lab");
  const s = page.getByTestId("phase9-study");
  await expect(s).toBeVisible({ timeout: 30_000 });
  await expect(s).toContainText("Unvalidated");
  await expect(s).toContainText("Original production stop");
  await expect(s).toContainText("Retest limit order");
  await expect(s.getByTestId("phase9-retest-note")).toContainText("0.1 ATR past the limit");
  await expect(s).toContainText("3.0 ATR + HTF");
  await expect(s).not.toContainText("C htf");
  if (existsSync(AXE)) expect(await axeViolations(page, '[data-testid="phase9-study"]')).toEqual([]);
});

test("captures the card for review when asked", async ({ page }) => {
  const dir = process.env.E2E_SHOT_DIR;
  test.skip(!dir, "set E2E_SHOT_DIR to save a screenshot");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/setup/BTCUSDT?tf=1h");
  await expect(page.getByTestId("plan-position").getByText("0.043 BTC")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("trade-plan").screenshot({ path: `${dir}/trade-plan.png` });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.getByTestId("trade-plan").screenshot({ path: `${dir}/trade-plan-phone.png` });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.route("**/api/planning/*/trade-plan*", (r) => r.fallback());
  await page.goto("/lab");
  await expect(page.getByTestId("phase9-study")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("phase9-study").screenshot({ path: `${dir}/lab-phase9.png` });
});
