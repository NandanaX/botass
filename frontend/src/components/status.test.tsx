import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Ticker } from "@/lib/types";
import { ScoreBar } from "./signal-detail";
import { DataStateBadge, EvaluationBadge, isTickerStale, SignalStatusBadge } from "./status";

const ticker = (overrides: Partial<Ticker> = {}): Ticker => ({
  symbol: "BTCUSDT",
  available: true,
  stale: false,
  updated_at: new Date().toISOString(),
  source: "websocket",
  last_price: 68500,
  ...overrides,
});

describe("data state", () => {
  it("shows LIVE only for fresh data", () => {
    render(<DataStateBadge ticker={ticker()} />);
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("shows STALE when updates stopped", () => {
    const old = new Date(Date.now() - 60_000).toISOString();
    render(<DataStateBadge ticker={ticker({ updated_at: old })} />);
    expect(screen.getByText("STALE")).toBeInTheDocument();
  });

  it("shows NO DATA when the market has never been received", () => {
    render(<DataStateBadge ticker={ticker({ available: false, updated_at: null })} />);
    expect(screen.getByText("NO DATA")).toBeInTheDocument();
  });

  it("isTickerStale respects server flag and age", () => {
    const now = Date.now();
    expect(isTickerStale(ticker(), now)).toBe(false);
    expect(isTickerStale(ticker({ stale: true }), now)).toBe(true);
    expect(isTickerStale(null, now)).toBe(true);
  });
});

describe("badges", () => {
  it("labels signal states", () => {
    render(<SignalStatusBadge status="LONG_SIGNAL" />);
    expect(screen.getByText("LONG SIGNAL")).toBeInTheDocument();
  });

  it("marks evaluated outcomes as hypothetical", () => {
    render(<EvaluationBadge status="TARGET_HIT" />);
    const el = screen.getByText("Target hit*");
    expect(el.getAttribute("title")).toMatch(/Hypothetical/);
  });

  it("score bar explains the score is not a probability", () => {
    const { container } = render(<ScoreBar score={82} />);
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(container.firstElementChild?.getAttribute("title")).toMatch(/Not a probability/);
  });
});
