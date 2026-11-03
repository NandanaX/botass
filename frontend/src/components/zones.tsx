import { Badge } from "@/components/ui/badge";
import { fmtPrice } from "@/lib/format";
import type { Zone } from "@/lib/types";

export function ZoneBadge({ zone }: { zone: Zone }) {
  const variant = zone.status === "INVALIDATED" ? "outline" : zone.zone_type === "SUPPORT" ? "bull" : "bear";
  return (
    <Badge variant={variant} title={zone.explanation}>
      {zone.zone_type === "SUPPORT" ? "Support" : "Resistance"} {zone.timeframe}
    </Badge>
  );
}

export function ZoneRange({ zone }: { zone: Zone | null | undefined }) {
  if (!zone) return <span className="text-muted-foreground">none detected</span>;
  return (
    <span className="tabular">
      {fmtPrice(zone.lower)} – {fmtPrice(zone.upper)}
    </span>
  );
}

/** Zones sorted by price (highest first) with status, tests, strength and the formation explanation on hover. */
export function ZoneList({ zones, price, empty = "No zones detected." }: { zones: Zone[]; price?: number | null; empty?: string }) {
  if (!zones.length) return <span className="text-xs text-muted-foreground">{empty}</span>;
  const sorted = zones.slice().sort((a, b) => b.upper - a.upper);
  let priceShown = price == null;
  return (
    <div className="space-y-1 text-xs">
      {sorted.map((z) => {
        const row = (
          <div key={z.id + z.timeframe} className="flex items-center justify-between gap-2" title={z.explanation}>
            <span className="flex items-center gap-1.5">
              <span className={z.status === "INVALIDATED" ? "text-muted-foreground" : z.zone_type === "SUPPORT" ? "text-bull" : "text-bear"}>
                {z.zone_type === "SUPPORT" ? "S" : "R"} {z.timeframe}
              </span>
              {z.status !== "ACTIVE" && <span className="rounded border border-border px-1 text-[10px] text-muted-foreground">{z.status.toLowerCase()}</span>}
            </span>
            <span className="tabular">
              {fmtPrice(z.lower)}–{fmtPrice(z.upper)}{" "}
              <span className="text-muted-foreground">· {z.tests} tests · {Math.round(z.strength)}</span>
            </span>
          </div>
        );
        if (!priceShown && price != null && z.upper < price) {
          priceShown = true;
          return [
            <div key="price" className="my-1 flex items-center gap-2 text-[11px] text-primary">
              <span className="h-px flex-1 bg-primary/40" /> price {fmtPrice(price)} <span className="h-px flex-1 bg-primary/40" />
            </div>,
            row,
          ];
        }
        return row;
      })}
      <p className="pt-1 text-[11px] text-muted-foreground">Hover a zone for how it formed. Strength (0–100) ranks zones; it is not a probability of a reaction.</p>
    </div>
  );
}
