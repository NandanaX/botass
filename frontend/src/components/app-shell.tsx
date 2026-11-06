"use client";

import {
  Activity,
  Bell,
  BellRing,
  Calculator,
  CandlestickChart,
  Crosshair,
  FlaskConical,
  Globe,
  HeartPulse,
  History,
  LayoutDashboard,
  LineChart,
  Microscope,
  Radar,
  Settings,
  ShieldCheck,
  Layers,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";
import { tzLabel, useDisplayTz, useTzVersion } from "@/lib/timezone";
import { cn } from "@/lib/utils";
import { CriticalBanner, NotificationBell } from "./notifications";
import { useNow } from "./status";

const NAV_GROUPS = [
  {
    label: "Markets",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/scanner", label: "Market Scanner", icon: Radar },
      { href: "/market/BTCUSDT", label: "Market Detail", icon: CandlestickChart, match: "/market" },
    ],
  },
  {
    label: "Signals",
    items: [
      { href: "/signals", label: "Trading Signals", icon: BellRing },
      { href: "/history", label: "Signal History", icon: History },
    ],
  },
  {
    label: "Analysis",
    items: [
      { href: "/setup/BTCUSDT", label: "Trade Setup", icon: Crosshair, match: "/setup" },
      { href: "/analysis/BTCUSDT", label: "Chart Analysis", icon: LineChart, match: "/analysis" },
      { href: "/risk", label: "Risk Calculator", icon: Calculator },
    ],
  },
  {
    label: "Research",
    items: [
      { href: "/backtesting", label: "Backtesting", icon: FlaskConical },
      { href: "/lab", label: "Intelligence Lab", icon: Microscope },
      { href: "/validation", label: "Validation Monitor", icon: ShieldCheck },
      { href: "/strategy-d", label: "Strategy D (15m)", icon: Layers },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/notifications", label: "Notifications", icon: Bell },
      { href: "/system-health", label: "System Health", icon: HeartPulse },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

const NAV = NAV_GROUPS.flatMap((g) => g.items);

function Dot({ ok, warn }: { ok: boolean; warn?: boolean }) {
  return <span className={cn("inline-block h-2 w-2 rounded-full", ok ? "bg-bull" : warn ? "bg-warn" : "bg-bear")} />;
}

function StatusBar() {
  const { socket, exchange, scanner } = useLive();
  const tz = useDisplayTz();
  useNow(5000);
  const backendOk = socket === "open";
  const exchangeOk = !!exchange?.ws_connected;
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-muted-foreground">
      <span className="flex items-center gap-1.5" title="Dashboard ↔ backend WebSocket">
        <Dot ok={backendOk} warn={socket === "connecting"} /> Backend {socket}
      </span>
      <span className="flex items-center gap-1.5" title="Backend ↔ Bybit stream">
        <Dot ok={exchangeOk} warn={!!exchange?.rest_fallback_active} />
        Bybit {exchangeOk ? "stream" : exchange?.rest_fallback_active ? "REST fallback" : "disconnected"}
      </span>
      <span className="flex items-center gap-1.5">
        <Activity className="h-3 w-3" />
        Scanner {scanner?.running ? (scanner.is_leader ? "running" : "standby") : "stopped"}
        {scanner?.last_cycle_at && <> · last cycle {timeAgo(scanner.last_cycle_at)}</>}
      </span>
      <Link href="/settings#display-time" className="flex items-center gap-1.5 hover:text-foreground" title="All times are shown in this zone. Hover a time for the other zone; change it in Settings." data-testid="active-timezone">
        <Globe className="h-3 w-3" /> Times: {tzLabel(tz)}
      </Link>
      {scanner?.last_error && <span className="text-bear">Scanner error: {scanner.last_error}</span>}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const tzVersion = useTzVersion();
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r bg-card/50 md:flex">
        <div className="flex items-center gap-2 px-4 py-4">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/15 text-primary">
            <CandlestickChart className="h-4 w-4" />
          </div>
          <div>
            <div className="text-sm font-semibold leading-tight">CryptoSignal AI</div>
            <div className="text-[10px] text-muted-foreground">Bybit USDT perpetuals</div>
          </div>
        </div>
        {/* Extra bottom padding so the last item can scroll clear of anything floating over the corner. */}
        <nav aria-label="Main" className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-2 pb-14">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} role="group" aria-labelledby={`nav-${group.label}`} className="flex flex-col gap-0.5">
              <div id={`nav-${group.label}`} className="px-2.5 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{group.label}</div>
              {group.items.map((item) => {
                const active = "match" in item && item.match ? pathname.startsWith(item.match) : pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                      active && "bg-accent text-foreground",
                    )}
                  >
                    <item.icon className="h-4 w-4" aria-hidden />
                    {item.label}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 border-b bg-background/90 px-4 py-2.5 backdrop-blur md:px-6">
          <nav className="mb-2 flex gap-1 overflow-x-auto md:hidden">
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} className="rounded px-2 py-1 text-xs text-muted-foreground hover:bg-accent">
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center justify-between gap-3">
            <StatusBar />
            <NotificationBell />
          </div>
          <CriticalBanner />
        </header>
        <main key={pathname === "/settings" ? "settings" : tzVersion} className="flex-1 px-4 py-5 md:px-6">{children}</main>
        {/* In the page flow with room below, so a floating badge or the settings save bar can't sit on it. */}
        <footer className="border-t px-4 pb-16 pt-3 text-[11px] leading-relaxed text-muted-foreground md:px-6" data-testid="disclaimer">
          Analysis and research signals only. No orders are placed. Not financial advice.
        </footer>
      </div>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
