"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { fmtDay, fmtNum } from "@/lib/format";
import type { P9Row, P9Short } from "@/lib/planning-types";

const VERDICT: Record<string, string> = {
  IMPROVES_IN_ALL_PERIODS: "Improves in every period",
  PROMISING: "Promising",
  NOT_VALIDATED: "No stable improvement",
  INSUFFICIENT_DATA: "Too few trades",
};
/** Plain names: "C" here would read as locked strategy C, which is 2.5 ATR + HTF, not a 3.0 ATR stop. */
const SHORTLIST_LABEL: Record<string, string> = {
  baseline_A: "Original stop",
  stop_B: "2.5 ATR",
  stop_C: "3.0 ATR",
  stop_F: "Hybrid",
  B_gate: "2.5 ATR + entry gate",
  B_htf: "2.5 ATR + HTF",
  C_htf: "3.0 ATR + HTF",
  F_htf: "Hybrid + HTF",
  B_htf_partial: "2.5 ATR + HTF + 30/40/30",
};
const shortName = (k: string) => SHORTLIST_LABEL[k] ?? k.replaceAll("_", " ");
const signed = (v: number | null | undefined, d = 3) => (v == null ? "—" : `${v > 0 ? "+" : ""}${fmtNum(v, d)}R`);
const th = "whitespace-nowrap border-b border-border px-2 py-1.5 text-left font-medium text-muted-foreground";
const td = "whitespace-nowrap border-b border-border/50 px-2 py-1.5 tabular";

function compareOf(r: P9Row) {
  return r.vs_original ?? r.vs_full_2R ?? r.vs_baseline ?? r.vs_baseline_A ?? null;
}

function Rows({ title, rows, label }: { title: string; rows: [string, P9Row][]; label?: (k: string, r: P9Row) => string }) {
  return (
    <div>
      <div className="mb-1 text-xs font-semibold">{title}</div>
      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={title}>
        <table className="w-full min-w-200 text-xs">
          <thead><tr>{["Variant", "Trades", "Avg net", "Later periods", "Wins", "Profit factor", "Max drawdown", "Stopped out", "Against baseline"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map(([k, r]) => {
              const c = compareOf(r);
              return (
                <tr key={k}>
                  <td className={td}>{label ? label(k, r) : r.label ?? k}</td>
                  <td className={td}>{r.n}</td>
                  <td className={td}>{signed(r.summary.avg_r)}</td>
                  <td className={td}>{signed(r.later_periods?.avg_r)}</td>
                  <td className={td}>{fmtNum(r.summary.win_rate_pct, 1)}%</td>
                  <td className={td}>{fmtNum(r.summary.profit_factor, 2)}</td>
                  <td className={td}>{fmtNum(r.summary.max_drawdown_r, 0)}R</td>
                  <td className={td}>{r.stop_out_pct != null ? `${fmtNum(r.stop_out_pct, 0)}%` : "—"}</td>
                  <td className={td}>{c ? VERDICT[c.research_verdict] ?? c.research_verdict : "Baseline"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Shortlist({ rows }: { rows: [string, P9Short][] }) {
  return (
    <div>
      <div className="mb-1 text-xs font-semibold">Stress and portfolio</div>
      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Stress and portfolio">
        <table className="w-full min-w-200 text-xs">
          <thead><tr>{["Variant", "Fees x2", "Delayed entry", "Stop at next open", "All adverse", "Survives all", "Portfolio trades", "Portfolio avg", "Return", "Drawdown"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map(([k, r]) => (
              <tr key={k}>
                <td className={td}>{shortName(k)}</td>
                <td className={td}>{signed(r.stress["fees x2"]?.avg_r)}</td>
                <td className={td}>{signed(r.stress["entry delayed 1 bar"]?.avg_r)}</td>
                <td className={td}>{signed(r.stress["stop executed next open"]?.avg_r)}</td>
                <td className={td}>{signed(r.stress["combined adverse"]?.avg_r)}</td>
                <td className={td}>{r.survives_all_stress ? "Yes" : "No"}</td>
                <td className={td}>{r.portfolio.fifo.taken}</td>
                <td className={td}>{signed(r.portfolio.fifo.avg_net_r)}</td>
                <td className={td}>{fmtNum(r.portfolio.fifo.return_pct, 1)}%</td>
                <td className={td}>{fmtNum(r.portfolio.fifo.max_drawdown_pct, 1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Historical research only. Shown in the Lab so it can't be mistaken for forward results. */
export function Phase9Section() {
  const q = useQuery({ queryKey: ["phase9-study"], queryFn: api.phase9Study, retry: false });
  const s = q.data;
  if (!s) return null;
  return (
    <Card className="mb-4" data-testid="phase9-study">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">Phase 9: stops, targets and entry quality <Badge variant="outline">{s.meta.evidence}</Badge></CardTitle>
        <CardDescription>
          {s.meta.label} {s.meta.signals.toLocaleString("en-US")} historical signals, {fmtDay(s.meta.data_start)} to {fmtDay(s.meta.data_end)}, net of fees, slippage and funding; {s.meta.configurations_tested} configurations tested, so some improvement is expected by chance alone. Full write-up: docs/RESEARCH_PHASE9.md.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Rows title="Stop methods, all with a 2R target" rows={Object.entries(s.stops)} />
        <Rows title="Exits on the original stop" rows={Object.entries(s.exits.A_original ?? {})} />
        <Rows title="Exits on a 2.5 ATR stop" rows={Object.entries(s.exits.B_atr_2_5 ?? {})} />
        <Rows title="Breakout entries" rows={[["All breakouts at the next open", s.retest.next_open], ["The same signals at the next open", s.retest.same_signals_next_open], ["Retest limit order, when it filled", { ...s.retest.retest, vs_baseline: s.retest.vs_same_signals }]]} label={(k) => k} />
        <p className="text-xs text-muted-foreground" data-testid="phase9-retest-note">
          The retest figures count a touch of the limit as a fill, and only {fmtNum(s.retest.fill_rate_pct, 0)}% of breakouts fill. If price must trade 0.1 ATR past the limit, the average falls to {signed(s.retest.stress["must trade 0.1 ATR past the limit"]?.avg_r)}. The same-signal comparison flatters it, because those are the signals where price came straight back.
        </p>
        <Rows title="Shortlist" rows={Object.entries(s.shortlist)} label={(k) => shortName(k)} />
        <Shortlist rows={Object.entries(s.shortlist)} />
      </CardContent>
    </Card>
  );
}
