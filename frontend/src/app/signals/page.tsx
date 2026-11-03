"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { PageHeader } from "@/components/app-shell";
import { ScoreBar, SignalDetail } from "@/components/signal-detail";
import { DirectionBadge, Empty, ErrorBox } from "@/components/status";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { fmtPrice, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

function SignalsView() {
  const search = useSearchParams();
  const router = useRouter();
  const active = useQuery({ queryKey: ["signals", "active"], queryFn: () => api.signals({ status: "active", limit: 100 }) });
  const selectedId = search.get("id") ?? active.data?.items[0]?.id ?? null;
  const detail = useQuery({
    queryKey: ["signals", "detail", selectedId],
    queryFn: () => api.signal(selectedId!),
    enabled: !!selectedId,
  });

  return (
    <>
      <PageHeader
        title="Trading Signals"
        description="Active setups detected on confirmed candles. Entry, stop and target are reference levels; nothing is executed."
      />
      <ErrorBox error={active.error ?? detail.error} />
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Active setups ({active.data?.total ?? 0})</CardTitle>
            <CardDescription>Remain active until the outcome methodology resolves them.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {active.data?.items.map((s) => (
              <button
                key={s.id}
                onClick={() => router.push(`/signals?id=${s.id}`)}
                className={cn(
                  "w-full rounded-md border p-2.5 text-left text-sm transition-colors hover:bg-accent",
                  s.id === selectedId && "border-primary bg-accent",
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium">{s.symbol}</span>
                  <DirectionBadge direction={s.direction} />
                  <span className="text-xs text-muted-foreground">{s.timeframe}</span>
                  <span className="ml-auto text-xs text-muted-foreground">{timeAgo(s.created_at)}</span>
                </div>
                <div className="mt-1 flex items-center justify-between text-xs">
                  <span className="tabular text-muted-foreground">Entry ref {fmtPrice(s.entry_reference)}</span>
                  <ScoreBar score={s.score} />
                </div>
              </button>
            ))}
            {active.data && active.data.items.length === 0 && <Empty>No active setups right now.</Empty>}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            {detail.data ? (
              <SignalDetail signal={detail.data} />
            ) : (
              <Empty>{selectedId ? "Loading signal…" : "Select a signal to see its explanation."}</Empty>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

export default function SignalsPage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <SignalsView />
    </Suspense>
  );
}
