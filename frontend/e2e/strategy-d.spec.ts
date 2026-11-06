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
});
