"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { DirectionBadge, Empty, ErrorBox } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, NativeSelect, Switch } from "@/components/ui/form";
import { api } from "@/lib/api";
import { fmtNum, fmtPrice, fmtUsd, fmtZoned, zonedDayBoundary } from "@/lib/format";
import type {
  NotificationCategory,
  NotificationDetail,
  NotificationFilters,
  NotificationItem,
  NotificationPayload,
  NotificationPrefs,
  NotificationSeverity,
} from "@/lib/notification-types";
import { cn } from "@/lib/utils";
import { NotificationRow, SeverityBadge, useMarkAllRead, useMarkRead, useNotificationTz } from "./notifications";

export const PAGE_SIZE = 25;

export const CATEGORY_TABS: { key: string; label: string; filters: NotificationFilters }[] = [
  { key: "all", label: "All", filters: {} },
  { key: "unread", label: "Unread", filters: { read: "unread" } },
  { key: "signal", label: "Signal alerts", filters: { category: "signal" } },
  { key: "trade", label: "Trade results", filters: { category: "trade" } },
  { key: "risk", label: "Risk warnings", filters: { category: "risk" } },
  { key: "market", label: "Market alerts", filters: { category: "market" } },
  { key: "system", label: "System", filters: { category: "system" } },
  { key: "validation", label: "Strategy validation", filters: { category: "validation" } },
];

const CATEGORY_LABEL: Record<NotificationCategory, string> = {
  signal: "Signal alerts", trade: "Trade results", risk: "Risk warnings", market: "Market alerts",
  system: "System", validation: "Strategy validation",
};
const SEVERITIES: NotificationSeverity[] = ["INFO", "SUCCESS", "WARNING", "CRITICAL"];

