import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { existsSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { fmtZoned, zonedDayBoundary } from "@/lib/format";
import { LiveProvider } from "@/lib/live";
import type { NotificationDetail, NotificationItem } from "@/lib/notification-types";
import { CATEGORY_TABS, EMPTY_FILTERS, NotificationDetailView, NotificationFeed, Pagination, toQuery } from "./notification-center";
import { CriticalBanner, NotificationBell, unreadLabel } from "./notifications";
import { paperTradeFocus, signalCandleMarker } from "./price-chart";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/", useSearchParams: () => new URLSearchParams() }));
vi.mock("lightweight-charts", () => ({ LineStyle: {} }));

function item(over: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: 1, event_id: "e", event_type: "SIGNAL_QUALIFIED", category: "signal", severity: "SUCCESS", title: "SHORT LINKUSDT 1h: qualified signal",
    message: "Entry 10", payload: {}, symbol: "LINKUSDT", timeframe: "1h", strategy_id: null, strategy_version: null, signal_id: "s1",
    paper_trade_id: null, incident_id: null, group_key: null, is_group_summary: false, muted: false, delivery_status: "IN_APP",
    occurred_at: "2026-11-04T07:30:00Z", created_at: "2026-11-04T07:30:01Z", read_at: null, links: [], ...over,
  };
}

function detail(over: Partial<NotificationDetail>): NotificationDetail {
  return { ...item(), deliveries: [], members: [], signal_exists: true, ...over };
}

function wrap(ui: React.ReactNode, qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const prefs = { preferences: { timezone: "Asia/Colombo", sound: false }, telegram_configured: true, telegram_enabled: true };

beforeEach(() => {
  push.mockClear();
  vi.spyOn(api, "notificationPrefs").mockResolvedValue(prefs as never);
  vi.spyOn(api, "notificationFacets").mockResolvedValue({ symbols: ["BTCUSDT", "LINKUSDT"], timeframes: ["15m", "1h"] });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("unread badge and popover", () => {
  it("shows the unread count and hides it at zero", async () => {
    const spy = vi.spyOn(api, "notificationUnread").mockResolvedValue({ total: 3, critical: 0 });
    const { unmount } = wrap(<NotificationBell />);
    expect(await screen.findByTestId("unread-badge")).toHaveTextContent("3");
    expect(screen.getByRole("button", { name: "Notifications, 3 unread" })).toBeInTheDocument();
    unmount();
    spy.mockResolvedValue({ total: 0, critical: 0 });
    wrap(<NotificationBell />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("unread-badge")).toBeNull();
    expect(unreadLabel(250)).toBe("99+");
  });

  it("opens the latest notifications, links to View All and navigates to the detail", async () => {
    vi.spyOn(api, "notificationUnread").mockResolvedValue({ total: 1, critical: 0 });
    vi.spyOn(api, "notificationEvents").mockResolvedValue({ total: 1, unread: { total: 1, critical: 0 }, items: [item({ id: 7 })] });
    const read = vi.spyOn(api, "notificationRead").mockResolvedValue({ id: 7, read: true, unread: { total: 0, critical: 0 } });
    wrap(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: /Notifications/ }));
    expect(await screen.findByText("SHORT LINKUSDT 1h: qualified signal")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View All" })).toHaveAttribute("href", "/notifications");
    fireEvent.click(screen.getByText("SHORT LINKUSDT 1h: qualified signal"));
    await waitFor(() => expect(read).toHaveBeenCalledWith(7, true));
    expect(push).toHaveBeenCalledWith("/notifications?id=7");
  });

  it("makes unread critical alerts prominent on every page", async () => {
    vi.spyOn(api, "notificationUnread").mockResolvedValue({ total: 2, critical: 1 });
    wrap(<CriticalBanner />);
    const banner = await screen.findByRole("link", { name: /1 unread critical alert/ });
    expect(banner).toHaveAttribute("href", "/notifications?severity=CRITICAL&read=unread");
  });
});

describe("filtering", () => {
  it("maps tabs and filters onto the API, using the display zone for dates", () => {
    expect(toQuery("risk", EMPTY_FILTERS, "Asia/Colombo", 0)).toMatchObject({ category: "risk", limit: 25, offset: 0 });
    expect(toQuery("unread", EMPTY_FILTERS, "Asia/Colombo", 2)).toMatchObject({ read: "unread", offset: 50 });
    const q = toQuery("all", { ...EMPTY_FILTERS, symbol: "BTCUSDT", strategy: "C", timeframe: "1h", severity: "CRITICAL", from: "2026-11-04", to: "2026-11-04" }, "Asia/Colombo", 0);
    expect(q).toMatchObject({ symbol: "BTCUSDT", strategy: "C", timeframe: "1h", severity: "CRITICAL" });
    expect(q.date_from).toBe("2026-11-03T18:30:00.000Z"); // midnight in Colombo
    expect(q.date_to).toBe("2026-11-04T18:29:59.999Z");
    expect(CATEGORY_TABS.map((t) => t.label)).toEqual(["All", "Unread", "Signal alerts", "Trade results", "Risk warnings", "Market alerts", "System", "Strategy validation"]);
  });

  it("refetches with the chosen filter and offers pagination", async () => {
    const events = vi.spyOn(api, "notificationEvents").mockResolvedValue({ total: 60, unread: { total: 1, critical: 0 }, items: [item()] });
    vi.spyOn(api, "notificationUnread").mockResolvedValue({ total: 1, critical: 0 });
    wrap(<NotificationFeed tab="all" selectedId={null} onSelect={() => {}} />);
    await screen.findByText("SHORT LINKUSDT 1h: qualified signal");
    fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "B" } });
    await waitFor(() => expect(events).toHaveBeenLastCalledWith(expect.objectContaining({ strategy: "B", offset: 0 })));
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    await waitFor(() => expect(events).toHaveBeenLastCalledWith(expect.objectContaining({ strategy: "B", offset: 25 })));
    expect(screen.getByText(/page 2 of 3/)).toBeInTheDocument();
  });

  it("disables pagination at the ends", () => {
    render(<Pagination page={0} total={10} onPage={() => {}} />);
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });
});

