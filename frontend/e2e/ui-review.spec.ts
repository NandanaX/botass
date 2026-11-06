import { expect, test, type Page } from "@playwright/test";

// Browser checks for the UI review. Everything that would change data is intercepted before it leaves the
// browser, so these are safe against the live app.

const decided = (page: Page) => page.getByTestId("decision-block");

/** Serve the real setup response with the decision replaced, to see each state without waiting for the market. */
async function forceDecision(page: Page, decision: string, direction: string | null) {
  await page.route("**/api/market/BTCUSDT/setup*", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.decision = decision;
    body.primary_setup = direction ? { direction } : null;
    body.setups = [];
    await route.fulfill({ response: res, json: body });
  });
}

test.describe("trade setup explains the decision", () => {
  for (const [decision, direction, title] of [
    ["NO_SETUP", null, "No setup"],
    ["WATCHLIST", "LONG", "Watchlist long"],
    ["WATCHLIST", "SHORT", "Watchlist short"],
    ["QUALIFIED", "LONG", "Long"],
    ["QUALIFIED", "SHORT", "Short"],
    ["REJECTED", "LONG", "Rejected long"],
  ] as const) {
    test(`${decision} ${direction ?? ""} shows "${title}"`, async ({ page }) => {
      await forceDecision(page, decision, direction);
      await page.goto("/setup/BTCUSDT?tf=15m");
      await expect(decided(page).locator(".text-2xl")).toHaveText(title, { timeout: 30_000 });
    });
  }

  test("both strategies list their rules with value and requirement, for either direction", async ({ page }) => {
    await page.goto("/setup/BTCUSDT?tf=15m");
    const list = page.getByTestId("rule-checklist");
    await expect(list.locator("[data-strategy=breakout]")).toBeVisible({ timeout: 30_000 });
    await expect(list.locator("[data-strategy=pullback]")).toBeVisible();
    for (const key of ["stop_distance", "room", "net_rr"]) await expect(list.locator(`[data-strategy=pullback] [data-rule=${key}]`)).toBeVisible();
    await expect(list.locator("[data-rule=rvol]")).toContainText(/min [\d.]+x/);
    await list.getByRole("tab", { name: "Short" }).click();
    await expect(list.locator("[data-rule=ema_fast_mid]").first()).toContainText("<");
    await list.getByRole("tab", { name: "Long" }).click();
    await expect(list.locator("[data-rule=ema_fast_mid]").first()).toContainText(">");
  });

  test("names what would trigger a signal, and counts down to the candle close", async ({ page }) => {
    await page.goto("/setup/BTCUSDT?tf=15m");
    await expect(page.getByTestId("trigger-lines")).toContainText(/breakout|pullback|mixed/i, { timeout: 30_000 });
    const clock = decided(page).locator("span.font-semibold");
    const first = await clock.textContent();
    await expect(clock).toHaveText(/^\d\d:\d\d$/);
    await expect.poll(() => clock.textContent(), { timeout: 5000 }).not.toBe(first);
  });

  test("uses the live price and active zones only", async ({ page }) => {
    await page.goto("/setup/BTCUSDT?tf=15m");
    await expect(page.getByTestId("zone-list-price")).toBeVisible({ timeout: 30_000 });
    const header = (await page.locator("main .text-3xl").first().textContent())!;
    const number = (s: string) => Number(s.replace(/[^\d.]/g, ""));
    const listed = number((await page.getByTestId("zone-list-price").textContent())!);
    expect(Math.abs(listed - number(header)) / number(header)).toBeLessThan(0.002);
    await expect(page.locator("details [data-zone-status=ACTIVE]")).toHaveCount(0);
    await expect(page.getByTestId("zone-list").locator("> [data-zone-status=BROKEN], > [data-zone-status=INVALIDATED]")).toHaveCount(0);
    await expect(page.getByText(/tests · broken/)).toHaveCount(0);
  });

  test("sizing recalculates after typing stops, without a button", async ({ page }) => {
    await page.goto("/setup/BTCUSDT?tf=15m");
    await expect(page.getByRole("button", { name: "Recalculate" })).toHaveCount(0);
    const risk = page.getByTestId("account-risk").getByLabel("Risk %");
    await expect(page.getByText(/^Using \$/)).toBeVisible({ timeout: 30_000 });
    const sent = page.waitForRequest((r) => r.url().includes("/setup?") && r.url().includes("risk_pct=2.5"));
    await risk.fill("2.5");
    await sent;
    await expect(page.getByText(/2\.5% risk/)).toBeVisible();
  });
});

