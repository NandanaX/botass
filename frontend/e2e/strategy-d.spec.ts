import { expect, test } from "@playwright/test";

test.describe("15m first and Strategy D", () => {
  test("Strategy D page labels itself as research and keeps evidence types apart", async ({ page }) => {
    await page.goto("/strategy-d");
    await expect(page.getByRole("heading", { name: /Strategy D: 15M Multi-Timeframe Confluence/ })).toBeVisible();
    await expect(page.getByTestId("strategy-d-forward")).toContainText("Unvalidated");
    await expect(page.getByTestId("strategy-d-forward")).toContainText("RESEARCH SHADOW");
    await expect(page.getByTestId("c-vs-d")).toContainText("Forward, new data only");
    await expect(page.getByTestId("c-vs-d")).toContainText("Historical replay, hypothetical");
  });

  test("the scanner, history and signals default to 15m and can show everything", async ({ page }) => {
    await page.goto("/scanner");
    await expect(page.getByLabel("Timeframe filter")).toHaveValue("15m");
    await page.getByLabel("Timeframe filter").selectOption("");
    await expect(page.getByLabel("Timeframe filter")).toHaveValue("");
    await page.goto("/history");
    await expect(page.getByLabel("Timeframe", { exact: true })).toHaveValue("15m");
    await page.goto("/signals");
    await expect(page.getByLabel("Timeframe", { exact: true })).toHaveValue("15m");
  });

  test("chart analysis opens on 15m with the higher-timeframe summary", async ({ page }) => {
    await page.goto("/analysis/BTCUSDT");
    await expect(page.getByTestId("htf-summary")).toContainText("Higher-timeframe context", { timeout: 30_000 });
  });

  test("the overview shows 15m decisions labelled as research", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("strategy-d-latest")).toContainText("Research shadow");
  });

  test("the reset form only appears when the account is halted and asks for confirmation", async ({ page }) => {
    // Every response here is mocked, and a real reset request can never leave the browser.
    const resets: string[] = [];
    await page.route("**/api/strategy-d/reset-drawdown", async (route) => {
      resets.push(route.request().postData() ?? "");
      await route.fulfill({ json: { reset: true, segment: 2, evaluation_version: "D-1/segment-2", reset_at: "2026-11-06T00:00:00+00:00", start_equity: 8900 } });
    });
    await page.route("**/api/strategy-d/overview", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      const tripped = resets.length === 0;
      await route.fulfill({ response: res, json: { ...body, breakers: { ...body.breakers, max_drawdown: { tripped, value_pct: tripped ? 11 : 0, limit_pct: 10, reset_required: tripped } } } });
    });
    await page.goto("/strategy-d");
    const halted = page.getByTestId("strategy-d-halted");
    await expect(halted).toContainText("Paper account halted");
    const button = halted.getByRole("button", { name: /Reset and start segment/ });
    await expect(button).toBeDisabled();
    await halted.getByLabel(/Reason/).fill("reviewed the losses");
    await halted.getByLabel(/Reviewer/).fill("tester");
    await expect(button).toBeDisabled();
    const phrase = (await page.getByText(/^Type ".*" to confirm$/).innerText()).match(/"(.*)"/)?.[1] ?? "";
    await halted.getByLabel(/to confirm/).fill(phrase);
    await button.click();
    await expect(halted).toBeHidden();
    expect(resets).toHaveLength(1);
    expect(JSON.parse(resets[0])).toEqual({ reason: "reviewed the losses", reviewer: "tester", confirm: phrase });
  });

  test("no reset form while the account isn't halted, and segments are listed", async ({ page }) => {
    await page.route("**/api/strategy-d/overview", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      await route.fulfill({ response: res, json: { ...body, breakers: { ...body.breakers, max_drawdown: { tripped: false, value_pct: 1, limit_pct: 10, reset_required: false } } } });
    });
    await page.goto("/strategy-d");
    await expect(page.getByTestId("strategy-d-segments")).toContainText("(current)");
    await expect(page.getByTestId("strategy-d-halted")).toHaveCount(0);
  });
});
