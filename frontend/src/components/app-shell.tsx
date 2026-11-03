"use client";

import {
  Activity,
  BellRing,
  Calculator,
  CandlestickChart,
  Crosshair,
  FlaskConical,
  History,
  LayoutDashboard,
  Radar,
  Settings,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";
import { cn } from "@/lib/utils";
import { useNow } from "./status";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/scanner", label: "Market Scanner", icon: Radar },
  { href: "/market/BTCUSDT", label: "Market Detail", icon: CandlestickChart, match: "/market" },
  { href: "/setup/BTCUSDT", label: "Trade Setup", icon: Crosshair, match: "/setup" },
  { href: "/signals", label: "Trading Signals", icon: BellRing },
  { href: "/history", label: "Signal History", icon: History },
  { href: "/backtesting", label: "Backtesting", icon: FlaskConical },
  { href: "/risk", label: "Risk Calculator", icon: Calculator },
  { href: "/settings", label: "Settings", icon: Settings },
];

function Dot({ ok, warn }: { ok: boolean; warn?: boolean }) {
  return <span className={cn("inline-block h-2 w-2 rounded-full", ok ? "bg-bull" : warn ? "bg-warn" : "bg-bear")} />;
}

function StatusBar() {
  const { socket, exchange, scanner } = useLive();
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
      {scanner?.last_error && <span className="text-bear">Scanner error: {scanner.last_error}</span>}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
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
        <nav className="flex flex-col gap-0.5 px-2">
          {NAV.map((item) => {
            const active = item.match ? pathname.startsWith(item.match) : pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                  active && "bg-accent text-foreground",
                )}
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto p-4 text-[10px] leading-relaxed text-muted-foreground">
          Analysis and research signals only. No orders are placed. Not financial advice.
        </div>
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
          <StatusBar />
        </header>
        <main className="flex-1 px-4 py-5 md:px-6">{children}</main>
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