describe("detail view", () => {
  it("separates the baseline status from a shadow verdict", () => {
    wrap(<NotificationDetailView tz="Asia/Colombo" onSelect={() => {}} d={detail({
      payload: { direction: "SHORT", baseline_status: "QUALIFIED", entry_reference: 10, stop_loss: 11, take_profit: 8, signal_time: "2026-11-04T07:30:00Z",
        decision: { outcome: "REJECTED", mode: "shadow", enforced: false, label: "Shadow decision: Would reject — signal was not blocked.", reason: "low quality", checks: [] } },
      links: [{ label: "Open signal on chart", href: "/market/LINKUSDT?tf=1h&signal=s1" }],
    })} />);
    expect(screen.getByText("Shadow decision: Would reject — signal was not blocked.")).toBeInTheDocument();
    expect(screen.getByText("QUALIFIED")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open signal on chart" })).toHaveAttribute("href", "/market/LINKUSDT?tf=1h&signal=s1");
    expect(screen.getByText(/04 Nov 2026 13:00 Asia\/Colombo/, { selector: "span" })).toBeInTheDocument();
  });

  it("labels trade results as paper trading and shows every cost", () => {
    wrap(<NotificationDetailView tz="Asia/Colombo" onSelect={() => {}} d={detail({
      event_type: "PAPER_STOP_LOSS_HIT", category: "trade", severity: "WARNING", strategy_id: "B", paper_trade_id: "t1",
      payload: { label: "PAPER TRADING", fill_price: 10, exit_price: 11, quantity: 2, net_pnl: -2.5, net_r: -1.1, entry_fee: 0.1, exit_fee: 0.2, slippage_cost: 0.05,
        spread_cost: 0.01, funding_cost: -0.02, cost_flags: { funding: "exchange_funding_history" }, duration: "2h 0m", portfolio_taken: true, remaining_quantity: 0 },
    })} />);
    expect(screen.getByText(/PAPER TRADING: simulated, not real money/)).toBeInTheDocument();
    expect(screen.getByText("Realized simulated P&L")).toBeInTheDocument();
    expect(screen.getByText("Strategy B")).toBeInTheDocument();
    expect(screen.getByText(/exchange funding history/)).toBeInTheDocument();
    expect(screen.getByText("-1.10R")).toBeInTheDocument();
  });

  it("explains risk events as paper-only and lists affected positions", () => {
    wrap(<NotificationDetailView tz="Asia/Colombo" onSelect={() => {}} d={detail({
      event_type: "RISK_DRAWDOWN_LIMIT", category: "risk", severity: "CRITICAL", strategy_id: "C",
      payload: { circuit_breaker: true, threshold: { value_pct: 10.2, limit_pct: 10 }, affected_positions: [{ symbol: "BTCUSDT", timeframe: "1h", direction: "LONG", risk_amount: 50 }] },
    })} />);
    expect(screen.getByText(/No real trades are affected/)).toBeInTheDocument();
    expect(screen.getByText("limit pct")).toBeInTheDocument();
    expect(screen.getByText(/LONG BTCUSDT 1h/)).toBeInTheDocument();
  });

  it("keeps grouped strategy records separate", () => {
    wrap(<NotificationDetailView tz="Asia/Colombo" onSelect={() => {}} d={detail({
      event_type: "STRATEGY_EVALUATION", category: "trade", is_group_summary: true, message: "Strategy A: Accepted\nStrategy C: Rejected",
      members: [item({ id: 2, strategy_id: "A", title: "A entered" }), item({ id: 3, strategy_id: "C", title: "C rejected" })],
    })} />);
    expect(screen.getByText("Individual records (2)")).toBeInTheDocument();
    expect(screen.getByText("A entered")).toBeInTheDocument();
    expect(screen.getByText("C rejected")).toBeInTheDocument();
  });
});

describe("navigation", () => {
  const app = path.resolve(import.meta.dirname, "../app");
  it("only links to routes that exist", () => {
    for (const route of ["notifications", "validation", "settings", "signals", "market/[symbol]"]) {
      expect(existsSync(path.join(app, route, "page.tsx")), route).toBe(true);
    }
  });

  it("places chart highlights on the candle containing the moment", () => {
    const candle = signalCandleMarker({ candle_open_time: "2026-11-04T07:00:00Z" }, "1h");
    expect(candle.time).toBe(Date.UTC(2026, 10, 4, 7) / 1000);
    const { plan, markers } = paperTradeFocus({ variant_key: "B", direction: "SHORT", fill_price: 10, fill_time: "2026-11-04T07:30:10Z", stop_loss: 11, take_profit: 8,
      exit_price: 11.1, exit_time: "2026-11-04T09:45:00Z" }, "1h");
    expect(markers.map((m) => m.time)).toEqual([Date.UTC(2026, 10, 4, 7) / 1000, Date.UTC(2026, 10, 4, 9) / 1000]);
    expect(markers[0].text).toBe("Paper B entry");
    expect(plan.targets.map((t) => t.label)).toEqual(["Take-profit", "Simulated exit"]);
  });
});

describe("time zones", () => {
  it("defaults to Sri Lanka time and respects another zone", () => {
    expect(fmtZoned("2026-11-04T07:30:00Z")).toBe("04 Nov 2026 13:00 Asia/Colombo");
    expect(fmtZoned("2026-11-04T07:30:00Z", "UTC")).toBe("04 Nov 2026 07:30 UTC");
    expect(fmtZoned(null)).toBe("—");
    expect(zonedDayBoundary("2026-11-04", "UTC")).toBe("2026-11-04T00:00:00.000Z");
  });
});

describe("real-time updates", () => {
  class FakeSocket {
    static instances: FakeSocket[] = [];
    static OPEN = 1;
    readyState = 1;
    onopen?: () => void;
    onmessage?: (e: { data: string }) => void;
    onclose?: () => void;
    onerror?: () => void;
    constructor() {
      FakeSocket.instances.push(this);
    }
    send() {}
    close() {}
    emit(event: string, data: unknown) {
      this.onmessage?.({ data: JSON.stringify({ event, ts: "2026-11-04T08:00:00Z", data }) });
    }
  }
  const hello = (total: number) => ({ connection: {}, tickers: [], scanner: {}, notifications: { total, critical: 0 } });

  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeSocket);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("updates the badge without a refresh, syncs reads and recovers after a reconnect", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const unread = vi.spyOn(api, "notificationUnread").mockResolvedValue({ total: 0, critical: 0 });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60_000 } } });
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    wrap(<LiveProvider><NotificationBell /></LiveProvider>, qc);
    await waitFor(() => expect(unread).toHaveBeenCalled());
    const ws = FakeSocket.instances[0];
    act(() => ws.onopen?.());
    // a new notification appears without refreshing
    act(() => ws.emit("notification", { event: item(), unread: { total: 4, critical: 0 } }));
    expect(await screen.findByTestId("unread-badge")).toHaveTextContent("4");
    // a read made in another tab
    act(() => ws.emit("notification_read", { ids: "all", read: true, unread: { total: 0, critical: 0 } }));
    await waitFor(() => expect(screen.queryByTestId("unread-badge")).toBeNull());
    // the connection drops, then returns: the count and the lists are re-read from the database
    invalidate.mockClear();
    act(() => ws.onclose?.());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    const ws2 = FakeSocket.instances[1];
    expect(ws2).toBeDefined();
    act(() => ws2.emit("hello", hello(2)));
    expect(await screen.findByTestId("unread-badge")).toHaveTextContent("2");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["notif"] });
    vi.useRealTimers();
  });
});