/** Filters shown in the URL map one-to-one onto the API. Empty values are dropped. */
export function toQuery(tab: string, f: FilterState, tz: string, page: number): NotificationFilters {
  const base = CATEGORY_TABS.find((t) => t.key === tab)?.filters ?? {};
  return {
    ...base,
    symbol: f.symbol || undefined,
    strategy: f.strategy || undefined,
    timeframe: f.timeframe || undefined,
    severity: f.severity || undefined,
    read: f.read || base.read,
    date_from: f.from ? zonedDayBoundary(f.from, tz, false) : undefined,
    date_to: f.to ? zonedDayBoundary(f.to, tz, true) : undefined,
    include_muted: f.includeMuted || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
}

export interface FilterState {
  symbol: string; strategy: string; timeframe: string; severity: string; read: string; from: string; to: string; includeMuted: boolean;
}

export const EMPTY_FILTERS: FilterState = { symbol: "", strategy: "", timeframe: "", severity: "", read: "", from: "", to: "", includeMuted: false };

function FilterBar({ f, set }: { f: FilterState; set: (f: FilterState) => void }) {
  const facets = useQuery({ queryKey: ["notif", "facets"], queryFn: api.notificationFacets, staleTime: 60_000 });
  const update = (patch: Partial<FilterState>) => set({ ...f, ...patch });
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
      <Field label="Symbol">
        <NativeSelect aria-label="Symbol" value={f.symbol} onChange={(e) => update({ symbol: e.target.value })}>
          <option value="">All</option>
          {[...new Set([...(facets.data?.symbols ?? []), ...(f.symbol ? [f.symbol] : [])])].map((s) => <option key={s}>{s}</option>)}
        </NativeSelect>
      </Field>
      <Field label="Strategy">
        <NativeSelect aria-label="Strategy" value={f.strategy} onChange={(e) => update({ strategy: e.target.value })}>
          <option value="">All</option>
          {["A", "B", "C", "D"].map((s) => <option key={s} value={s}>Strategy {s}</option>)}
        </NativeSelect>
      </Field>
      <Field label="Timeframe">
        <NativeSelect aria-label="Timeframe" value={f.timeframe} onChange={(e) => update({ timeframe: e.target.value })}>
          <option value="">All</option>
          {(facets.data?.timeframes ?? []).map((t) => <option key={t}>{t}</option>)}
        </NativeSelect>
      </Field>
      <Field label="Severity">
        <NativeSelect aria-label="Severity" value={f.severity} onChange={(e) => update({ severity: e.target.value })}>
          <option value="">All</option>
          {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
        </NativeSelect>
      </Field>
      <Field label="Status">
        <NativeSelect aria-label="Status" value={f.read} onChange={(e) => update({ read: e.target.value })}>
          <option value="">All</option>
          <option value="unread">Unread</option>
          <option value="read">Read</option>
        </NativeSelect>
      </Field>
      <Field label="From"><Input aria-label="From date" type="date" value={f.from} onChange={(e) => update({ from: e.target.value })} /></Field>
      <Field label="To"><Input aria-label="To date" type="date" value={f.to} onChange={(e) => update({ to: e.target.value })} /></Field>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/50 py-1 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular text-right">{children}</span>
    </div>
  );
}

function SignalContext({ p, tz }: { p: NotificationPayload; tz: string }) {
  return (
    <div>
      {p.direction && <div className="mb-2"><DirectionBadge direction={p.direction} /></div>}
      <Row label="Signal time">{p.signal_time ? fmtZoned(p.signal_time, tz) : "—"}</Row>
      <Row label="Strategy">{p.strategy ?? "—"}</Row>
      <Row label="Entry reference">{fmtPrice(p.entry_reference)}</Row>
      <Row label="Stop-loss">{fmtPrice(p.stop_loss)}</Row>
      <Row label="Take-profit">{fmtPrice(p.take_profit)}</Row>
      <Row label="Baseline signal status">{p.baseline_status ?? "—"}</Row>
      {p.decision && (
        <div className={cn("mt-2 rounded-md border px-2.5 py-2 text-sm", p.decision.enforced ? "border-bear/50 bg-bear/10" : "border-primary/30 bg-primary/5")}>
          <div className="font-medium">{p.decision.label}</div>
          {p.decision.reason && <div className="mt-0.5 text-xs text-muted-foreground">{p.decision.reason}</div>}
        </div>
      )}
      {p.reasoning && <p className="mt-2 text-sm text-muted-foreground">{p.reasoning}</p>}
    </div>
  );
}

function TradeContext({ p }: { p: NotificationPayload }) {
  const fees = (p.entry_fee ?? 0) + (p.exit_fee ?? 0);
  return (
    <div>
      <Badge variant="warn" className="mb-2">PAPER TRADING: simulated, not real money</Badge>
      <Row label="Entry reference">{fmtPrice(p.reference_entry)}</Row>
      <Row label="Simulated fill">{fmtPrice(p.fill_price)}</Row>
      <Row label="Position size">{fmtNum(p.quantity, 4)}</Row>
      <Row label="Stop-loss">{fmtPrice(p.stop_loss)}</Row>
      <Row label="Take-profit">{fmtPrice(p.take_profit)}</Row>
      {p.exit_price != null && <Row label="Exit price">{fmtPrice(p.exit_price)}</Row>}
      {p.net_pnl != null && <Row label="Realized simulated P&L">{fmtUsd(p.net_pnl)}</Row>}
      {p.realized_pnl != null && p.net_pnl == null && <Row label="Realized simulated P&L">{fmtUsd(p.realized_pnl)}</Row>}
      {p.remaining_quantity != null && <Row label="Remaining position">{fmtNum(p.remaining_quantity, 4)}</Row>}
      {p.net_r != null && <Row label="Net R">{fmtNum(p.net_r, 2)}R</Row>}
      <Row label="Fees">{fmtUsd(fees)}</Row>
      <Row label="Slippage">{fmtUsd(p.slippage_cost)}</Row>
      <Row label="Spread">{fmtUsd(p.spread_cost)}</Row>
      <Row label="Funding">{p.funding_cost != null ? `${fmtUsd(p.funding_cost)}${p.cost_flags?.funding ? ` (${p.cost_flags.funding.replaceAll("_", " ")})` : ""}` : "not available"}</Row>
      {p.duration && <Row label="Duration">{p.duration}</Row>}
      <Row label="Paper portfolio">{p.portfolio_taken ? "Taken" : `Not taken${p.portfolio_reason ? `: ${p.portfolio_reason}` : ""}`}</Row>
    </div>
  );
}

function RiskContext({ p }: { p: NotificationPayload }) {
  return (
    <div>
      <Badge variant="warn" className="mb-2">Paper portfolio only. No real trades are affected.</Badge>
      {p.threshold && Object.entries(p.threshold).map(([k, v]) => <Row key={k} label={k.replaceAll("_", " ")}>{String(v)}</Row>)}
      {p.affected_positions && (
        <div className="mt-2">
          <div className="mb-1 text-xs font-semibold">Affected positions ({p.affected_positions.length})</div>
          {p.affected_positions.length === 0 ? <p className="text-xs text-muted-foreground">No open positions.</p> : (
            <ul className="space-y-0.5 text-xs">
              {p.affected_positions.map((x) => <li key={`${x.symbol}${x.timeframe}${x.direction}`}>{x.direction} {x.symbol} {x.timeframe} · risk {fmtUsd(x.risk_amount)}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export function NotificationDetailView({ d, tz, onSelect }: { d: NotificationDetail; tz: string; onSelect: (n: NotificationItem) => void }) {
  const markRead = useMarkRead();
  const isTrade = !!d.paper_trade_id;
  const isSignal = d.event_type.startsWith("SIGNAL_") && !isTrade;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-1.5">
          <SeverityBadge severity={d.severity} />
          <Badge variant="outline">{CATEGORY_LABEL[d.category]}</Badge>
          {d.strategy_id && <Badge variant="info">Strategy {d.strategy_id}</Badge>}
          {d.muted && <Badge variant="outline">Muted</Badge>}
        </div>
        <CardTitle className="mt-1">{d.title}</CardTitle>
        <CardDescription>
          {fmtZoned(d.occurred_at, tz)} · {d.event_type}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="whitespace-pre-line text-sm">{d.message}</p>
        {isSignal && <SignalContext p={d.payload} tz={tz} />}
        {isTrade && !d.event_type.startsWith("RISK_") && <TradeContext p={d.payload} />}
        {d.category === "risk" && <RiskContext p={d.payload} />}
        {d.members.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-semibold">Individual records ({d.members.length})</div>
            <div className="space-y-0.5">{d.members.map((m) => <NotificationRow key={m.id} n={m} tz={tz} onSelect={onSelect} />)}</div>
          </div>
        )}
        {d.incident && (
          <div>
            <Row label="Incident">{d.incident.status}</Row>
            <Row label="Started">{fmtZoned(d.incident.started_at, tz)}</Row>
            {d.incident.resolved_at && <Row label="Resolved">{fmtZoned(d.incident.resolved_at, tz)}</Row>}
            <Row label="Checks while open">{d.incident.occurrences}</Row>
          </div>
        )}
        {d.deliveries.map((x) => (
          <div key={x.id} className="rounded-md border px-2.5 py-1.5 text-xs">
            <span className="font-medium capitalize">{x.channel}</span>: {x.status} · {x.attempts} attempt{x.attempts === 1 ? "" : "s"}
            {x.sent_at && <> · sent {fmtZoned(x.sent_at, tz)}</>}
            {x.last_error && <div className="mt-0.5 text-muted-foreground">{x.last_error}</div>}
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          {d.links.map((l) => <Button key={l.href} asChild variant="outline" size="sm"><Link href={l.href}>{l.label}</Link></Button>)}
          <Button variant="ghost" size="sm" onClick={() => markRead.mutate({ id: d.id, read: !d.read_at })}>
            Mark as {d.read_at ? "unread" : "read"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function Pagination({ page, total, onPage }: { page: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <div className="flex items-center justify-between gap-2 pt-2 text-xs text-muted-foreground">
      <span>{total} notification{total === 1 ? "" : "s"} · page {page + 1} of {pages}</span>
      <span className="flex gap-1">
        <Button variant="outline" size="sm" disabled={page === 0} onClick={() => onPage(page - 1)}>Previous</Button>
        <Button variant="outline" size="sm" disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}>Next</Button>
      </span>
    </div>
  );
}

export function NotificationFeed({ tab, initial, selectedId, onSelect }: {
  tab: string; initial?: Partial<FilterState>; selectedId: number | null; onSelect: (id: number) => void;
}) {
  const tz = useNotificationTz();
  const [f, setF] = useState<FilterState>({ ...EMPTY_FILTERS, ...initial });
  const [page, setPage] = useState(0);
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();
  useEffect(() => setPage(0), [tab, f]);
  const query = toQuery(tab, f, tz, page);
  const list = useQuery({ queryKey: ["notif", "list", query], queryFn: () => api.notificationEvents(query), placeholderData: keepPreviousData });
  const detail = useQuery({ queryKey: ["notif", "detail", selectedId], queryFn: () => api.notificationEvent(selectedId!), enabled: selectedId != null });

  const select = (n: NotificationItem) => {
    if (!n.read_at) markRead.mutate({ id: n.id });
    onSelect(n.id);
  };

  return (
    <div className="space-y-3">
      <FilterBar f={f} set={setF} />
      <ErrorBox error={list.error} />
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
        <Card>
          <CardHeader className="flex-row items-center justify-between gap-2">
            <CardTitle>Notifications</CardTitle>
            <Button variant="ghost" size="sm" disabled={!list.data?.unread.total || markAll.isPending} onClick={() => markAll.mutate()}>
              <CheckCheck className="h-3.5 w-3.5" /> Mark all as read
            </Button>
          </CardHeader>
          <CardContent>
            {list.isLoading ? <Empty>Loading notifications…</Empty> : list.data?.items.length === 0 ? (
              <Empty>No notifications match these filters.</Empty>
            ) : (
              <div className="space-y-0.5">
                {list.data?.items.map((n) => <NotificationRow key={n.id} n={n} tz={tz} onSelect={select} active={n.id === selectedId} />)}
              </div>
            )}
            {list.data && <Pagination page={page} total={list.data.total} onPage={setPage} />}
          </CardContent>
        </Card>
        <div className="lg:sticky lg:top-20">
          {selectedId == null ? <Empty>Select a notification to see its context.</Empty> : detail.error ? <ErrorBox error={detail.error} /> : detail.data ? (
            <NotificationDetailView d={detail.data} tz={tz} onSelect={select} />
          ) : <Empty>Loading…</Empty>}
        </div>
      </div>
    </div>
  );
}

export function IncidentHistory() {
  const tz = useNotificationTz();
  const q = useQuery({ queryKey: ["notif", "incidents"], queryFn: api.notificationIncidents });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Incident history</CardTitle>
        <CardDescription>One entry per outage: a problem that keeps retrying updates its entry instead of alerting again.</CardDescription>
      </CardHeader>
      <CardContent>
        <ErrorBox error={q.error} />
        {q.data?.length === 0 && <Empty>No incidents recorded.</Empty>}
        <ul className="space-y-2">
          {q.data?.map((i) => (
            <li key={i.id} className={cn("rounded-md border px-3 py-2 text-sm", i.status === "OPEN" && i.severity === "CRITICAL" && "border-bear/50 bg-bear/10")}>
              <div className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={i.severity} />
                <Badge variant={i.status === "OPEN" ? "warn" : "bull"}>{i.status}</Badge>
                <span className="font-medium">{i.title}</span>
              </div>
              <p className="mt-1 text-muted-foreground">{i.message}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Started {fmtZoned(i.started_at, tz)}{i.resolved_at ? ` · resolved ${fmtZoned(i.resolved_at, tz)}` : ""} · {i.occurrences} check{i.occurrences === 1 ? "" : "s"} while open
              </p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

// Early "getting close" messages. Listed first because they are the most frequent.
const WATCHLIST_OPTION = { key: "WATCHLIST_DIGEST", label: "Watchlist summaries (not signals)", fallback: true };

const TELEGRAM_EVENT_OPTIONS: { key: string; label: string; fallback: boolean }[] = [
  { key: "SIGNAL_QUALIFIED", label: "New qualified signal", fallback: true },
  { key: "PAPER_ENTRY_FILLED", label: "Paper trade entry", fallback: false },
  { key: "PAPER_TP1_HIT", label: "Take-profit (TP1)", fallback: true },
  { key: "PAPER_TP2_HIT", label: "Take-profit (TP2)", fallback: true },
  { key: "PAPER_TP3_HIT", label: "Take-profit (TP3)", fallback: true },
  { key: "PAPER_STOP_LOSS_HIT", label: "Stop-loss", fallback: true },
  { key: "PAPER_TRADE_CLOSED", label: "Trade closed at time limit", fallback: true },
  { key: "SIGNAL_REJECTED", label: "Rejected candidate", fallback: false },
];

// Hidden from the feed when off; still recorded in the history.
const CANDIDATE_TOGGLES = [
  { event: "SIGNAL_REJECTED", label: "Show rejected candidates", hint: "Setups that triggered but failed a rule. Several can appear on one candle." },
  { event: "SIGNAL_DETECTED", label: "Show watchlist candidates", hint: "Early candidates with no entry, stop or target yet. Off by default because they are frequent." },
];

function Toggle({ label, hint, checked, onChange, disabled }: { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span>
        <span className="text-sm">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
      <Switch aria-label={label} checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </div>
  );
}

const csv = (v: string) => v.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);

export function NotificationPreferences() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["notif-prefs"], queryFn: api.notificationPrefs });
  const [draft, setDraft] = useState<NotificationPrefs | null>(null);
  useEffect(() => {
    if (q.data) setDraft(q.data.preferences);
  }, [q.data]);
  const save = useMutation({
    mutationFn: (p: NotificationPrefs) => api.saveNotificationPrefs(p),
    onSuccess: (r) => qc.setQueryData(["notif-prefs"], r),
  });
  if (q.error) return <ErrorBox error={q.error} />;
  if (!draft || !q.data) return <Empty>Loading preferences…</Empty>;
  const set = (patch: Partial<NotificationPrefs>) => setDraft({ ...draft, ...patch });
  const setCat = (c: NotificationCategory, patch: Partial<NotificationPrefs["categories"][NotificationCategory]>) =>
    set({ categories: { ...draft.categories, [c]: { ...draft.categories[c], ...patch } } });
  const tgReady = q.data.telegram_configured && q.data.telegram_enabled;
  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Channels by category</CardTitle>
          <CardDescription>
            {tgReady ? "Telegram is on." : q.data.telegram_configured ? "Telegram alerts are switched off in Settings." : "Telegram is not configured on the server."}{" "}
            Muted events are still recorded, so critical risk and system incidents always stay in the history.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Toggle label="In-app notifications" checked={draft.in_app_enabled} onChange={(v) => set({ in_app_enabled: v })} />
          {(Object.keys(CATEGORY_LABEL) as NotificationCategory[]).map((c) => (
            <div key={c} className="grid grid-cols-[1fr_auto_auto_90px] items-center gap-2 border-t pt-2 text-sm sm:gap-3">
              <span>{CATEGORY_LABEL[c]}</span>
              <label className="flex items-center gap-1 text-xs text-muted-foreground">In-app
                <Switch aria-label={`${CATEGORY_LABEL[c]} in-app`} checked={draft.categories[c].in_app} onCheckedChange={(v) => setCat(c, { in_app: v })} />
              </label>
              <label className="flex items-center gap-1 text-xs text-muted-foreground">Telegram
                <Switch aria-label={`${CATEGORY_LABEL[c]} Telegram`} disabled={!tgReady} checked={draft.categories[c].telegram} onCheckedChange={(v) => setCat(c, { telegram: v })} />
              </label>
              <NativeSelect aria-label={`${CATEGORY_LABEL[c]} minimum severity`} className="h-8" value={draft.categories[c].min_severity} onChange={(e) => setCat(c, { min_severity: e.target.value as NotificationSeverity })}>
                {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
              </NativeSelect>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Telegram events</CardTitle>
          <CardDescription>Critical risk and system events go to Telegram automatically. Other risk events and rejected candidates stay in-app. To stop risk alerts on Telegram, switch off Telegram for Risk under Channels; they are still recorded here.</CardDescription>
        </CardHeader>
        <CardContent>
          <Field label="Message style" hint="Compact sends the key numbers. Full reasoning stays in this app.">
            <NativeSelect aria-label="Telegram message style" className="mb-2" value={draft.telegram_format ?? "compact"} onChange={(e) => set({ telegram_format: e.target.value as NotificationPrefs["telegram_format"] })}>
              <option value="compact">Compact (recommended)</option>
              <option value="detailed">Detailed</option>
            </NativeSelect>
          </Field>
          {[WATCHLIST_OPTION, ...TELEGRAM_EVENT_OPTIONS].map((o) => (
            <Toggle key={o.key} label={o.label} disabled={!tgReady} checked={draft.telegram_events[o.key] ?? o.fallback}
              onChange={(v) => set({ telegram_events: { ...draft.telegram_events, [o.key]: v } })} />
          ))}
          <Toggle label="Only trades the paper portfolio took" hint="Trades blocked by the risk limits stay in-app." disabled={!tgReady}
            checked={draft.telegram_portfolio_only} onChange={(v) => set({ telegram_portfolio_only: v })} />
          <Toggle label="Strategy D research candidates" hint="Off by default. D is an unvalidated shadow strategy, so its candidates stay in-app unless you switch this on." disabled={!tgReady}
            checked={draft.strategy_d_telegram ?? false} onChange={(v) => set({ strategy_d_telegram: v })} />
          <Toggle label="List 15m watchlist setups first" disabled={!tgReady}
            checked={draft.watchlist_15m_first ?? true} onChange={(v) => set({ watchlist_15m_first: v })} />
          <div className="mt-2 grid gap-3 sm:grid-cols-2">
            <Field label="Watchlist grouping (minutes)" hint="New setups wait this long, so several arrive in one message.">
              <Input type="number" min={5} max={240} value={draft.watchlist_interval_minutes ?? 15} onChange={(e) => set({ watchlist_interval_minutes: Number(e.target.value) })} />
            </Field>
            <Field label="Public dashboard address" hint="Optional. Adds links to messages. Must open from a phone, so localhost is refused.">
              <Input type="url" placeholder="https://…" value={draft.dashboard_url ?? ""} onChange={(e) => set({ dashboard_url: e.target.value || null })} />
            </Field>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>What to monitor</CardTitle><CardDescription>Leave empty to monitor everything.</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          <Field label="Minimum severity">
            <NativeSelect value={draft.min_severity} onChange={(e) => set({ min_severity: e.target.value as NotificationSeverity })}>
              {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
            </NativeSelect>
          </Field>
          <div>
            <div className="mb-1 text-xs font-medium text-muted-foreground">Strategies</div>
            <div className="flex gap-3">
              {["A", "B", "C", "D"].map((s) => (
                <label key={s} className="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" checked={draft.strategies.includes(s)}
                    onChange={(e) => set({ strategies: e.target.checked ? [...draft.strategies, s] : draft.strategies.filter((x) => x !== s) })} />
                  Strategy {s}
                </label>
              ))}
            </div>
          </div>
          <Field label="Coins (comma separated, e.g. BTCUSDT, ETHUSDT)">
            <Input defaultValue={draft.symbols.join(", ")} onBlur={(e) => set({ symbols: csv(e.target.value) })} />
          </Field>
          <Field label="Timeframes (comma separated, e.g. 15m, 1h)">
            <Input defaultValue={draft.timeframes.join(", ")} onBlur={(e) => set({ timeframes: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
          </Field>
          {CANDIDATE_TOGGLES.map((t) => (
            <Toggle key={t.event} label={t.label} hint={t.hint} checked={!draft.muted_events.includes(t.event)}
              onChange={(v) => set({ muted_events: v ? draft.muted_events.filter((x) => x !== t.event) : [...draft.muted_events, t.event] })} />
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Display and retention</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <Toggle label="Sound for new notifications" checked={draft.sound} onChange={(v) => set({ sound: v })} />
          <Toggle label="Group related notifications" hint="A/B/C evaluations of one signal and bursts of correlated signals. Individual records stay available."
            checked={draft.grouping.enabled} onChange={(v) => set({ grouping: { ...draft.grouping, enabled: v } })} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Correlated signals to group">
              <Input type="number" min={2} max={50} value={draft.grouping.correlated_min_signals}
                onChange={(e) => set({ grouping: { ...draft.grouping, correlated_min_signals: Number(e.target.value) } })} />
            </Field>
            <Field label="Group window (minutes)">
              <Input type="number" min={5} max={1440} value={draft.grouping.correlated_window_minutes}
                onChange={(e) => set({ grouping: { ...draft.grouping, correlated_window_minutes: Number(e.target.value) } })} />
            </Field>
            <Field label="Keep notifications (days)" hint="Critical events are always kept.">
              <Input type="number" min={7} max={3650} value={draft.retention_days} onChange={(e) => set({ retention_days: Number(e.target.value) })} />
            </Field>
            <Field label="Telegram message time zone" hint="Times in the app follow the display time zone in Settings.">
              <Input value={draft.timezone} onChange={(e) => set({ timezone: e.target.value })} />
            </Field>
          </div>
          <ErrorBox error={save.error} />
          <div className="flex items-center gap-2">
            <Button size="sm" disabled={save.isPending} onClick={() => save.mutate(draft)}>Save preferences</Button>
            {save.isSuccess && !save.isPending && <span className="text-xs text-bull">Saved</span>}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
