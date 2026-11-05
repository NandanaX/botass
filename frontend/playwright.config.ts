import { defineConfig } from "@playwright/test";

// Real-browser tests. Point E2E_BASE_URL at a running dashboard. Tests that change data run only when
// E2E_MUTATE=1, which must never be set against the live app: use an isolated backend and database.
export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  fullyParallel: false,
  workers: 2,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: process.env.E2E_JSON ?? "e2e-results.json" }]],
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000", channel: "chrome", trace: "off", screenshot: "off", video: "off" },
});
