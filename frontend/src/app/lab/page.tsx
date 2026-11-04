"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, RefreshCw } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/app-shell";
import { ForensicPanel, slTime, StudySections, TodaySection, todaySL } from "@/components/lab";
import { Empty, ErrorBox } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { TodayRow } from "@/lib/research-types";

// ---------------------------------------------------------------------------------- page

export default function LabPage() {
  const [day, setDay] = useState(todaySL());
  const [sel, setSel] = useState<TodayRow | null>(null);
  const qc = useQueryClient();
  const study = useQuery({ queryKey: ["study"], queryFn: api.study, retry: false });
  const status = useQuery({ queryKey: ["study-status"], queryFn: api.studyStatus, refetchInterval: (q) => (q.state.data?.state === "running" ? 5000 : false) });
  const run = useMutation({
    mutationFn: () => api.startStudy(true),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["study-status"] }); },
  });
  const running = status.data?.state === "running";
  if (status.data?.state === "done" && study.data && study.data.meta.generated_at && status.dataUpdatedAt > study.dataUpdatedAt) {
    qc.invalidateQueries({ queryKey: ["study"] });
  }
  return (
    <>
      <PageHeader
        title="Signal Intelligence Lab"
        description="Why signals failed, whether the evaluation is correct, and which changes improve out-of-sample net expectancy. Research only: outcomes are hypothetical replays, counterfactuals are simulations, and no order is ever placed."
        actions={
          <Button variant="outline" size="sm" disabled={running || run.isPending} onClick={() => run.mutate()}>
            <RefreshCw className={`h-3.5 w-3.5 ${running ? "animate-spin" : ""}`} /> {running ? `Study running: ${status.data?.step ?? ""}` : "Re-run study"}
          </Button>
        }
      />
      {status.data?.state === "failed" && <ErrorBox error={new Error(`Study failed: ${status.data.error}`)} />}
      <TodaySection day={day} setDay={(d) => { setDay(d); setSel(null); }} onSelect={setSel} selected={sel?.signal.id ?? null} />
      {sel && <ForensicPanel row={sel} />}
      {study.error ? (
        <Card className="mb-4"><CardContent className="p-4 text-sm"><FlaskConical className="mr-2 inline h-4 w-4" />No study results yet. Click &ldquo;Re-run study&rdquo; (downloads about a year of candles and replays the strategy; takes several minutes).</CardContent></Card>
      ) : !study.data ? <Empty>Loading study…</Empty> : (
        <>
          <p className="mb-3 text-xs text-muted-foreground">
            Study generated {slTime(study.data.meta.generated_at, true)} (Sri Lanka) on {study.data.meta.production_candidates} historical production signals ({study.data.meta.filled_trades} filled) across {(study.data.meta.symbols as string[] | undefined)?.length ?? "—"} coins and {(study.data.meta.timeframes as string[] | undefined)?.join("/")}; strategy {study.data.meta.strategy_version}. Costs: fee {study.data.meta.costs.fee_pct}% per side, entry slippage {study.data.meta.costs.entry_slip_pct}%, stop slippage {study.data.meta.costs.stop_slip_pct}%, funding included. Past performance does not guarantee future returns.
          </p>
          <StudySections s={study.data} />
        </>
      )}
    </>
  );
}
