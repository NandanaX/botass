import { describe, expect, it } from "vitest";
import { backendDownComponents, deriveFrontend, formatUptime } from "./system-health";

describe("system health", () => {
  it("never reports services as healthy when the backend cannot be reached", () => {
    const c = backendDownComponents("Cannot reach the backend");
    expect(c.find((x) => x.key === "backend")?.status).toBe("DOWN");
    expect(c.filter((x) => x.key !== "backend").every((x) => x.status === "UNKNOWN")).toBe(true);
    expect(c.some((x) => x.status === "HEALTHY")).toBe(false);
  });

  it("marks the frontend degraded when live updates are off", () => {
    expect(deriveFrontend("open").status).toBe("HEALTHY");
    expect(deriveFrontend("closed").status).toBe("DEGRADED");
    expect(deriveFrontend("connecting").status).toBe("DEGRADED");
  });

  it("formats uptime", () => {
    expect(formatUptime(null)).toBe("—");
    expect(formatUptime(125)).toBe("2m");
    expect(formatUptime(3700)).toBe("1h 1m");
    expect(formatUptime(90_000)).toBe("1d 1h 0m");
  });
});
