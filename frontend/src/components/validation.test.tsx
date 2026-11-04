import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { HistoricalRobustness, ValidationOverview } from "@/lib/validation-types";
import hist from "./__fixtures__/validation-historical.json";
import overview from "./__fixtures__/validation-overview.json";
import { ForwardSection, HistoricalSection, PromotionSection } from "./validation";

// Captured from the API: a forward period that has just started, and the historical study.
vi.mock("./price-chart", () => ({ EquityChart: () => <div data-testid="equity" /> }));

function wrap(ui: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
}

describe("Strategy Validation Monitor", () => {
  it("never presents an incomplete forward test as validated", () => {
    const o = overview as unknown as ValidationOverview;
    wrap(<><ForwardSection o={o} /><PromotionSection o={o} /></>);
    expect(screen.getByText(/FORWARD · live paper/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing here is a validation yet/)).toBeInTheDocument();
    expect(screen.getAllByText("INSUFFICIENT FORWARD DATA")).toHaveLength(3);
    for (const opt of screen.getAllByRole("option", { name: /Approve for production review/ })) expect(opt).toBeDisabled();
  });

  it("labels historical results separately and shows stability and stress", () => {
    wrap(<HistoricalSection h={hist as unknown as HistoricalRobustness} />);
    expect(screen.getByText(/HISTORICAL · re-simulation/)).toBeInTheDocument();
    expect(screen.getByText(/none is an untouched test/)).toBeInTheDocument();
    expect(screen.getByText("combined adverse")).toBeInTheDocument();
    expect(screen.getByText(/Break-even cost multiplier/)).toBeInTheDocument();
  });
});
