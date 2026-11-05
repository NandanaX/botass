"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { PageHeader } from "@/components/app-shell";
import { CATEGORY_TABS, IncidentHistory, NotificationFeed, NotificationPreferences } from "@/components/notification-center";
import { useUnread } from "@/components/notifications";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const EXTRA_TABS = [{ key: "incidents", label: "Incident history" }, { key: "preferences", label: "Preferences" }];

function Center() {
  const search = useSearchParams();
  const router = useRouter();
  const unread = useUnread();
  const tab = search.get("tab") ?? "all";
  const rawId = Number(search.get("id"));
  const selectedId = Number.isInteger(rawId) && rawId > 0 ? rawId : null;
  const go = (params: Record<string, string>) => router.push(`/notifications?${new URLSearchParams(params)}`);
  const count = unread.data?.total ?? 0;
  return (
    <>
      <PageHeader
        title="Notification Center"
        description="Signals, paper-trade results, risk events and system incidents, stored permanently and updated live. Paper results are simulated; no real orders are placed."
      />
      <Tabs value={tab} onValueChange={(v) => go({ tab: v })} className="mb-4">
        <TabsList className="flex h-auto max-w-full flex-wrap justify-start">
          {[...CATEGORY_TABS, ...EXTRA_TABS].map((t) => (
            <TabsTrigger key={t.key} value={t.key}>{t.label}{t.key === "unread" && count ? ` (${count})` : ""}</TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {tab === "incidents" ? <IncidentHistory /> : tab === "preferences" ? <NotificationPreferences /> : (
        <NotificationFeed
          key={`${tab}|${search.get("severity") ?? ""}|${search.get("read") ?? ""}`}
          tab={tab}
          initial={{ severity: search.get("severity") ?? "", read: search.get("read") ?? "" }}
          selectedId={selectedId}
          onSelect={(id) => go({ ...(tab === "all" ? {} : { tab }), id: String(id) })}
        />
      )}
    </>
  );
}

export default function NotificationsPage() {
  return <Suspense><Center /></Suspense>;
}