test.describe("chart", () => {
  test("the wheel scrolls the page unless the user asks to zoom", async ({ page }) => {
    await page.goto("/setup/BTCUSDT?tf=15m");
    await expect(page.getByTestId("zoom-hint")).toHaveText(/\+ scroll to zoom/, { timeout: 30_000 });
    const canvas = page.locator("canvas").first();
    await canvas.scrollIntoViewIfNeeded();
    const box = (await canvas.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    const y = () => page.evaluate(() => window.scrollY);
    const start = await y();
    await page.mouse.wheel(0, 120);
    await expect.poll(y).toBeGreaterThan(start);

    const box2 = (await canvas.boundingBox())!;
    await page.mouse.move(box2.x + box2.width / 2, box2.y + box2.height / 2);
    const before = await y();
    await page.keyboard.down("Control");
    await page.mouse.wheel(0, -120);
    await page.keyboard.up("Control");
    await page.waitForTimeout(300);
    expect(await y()).toBe(before);
  });
});

test.describe("time zone", () => {
  test("one setting drives every time, with the other time on hover", async ({ page }) => {
    await page.goto("/settings");
    const pick = page.locator("#display-time select");
    await pick.selectOption("Asia/Tokyo", { timeout: 30_000 });
    await expect(page.getByTestId("active-timezone")).toContainText("Asia/Tokyo (UTC+09:00)");
    await page.goto("/setup/BTCUSDT?tf=15m");
    const time = page.locator("main time").first();
    await expect(time).toBeVisible({ timeout: 30_000 });
    // Read together: the first timestamp on the page can change as data arrives.
    const shown = await page.locator("main time").evaluateAll((els) => els.map((e) => [e.getAttribute("datetime")!, e.textContent!, e.getAttribute("title")!]));
    expect(shown.length).toBeGreaterThan(0);
    for (const [iso, text, title] of shown) {
      const expected = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(iso));
      expect(text).toContain(expected);
      expect(title).toMatch(/UTC$/);
    }
    await page.evaluate(() => localStorage.removeItem("display-timezone"));
  });
});

test.describe("trading signals", () => {
  test("with nothing active, shows what is closest, the last signals and the next closes", async ({ page }) => {
    await page.route("**/api/signals?*status=active*", (route) => route.fulfill({ json: { total: 0, items: [] } }));
    await page.goto("/signals");
    await expect(page.getByTestId("signals-empty")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("closest")).toBeVisible();
    await expect(page.getByTestId("recent").locator("a").nth(0)).toBeVisible();
    expect(await page.getByTestId("recent").locator("a").count()).toBeLessThanOrEqual(5);
    await expect(page.getByTestId("next-closes")).toContainText(/\d\d:\d\d/);
    await page.getByRole("link", { name: /Signal History/ }).last().click();
    await expect(page).toHaveURL(/\/history/);
  });
});

test.describe("navigation", () => {
  test("groups the sidebar and keeps the disclaimer in the page", async ({ page }) => {
    await page.goto("/");
    const groups = await page.locator("aside nav [role=group]").evaluateAll((els) => els.map((g) => g.firstElementChild!.textContent));
    expect(groups).toEqual(["Markets", "Signals", "Analysis", "Research", "System"]);
    await expect(page.locator("aside nav a")).toHaveCount(15);
    await expect(page.getByTestId("disclaimer")).toContainText("Not financial advice");
    await expect(page.locator("aside").getByText("Not financial advice")).toHaveCount(0);
  });
});

