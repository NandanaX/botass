"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/app-shell";
import { Empty, ErrorBox } from "@/components/status";
import { Button } from "@/components/ui/button";
import { ForwardSection, HistoricalSection, PromotionSection } from "@/components/validation";
import { api } from "@/lib/api";

export default function ValidationPage() {
  const qc = useQueryClient();
  const ov = useQuery({ queryKey: ["validation-overview"], queryFn: api.validationOverview, refetchInterval: 60_000 });
  const hist = useQuery({ queryKey: ["validation-historical"], queryFn: api.validationHistorical, retry: false,
    refetchInterval: (q) => (q.state.data?.status?.state === "running" ? 5000 : false) });
  const run = useMutation({ mutationFn: api.runValidationHistorical, onSuccess: () => qc.invalidateQueries({ queryKey: ["validation-historical"] }) });
  const running = hist.data?.status?.state === "running" || run.isPending;
  return (
    <>
      <PageHeader
        title="Strategy Validation Monitor"
        description="A: baseline · B: 2.5 ATR stop · C: 2.5 ATR stop + higher-timeframe confirmation, evaluated in parallel on the same live signals. Paper trading only: real-money trading is disabled and enforcement is never switched on automatically."
        actions={<Button variant="outline" size="sm" disabled={running} onClick={() => run.mutate()}><RefreshCw className={`h-3.5 w-3.5 ${running ? "animate-spin" : ""}`} /> {running ? "Historical study running…" : "Re-run historical tests"}</Button>}
      />
      {ov.error ? <ErrorBox error={ov.error} /> : !ov.data ? <Empty>Loading forward results…</Empty> : (
        <>
          <ForwardSection o={ov.data} />
          <PromotionSection o={ov.data} />
        </>
      )}
      {hist.error ? <Empty>No historical robustness run yet. Click &ldquo;Re-run historical tests&rdquo;.</Empty> : !hist.data ? <Empty>Loading historical tests…</Empty> : <HistoricalSection h={hist.data} />}
    </>
  );
}
