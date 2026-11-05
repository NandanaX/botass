import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { setTzChoice } from "@/lib/timezone";
import type { StudyResponse, TodayResponse } from "@/lib/research-types";
import study from "./__fixtures__/lab-study.json";
import today from "./__fixtures__/lab-today.json";
import { ForensicPanel, StudySections, TodaySection } from "./lab";

// Real study output and the reconstructed Nov 3 2026 signals (fixtures captured from the API).
vi.mock("./price-chart", () => ({ PriceChart: () => <div data-testid="chart" />, DEFAULT_TOGGLES: {} }));
vi.mock("@/lib/api", () => ({
  api: {
    researchToday: async () => today,
    studyTrades: async () => [],
  },
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("Signal Intelligence Lab", () => {
  it("renders every study section with verdicts and the unvalidated score warning", () => {
    wrap(<StudySections s={study as unknown as StudyResponse} />);
    for (const t of ["C · Historical failure categories", "D · Win rate versus signal score", "E · Market regime performance",
      "F · Strategy comparison", "G · Entry timing analysis", "H · Stop-loss analysis", "I · Portfolio correlation analysis",
      "J · Candidate strategy improvements (experiment framework)"]) {
      expect(screen.getByText(t)).toBeInTheDocument();
    }
    expect(screen.getByText(/flagged as UNVALIDATED/)).toBeInTheDocument();
    expect(screen.getAllByText("VALIDATED").length).toBeGreaterThan(0);
    expect(screen.getByText(/Positive expectancy demonstrated/)).toBeInTheDocument();
  });

  it("shows today's verified results, display-zone times and a forensic report", async () => {
    setTzChoice("Asia/Colombo");
    wrap(<TodaySection day="2026-11-03" setDay={() => {}} onSelect={() => {}} selected={null} />);
    expect(await screen.findByText("Verified net of costs")).toBeInTheDocument();
    expect(screen.getAllByText("08:30")).toHaveLength(2); // DOGE 1h + AVAX 15m, Sri Lanka time
    const t = today as unknown as TodayResponse;
    wrap(<ForensicPanel row={t.signals[0]} />);
    const panel = screen.getByText(/B · Forensics: DOGEUSDT 1h SHORT/).closest("div.rounded-lg") as HTMLElement;
    expect(within(panel).getByText("Rebuilt decision matches stored")).toBeInTheDocument();
    expect(within(panel).getByText("Validated strategy-quality filters")).toBeInTheDocument();
    expect(within(panel).getByTestId("chart")).toBeInTheDocument();
  });
});