test.describe("settings", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/settings");
    await expect(page.locator("[data-field]").first()).toBeVisible({ timeout: 30_000 });
  });

  test("has the agreed tabs and a working search", async ({ page }) => {
    expect(await page.getByRole("tab").allTextContents()).toEqual(["Markets & alerts", "Strategy", "Stops & risk", "Indicators", "Decision engine", "Validation", "Technical analysis"]);
    const search = page.getByLabel("Search settings by label");
    await search.fill("macd");
    await expect(page.locator("[data-field]")).toHaveCount(4); // MACD fast, slow, signal and the pullback's MACD turn
    await search.fill("no such label");
    await expect(page.getByText(/No setting has/)).toBeVisible();
  });

  test("the save bar counts changes and discards them, and warnings appear inline", async ({ page }) => {
    await expect(page.getByTestId("save-bar")).toHaveCount(0);
    const score = page.getByLabel("Minimum signal score");
    const start = await score.inputValue();
    await score.fill(start === "33" ? "34" : "33");
    await expect(page.getByTestId("save-bar")).toContainText("1 unsaved change");
    await page.getByTestId("save-bar").getByRole("button", { name: "Discard" }).click();
    await expect(page.getByTestId("save-bar")).toHaveCount(0);
    await expect(score).toHaveValue(start);
    await page.getByRole("tab", { name: "Decision engine" }).click();
    await expect(page.locator("[data-warning='decision.mode']")).toBeVisible();
  });

  test("a change that restarts the forward test asks first, and the confirmation is sent", async ({ page }) => {
    const calls: string[] = [];
    await page.route("**/api/settings*", async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      calls.push(route.request().url());
      if (!route.request().url().includes("confirm_new_forward_period=true")) {
        return route.fulfill({ status: 409, json: { detail: "This change ends the current forward test." } });
      }
      const current = await (await page.request.get(route.request().url().split("?")[0])).json();
      return route.fulfill({ json: { ...current, settings: route.request().postDataJSON() } });
    });
    const score = page.getByLabel("Minimum signal score");
    await score.fill((await score.inputValue()) === "33" ? "34" : "33");
    await page.getByTestId("save-bar").getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Saving would restart the forward test")).toBeVisible();
    await page.getByRole("button", { name: "Save and restart the forward test" }).click();
    await expect(page.getByText("Saved", { exact: true })).toBeVisible();
    expect(calls).toHaveLength(2);
    await expect(page.getByTestId("save-bar")).toHaveCount(0);
  });

  test("every switch has a name and works from the keyboard and its label", async ({ page }) => {
    await page.getByRole("tab", { name: "Strategy", exact: true }).click();
    const named = page.getByRole("switch", { name: "MACD turn" });
    const state = await named.getAttribute("data-state");
    await named.focus();
    await page.keyboard.press("Space");
    await expect(named).not.toHaveAttribute("data-state", state!);
    await page.getByText("MACD turn", { exact: true }).click();
    await expect(named).toHaveAttribute("data-state", state!);
    await expect(page.getByTestId("save-bar")).toHaveCount(0);
    const unnamed = await page.getByRole("switch").evaluateAll((els) => els.filter((e) => !e.getAttribute("aria-label") && !document.getElementById(e.getAttribute("aria-labelledby") ?? "")?.textContent).length);
    expect(unnamed).toBe(0);
  });
});

test.describe("polish", () => {
  test("price alert fields aren't truncated and swing dates appear once", async ({ page }) => {
    await page.goto("/setup/BTCUSDT?tf=15m");
    await expect(page.getByLabel("Alert price")).toHaveAttribute("placeholder", "Price", { timeout: 30_000 });
    await expect(page.getByTestId("alert-price-now")).toContainText("Current price");
    const swings = page.getByTestId("swings");
    await expect(swings.locator("time").first()).toHaveText(/^\d\d:\d\d$/);
  });

  for (const width of [1024, 1280, 1440, 1920]) {
    test(`no sideways overflow at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      for (const route of ["/setup/BTCUSDT", "/signals", "/settings", "/history"]) {
        await page.goto(route);
        await page.waitForTimeout(1500);
        expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), route).toBeLessThanOrEqual(0);
      }
    });
  }
});
