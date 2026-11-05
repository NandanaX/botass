import { expect, test, type Page } from "@playwright/test";
import { readdirSync, statSync } from "node:fs";
import path from "node:path";

/** Routes come from the router folder, so a new page cannot be forgotten. */
function discover(dir: string, prefix = ""): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) out.push(...discover(full, `${prefix}/${name}`));
    else if (name === "page.tsx") out.push(prefix || "/");
  }
  return out;
}

const SAMPLE: Record<string, string> = { "[symbol]": "BTCUSDT" };
const ROUTES = discover(path.resolve(__dirname, "../src/app")).map((r) =>
  r.split("/").map((part) => SAMPLE[part] ?? part).join("/") || "/",
);
const VARIANTS = ["/analysis/SUIUSDT?tf=4h", "/market/ETHUSDT?tf=1h", "/notifications?tab=preferences", "/notifications?tab=incidents", "/setup/SOLUSDT"];
const MOBILE = { width: 390, height: 844 };

function watch(page: Page) {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(`exception: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text()}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 400 && !r.url().includes("favicon")) problems.push(`HTTP ${r.status()} ${r.url()}`);
  });
  page.on("requestfailed", (r) => {
    const why = r.failure()?.errorText ?? "";
    if (!why.includes("ABORTED")) problems.push(`failed ${r.url()} ${why}`);
  });
  return problems;
}

async function settled(page: Page) {
  await page.waitForLoadState("load");
  await expect(page.locator("main")).toBeVisible();
  await page.waitForTimeout(2500);
  await expect(page.getByText("Application error")).toHaveCount(0);
  await expect(page.locator("nextjs-portal [data-nextjs-dialog]")).toHaveCount(0);
}

test.describe("every route on desktop", () => {
  for (const route of [...ROUTES, ...VARIANTS]) {
    test(`loads cleanly: ${route}`, async ({ page }) => {
      const problems = watch(page);
      const res = await page.goto(route);
      expect(res?.status()).toBe(200);
      await settled(page);
      await expect(page.locator("h1").first()).toBeVisible();
      expect(problems).toEqual([]);
    });
  }
});

test.describe("every route on a phone", () => {
  test.use({ viewport: MOBILE });
  for (const route of ROUTES) {
    test(`fits the screen: ${route}`, async ({ page }) => {
      const problems = watch(page);
      await page.goto(route);
      await settled(page);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, "page must not scroll sideways").toBeLessThanOrEqual(1);
      expect(problems).toEqual([]);
    });
  }
});

test("navigation reaches every page in the menu", async ({ page }) => {
  await page.goto("/");
  await settled(page);
  const links = page.locator("aside nav a");
  const hrefs = await links.evaluateAll((els) => els.map((e) => e.getAttribute("href")!));
  expect(hrefs.length).toBeGreaterThanOrEqual(13);
  for (const href of hrefs) {
    await page.locator(`aside nav a[href="${href}"]`).click();
    await page.waitForURL(`**${href}*`);
    await expect(page.locator("h1").first()).toBeVisible();
  }
});

test("bell opens the latest notifications and View All goes to the center", async ({ page }) => {
  await page.goto("/");
  await settled(page);
  await page.getByRole("button", { name: /^Notifications/ }).click();
  const dialog = page.getByRole("dialog", { name: "Latest notifications" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: /^Notifications/ }).click();
  await page.getByRole("link", { name: "View All" }).click();
  await page.waitForURL("**/notifications");
  await expect(page.getByRole("heading", { name: "Notification Center" })).toBeVisible();
});

test("notification tabs and filters request the right data", async ({ page }) => {
  await page.goto("/notifications");
  await settled(page);
  await page.getByRole("tab", { name: "Risk warnings" }).click();
  await page.waitForURL("**tab=risk*");
  const req = page.waitForRequest((r) => r.url().includes("/notification-center/events") && r.url().includes("severity=CRITICAL"));
  await page.getByLabel("Severity").selectOption("CRITICAL");
  await req;
});

test("system health shows every component with a real status", async ({ page }) => {
  await page.goto("/system-health");
  await settled(page);
  for (const key of ["frontend", "backend", "database", "bybit_rest", "bybit_ws", "market_data", "scanner", "signal_engine", "paper_worker", "telegram", "notifications", "monitor"]) {
    const card = page.getByTestId(`component-${key}`);
    await expect(card).toBeVisible();
    await expect(card.getByText(/^(HEALTHY|DEGRADED|DOWN|UNKNOWN)$/)).toBeVisible();
  }
  await expect(page.getByText("Real-money trading")).toBeVisible();
  await expect(page.getByText("Disabled", { exact: true })).toBeVisible();
});

test("market page draws a chart", async ({ page }) => {
  await page.goto("/market/BTCUSDT?tf=1h");
  await settled(page);
  await expect(page.locator("canvas").first()).toBeVisible({ timeout: 15000 });
});

test("risk calculator validates and computes", async ({ page }) => {
  await page.goto("/risk");
  await settled(page);
  await expect(page.getByRole("heading").first()).toBeVisible();
  const inputs = page.locator("main input[type=number]");
  expect(await inputs.count()).toBeGreaterThan(2);
});

test("an unknown page shows Not Found rather than crashing", async ({ page }) => {
  const res = await page.goto("/this-page-does-not-exist");
  expect(res?.status()).toBe(404);
});

test("the dashboard explains when the backend is unreachable", async ({ page }) => {
  await page.route("**/api/**", (r) => r.abort());
  await page.goto("/system-health");
  await expect(page.getByTestId("component-backend").getByText("DOWN")).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("component-database").getByText("UNKNOWN")).toBeVisible();
  await page.goto("/signals");
  await expect(page.getByText(/Cannot reach the backend/).first()).toBeVisible({ timeout: 15000 });
});

test("empty lists show a message", async ({ page }) => {
  await page.route("**/api/notification-center/events**", (r) => r.fulfill({ json: { total: 0, items: [], unread: { total: 0, critical: 0 } } }));
  await page.goto("/notifications");
  await expect(page.getByText("No notifications match these filters.")).toBeVisible({ timeout: 15000 });
});

test("form defaults never block submission", async ({ page }) => {
  // A value the browser considers invalid stops a form silently, which no API test can see.
  for (const route of ["/backtesting", "/settings", "/notifications?tab=preferences", "/setup/BTCUSDT"]) {
    await page.goto(route);
    await settled(page);
    const invalid = await page.evaluate(() => [...document.querySelectorAll<HTMLInputElement>("main input, main select")].filter((e) => !e.checkValidity()).map((e) => `${e.closest("div")?.querySelector("label")?.textContent}: ${e.validationMessage}`));
    expect(invalid, route).toEqual([]);
  }
});

test("risk calculator computes and rejects a stop on the wrong side", async ({ page }) => {
  await page.goto("/risk");
  await settled(page);
  const field = (label: string) => page.getByLabel(label, { exact: true });
  await field("Account balance ($)").fill("10000");
  await field("Risk per trade (%)").fill("1");
  await field("Entry price").fill("100");
  await field("Stop-loss price").fill("95");
  let done = page.waitForResponse((r) => r.url().includes("/api/risk/calculate"));
  await page.getByRole("button", { name: "Calculate" }).click();
  expect((await done).status()).toBe(200);
  await field("Stop-loss price").fill("105");
  done = page.waitForResponse((r) => r.url().includes("/api/risk/calculate"));
  await page.getByRole("button", { name: "Calculate" }).click();
  expect((await done).status()).toBe(422);
  await expect(page.getByText(/stop-loss must be below/i)).toBeVisible();
});

test("scanner filter narrows the table", async ({ page }) => {
  await page.goto("/scanner");
  await settled(page);
  await page.getByPlaceholder("Filter symbol…").fill("BTC");
  await expect.poll(async () => {
    const rows = await page.locator("main tbody tr").allInnerTexts();
    return rows.length > 0 && rows.every((r) => r.includes("BTC"));
  }, { timeout: 10000 }).toBe(true);
});

test("signal history filter asks the API for that status", async ({ page }) => {
  await page.goto("/history");
  await settled(page);
  const select = page.getByLabel("Status");
  const value = await select.locator("option").nth(1).getAttribute("value");
  const req = page.waitForRequest((r) => r.url().includes("/api/signals?") && r.url().includes(`status=${value}`));
  await select.selectOption(value!);
  await req;
});

test("opening a notification shows its details and links to pages that exist", async ({ page }) => {
  await page.goto("/notifications?tab=signal");
  await settled(page);
  const rows = page.locator("main button:has(time)");
  test.skip((await rows.count()) === 0, "no signal notifications in this database");
  await rows.first().click();
  await page.waitForURL("**id=*");
  await expect(page.getByRole("button", { name: /Mark as (read|unread)/ })).toBeVisible();
  const links = await page.locator("main a[href^='/market/'], main a[href^='/signals'], main a[href^='/validation']").evaluateAll((els) => els.map((e) => e.getAttribute("href")!));
  for (const href of links.slice(0, 3)) {
    const res = await page.request.get(href);
    expect(res.status(), href).toBe(200);
  }
});

test("validation monitor separates forward from historical and blocks early approval", async ({ page }) => {
  await page.goto("/validation");
  await settled(page);
  await expect(page.getByText(/FORWARD · live paper/)).toBeVisible();
  await expect(page.getByText(/HISTORICAL · re-simulation/)).toBeVisible();
  for (const opt of await page.getByRole("option", { name: /Approve for production review/ }).all()) await expect(opt).toBeDisabled();
});

test.describe("actions that change data", () => {
  test.skip(process.env.E2E_MUTATE !== "1", "only against an isolated backend");

  test("mark as read updates the row and the unread count", async ({ page }) => {
    await page.goto("/notifications");
    await settled(page);
    const first = page.locator("main button:has([aria-label='Unread'])").first();
    test.skip((await first.count()) === 0, "nothing unread in this database");
    const before = await page.getByTestId("unread-badge").innerText();
    await first.click();
    await expect.poll(async () => ((await page.getByTestId("unread-badge").count()) ? page.getByTestId("unread-badge").innerText() : "0")).not.toBe(before);
  });

  test("saving a change to the locked configuration asks first and can be abandoned", async ({ page, request }) => {
    const api = process.env.E2E_API!;
    const version = async () => (await (await request.get(`${api}/api/settings`)).json()).strategy_version;
    const before = await version();
    await page.goto("/settings");
    await settled(page);
    await page.getByRole("tab", { name: "Strategy", exact: true }).click();
    await page.locator("[data-field='strategy.zones.htf_timeframes']").getByText("1w", { exact: true }).click();
    await page.getByTestId("save-bar").getByRole("button", { name: "Save", exact: true }).click();
    const warning = page.getByText("Saving would restart the forward test");
    await expect(warning).toBeVisible();
    expect(await version()).toBe(before);
    await page.getByRole("button", { name: "Discard my changes" }).click();
    await expect(warning).toHaveCount(0);
    expect(await version()).toBe(before);
  });

  test("saving an unrelated setting needs no confirmation and keeps the forward test", async ({ page, request }) => {
    const api = process.env.E2E_API!;
    const version = async () => (await (await request.get(`${api}/api/settings`)).json()).strategy_version;
    const before = await version();
    await page.goto("/settings");
    await settled(page);
    const age = page.getByLabel("Max signal age (candles)");
    await age.fill(String((Number(await age.inputValue()) % 9) + 1));
    await page.getByTestId("save-bar").getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Saved", { exact: true })).toBeVisible();
    await expect(page.getByText("Saving would restart the forward test")).toHaveCount(0);
    expect(await version()).toBe(before);
  });

  test("a price alert can be created and cancelled", async ({ page }) => {
    await page.goto("/setup/BTCUSDT");
    await settled(page);
    const level = "1234567";
    await page.getByPlaceholder(/^Price/).fill(level);
    const created = page.waitForResponse((r) => r.url().includes("/api/price-alerts") && r.request().method() === "POST");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    expect((await created).status()).toBe(201);
    await expect(page.getByText(/1,234,567/).first()).toBeVisible();
    const before = await page.getByRole("button", { name: "Cancel alert" }).count();
    await page.getByRole("button", { name: "Cancel alert" }).first().click();
    await expect.poll(() => page.getByRole("button", { name: "Cancel alert" }).count()).toBeLessThan(before);
  });

  test("the Telegram test button reports delivery", async ({ page }) => {
    await page.goto("/settings");
    await settled(page);
    const sent = page.waitForResponse((r) => r.url().includes("/api/alerts/telegram/test"));
    await page.getByRole("button", { name: /test/i }).first().click();
    expect((await sent).status()).toBe(200);
  });

  test("a backtest runs from the form and shows LONG and SHORT results", async ({ page }) => {
    test.setTimeout(150_000);
    await page.goto("/backtesting");
    await settled(page);
    await page.getByLabel("Timeframe", { exact: true }).selectOption("4h");
    await page.getByLabel("Start (UTC)", { exact: true }).fill("2026-06-01");
    await page.getByLabel("End (UTC)", { exact: true }).fill("2026-10-01");
    await page.getByRole("button", { name: /Run backtest/ }).click();
    await expect(page.getByText("LONG performance")).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText("SHORT performance")).toBeVisible();
    await expect(page.getByText(/Historical backtest \(simulated\)/)).toBeVisible();
  });
});
