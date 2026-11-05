"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Bell, CheckCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { fmtZoned, timeAgo } from "@/lib/format";
import { useDisplayTz } from "@/lib/timezone";
import type { NotificationItem, NotificationSeverity } from "@/lib/notification-types";
import { cn } from "@/lib/utils";

const SEVERITY_BADGE: Record<NotificationSeverity, "default" | "bull" | "warn" | "bear"> = {
  INFO: "default", SUCCESS: "bull", WARNING: "warn", CRITICAL: "bear",
};

export function SeverityBadge({ severity }: { severity: NotificationSeverity }) {
  return <Badge variant={SEVERITY_BADGE[severity]}>{severity}</Badge>;
}

export const unreadLabel = (n: number) => (n > 99 ? "99+" : String(n));

export function useUnread() {
  return useQuery({ queryKey: ["notif-unread"], queryFn: api.notificationUnread, refetchInterval: 60_000 });
}

export function useNotificationTz(): string {
  return useDisplayTz();
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, read = true }: { id: number; read?: boolean }) => api.notificationRead(id, read),
    onSuccess: (r) => {
      qc.setQueryData(["notif-unread"], r.unread);
      qc.invalidateQueries({ queryKey: ["notif"] });
    },
  });
}

export function useMarkAllRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.notificationReadAll(),
    onSuccess: (r) => {
      qc.setQueryData(["notif-unread"], r.unread);
      qc.invalidateQueries({ queryKey: ["notif"] });
    },
  });
}

export function NotificationRow({ n, tz, onSelect, active }: {
  n: NotificationItem; tz: string; onSelect: (n: NotificationItem) => void; active?: boolean;
}) {
  const unread = !n.read_at;
  return (
    <button
      type="button"
      onClick={() => onSelect(n)}
      className={cn(
        "flex w-full gap-2 rounded-md border px-2.5 py-2 text-left transition-colors hover:bg-accent",
        active ? "border-primary/50 bg-accent" : "border-transparent",
        n.severity === "CRITICAL" && "border-bear/50 bg-bear/10",
      )}
    >
      <span aria-label={unread ? "Unread" : "Read"} className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", unread ? "bg-primary" : "bg-transparent")} />
      <span className="min-w-0 flex-1">
        <span className={cn("block text-sm leading-snug", unread && "font-semibold")}>{n.title}</span>
        <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
          <SeverityBadge severity={n.severity} />
          {n.strategy_id && <Badge variant="outline">Strategy {n.strategy_id}</Badge>}
          {n.is_group_summary && <Badge variant="info">Grouped</Badge>}
          {n.muted && <Badge variant="outline">Muted</Badge>}
          <time dateTime={n.occurred_at} title={fmtZoned(n.occurred_at, tz)}>{timeAgo(n.occurred_at)}</time>
        </span>
      </span>
    </button>
  );
}

/** Header bell: unread badge and a popover with the latest notifications. */
export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const unread = useUnread();
  const tz = useNotificationTz();
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();
  const latest = useQuery({ queryKey: ["notif", "latest"], queryFn: () => api.notificationEvents({ limit: 8 }), enabled: open });
  const count = unread.data?.total ?? 0;
  const critical = unread.data?.critical ?? 0;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const select = (n: NotificationItem) => {
    if (!n.read_at) markRead.mutate({ id: n.id });
    setOpen(false);
    router.push(`/notifications?id=${n.id}`);
  };

  return (
    <div className="relative" ref={root}>
      <button
        type="button"
        aria-label={count ? `Notifications, ${count} unread` : "Notifications"}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="relative flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <Bell className="h-4 w-4" />
        {count > 0 && (
          <span data-testid="unread-badge" className={cn("absolute -right-1 -top-1 min-w-4 rounded-full px-1 text-center text-[10px] font-semibold leading-4 text-white", critical ? "bg-bear" : "bg-primary")}>
            {unreadLabel(count)}
          </span>
        )}
      </button>
      {open && (
        <div role="dialog" aria-label="Latest notifications" className="fixed inset-x-3 top-14 z-30 rounded-lg border bg-card shadow-lg sm:absolute sm:inset-x-auto sm:right-0 sm:top-10 sm:w-96">
          <div className="flex items-center justify-between border-b px-3 py-2">
            <span className="text-sm font-semibold">Notifications</span>
            <Button variant="ghost" size="sm" disabled={!count || markAll.isPending} onClick={() => markAll.mutate()}>
              <CheckCheck className="h-3.5 w-3.5" /> Mark all as read
            </Button>
          </div>
          <div className="max-h-96 space-y-0.5 overflow-y-auto p-1.5">
            {latest.isLoading && <p className="py-6 text-center text-sm text-muted-foreground">Loading…</p>}
            {latest.error ? <p className="py-6 text-center text-sm text-bear">Could not load notifications.</p> : null}
            {latest.data?.items.length === 0 && <p className="py-6 text-center text-sm text-muted-foreground">No notifications yet.</p>}
            {latest.data?.items.map((n) => <NotificationRow key={n.id} n={n} tz={tz} onSelect={select} />)}
          </div>
          <div className="border-t p-1.5 text-center">
            <Link href="/notifications" onClick={() => setOpen(false)} className="block rounded px-2 py-1.5 text-sm text-primary hover:bg-accent">
              View All
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

/** Critical risk and system events stay visible on every page until they are read. */
export function CriticalBanner() {
  const unread = useUnread();
  const critical = unread.data?.critical ?? 0;
  if (!critical) return null;
  return (
    <Link
      href="/notifications?severity=CRITICAL&read=unread"
      className="mt-2 flex items-center gap-2 rounded-md border border-bear/50 bg-bear/15 px-3 py-1.5 text-sm font-medium text-bear"
    >
      <AlertTriangle className="h-4 w-4 shrink-0" />
      {critical} unread critical {critical === 1 ? "alert" : "alerts"}. Review now.
    </Link>
  );
}
