"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellPlus, X } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/form";
import { api } from "@/lib/api";
import { fmtPrice, fmtTime } from "@/lib/format";
import type { Zone } from "@/lib/types";
import { ErrorBox } from "./status";

interface Props {
  symbol: string;
  inWatchlist: boolean;
  price: number | null | undefined;
  support: Zone | null | undefined;
  resistance: Zone | null | undefined;
}

/** Telegram price-level alerts for one symbol, with one-click levels from the nearest zones. */
export function PriceAlertsCard({ symbol, inWatchlist, price, support, resistance }: Props) {
  const qc = useQueryClient();
  const [level, setLevel] = useState("");
  const [note, setNote] = useState("");
  const alerts = useQuery({ queryKey: ["price-alerts"], queryFn: () => api.priceAlerts(), refetchInterval: 10_000 });
  const refresh = () => qc.invalidateQueries({ queryKey: ["price-alerts"] });
  const create = useMutation({
    mutationFn: (b: { price: number; condition?: "above" | "below"; note?: string }) => api.createPriceAlert({ symbol, ...b }),
    onSuccess: () => {
      setLevel("");
      setNote("");
      refresh();
    },
  });
  const cancel = useMutation({ mutationFn: (id: number) => api.cancelPriceAlert(id), onSuccess: refresh });

  const mine = (alerts.data ?? []).filter((a) => a.symbol === symbol);
  const active = mine.filter((a) => a.status === "ACTIVE").sort((a, b) => b.price - a.price);
  const triggered = mine.filter((a) => a.status === "TRIGGERED").slice(0, 3);
  const quick = [
    support && { label: `Breaks below support ${fmtPrice(support.lower)}`, price: support.lower, condition: "below" as const, note: `below ${support.timeframe} support ${fmtPrice(support.lower)}–${fmtPrice(support.upper)}` },
    resistance && { label: `Breaks above resistance ${fmtPrice(resistance.upper)}`, price: resistance.upper, condition: "above" as const, note: `above ${resistance.timeframe} resistance ${fmtPrice(resistance.lower)}–${fmtPrice(resistance.upper)}` },
  ].filter(Boolean) as { label: string; price: number; condition: "above" | "below"; note: string }[];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Price alerts</CardTitle>
        <CardDescription>Telegram message once when the live price reaches a level. A price notification, not a trade signal.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        {!inWatchlist ? (
          <p className="text-muted-foreground">Add {symbol} to the watchlist in Settings to stream its live price and set alerts.</p>
        ) : (
          <>
            {quick.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {quick.map((q) => (
                  <Button key={q.label} size="sm" variant="outline" disabled={create.isPending} onClick={() => create.mutate({ price: q.price, condition: q.condition, note: q.note })}>
                    <BellPlus /> {q.label}
                  </Button>
                ))}
              </div>
            )}
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (Number(level) > 0) create.mutate({ price: Number(level), note: note || undefined }); }}>
              <Input type="number" step="any" min="0" placeholder={price ? `Price (now ${fmtPrice(price)})` : "Price"} value={level} onChange={(e) => setLevel(e.target.value)} className="h-8" />
              <Input placeholder="Note (optional)" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} className="h-8" />
              <Button type="submit" size="sm" disabled={create.isPending || !(Number(level) > 0)}>Add</Button>
            </form>
            <ErrorBox error={create.error ?? cancel.error} />
          </>
        )}
        {active.length > 0 && (
          <div className="space-y-1">
            {active.map((a) => (
              <div key={a.id} className="flex items-center justify-between gap-2">
                <span>
                  <Badge variant="info">{a.condition === "above" ? "≥" : "≤"} {fmtPrice(a.price)}</Badge>
                  {a.note && <span className="ml-2 text-muted-foreground">{a.note}</span>}
                </span>
                <button className="text-muted-foreground hover:text-bear" aria-label="Cancel alert" onClick={() => cancel.mutate(a.id)}>
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
        {triggered.length > 0 && (
          <div className="space-y-0.5 text-muted-foreground">
            {triggered.map((a) => (
              <div key={a.id}>
                Triggered {fmtTime(a.triggered_at)}: {a.condition === "above" ? "≥" : "≤"} {fmtPrice(a.price)} at {fmtPrice(a.trigger_price)} · telegram {a.notification_status?.toLowerCase().replace("_", " ")}
              </div>
            ))}
          </div>
        )}
        {inWatchlist && active.length === 0 && <p className="text-muted-foreground">No active alerts for {symbol}.</p>}
      </CardContent>
    </Card>
  );
}
