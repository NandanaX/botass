import { Badge } from "@/components/ui/badge";
import { fmtPrice } from "@/lib/format";
import type { Zone } from "@/lib/types";

/** Support or resistance as a letter, so the type doesn't rest on colour alone. */
export function ZoneTypeBadge({ type, muted = false }: { type: Zone["zone_type"]; muted?: boolean }) {
  const support = type === "SUPPORT";
  const name = support ? "Support" : "Resistance";
  return <Badge variant={muted ? "outline" : support ? "bull" : "bear"} className="px-1" title={name} aria-label={name}>{support ? "S" : "R"}</Badge>;
}

export function ZoneBadge({ zone }: { zone: Zone }) {
  const variant = zone.status === "INVALIDATED" ? "outline" : zone.zone_type === "SUPPORT" ? "bull" : "bear";
  return (
    <Badge variant={variant} title={zone.explanation}>
      {zone.zone_type === "SUPPORT" ? "Support" : "Resistance"} {zone.timeframe}
    </Badge>
  );
}

/** The nearest zone of a type that is still active. Zones arrive from the API nearest first. */
export function nearestActive(zones: Zone[] | undefined, type: Zone["zone_type"]): Zone | null {
  return zones?.find((z) => z.zone_type === type && z.status === "ACTIVE") ?? null;
}

export function ZoneRange({ zone, none = "none detected" }: { zone: Zone | null | undefined; none?: string }) {
  if (!zone) return <span className="text-muted-foreground">{none}</span>;
  return (
    <span className="tabular">
      {fmtPrice(zone.lower)} – {fmtPrice(zone.upper)}
    </span>
  );
}

/** Distance from price to the nearest edge of a zone, as a percentage of price (0 when price is inside it). */
export function zoneDistancePct(z: Pick<Zone, "lower" | "upper">, price: number): number {
  if (price >= z.lower && price <= z.upper) return 0;
  return ((price < z.lower ? z.lower - price : price - z.upper) / price) * 100;
}

function ZoneRow({ z, price, history }: { z: Zone; price?: number | null; history?: boolean }) {
  const d = price ? zoneDistancePct(z, price) : null;
  return (
    <div className="flex items-center justify-between gap-2" title={z.explanation} data-zone-status={z.status}>
      <span className="flex shrink-0 items-center gap-1.5">
        <ZoneTypeBadge type={z.zone_type} muted={history} />
        <span>{z.timeframe}</span>
        {z.status !== "ACTIVE" && <span className="rounded border border-border px-1 text-[10px] text-muted-foreground">{z.status.toLowerCase()}</span>}
      </span>
      <span className="text-right tabular">
        {fmtPrice(z.lower)}–{fmtPrice(z.upper)}{" "}
        <span className="text-muted-foreground">
          · {z.tests} tests · {Math.round(z.strength)}
          {d != null && <> · {d === 0 ? "inside" : `${d.toFixed(2)}%`}</>}
        </span>
      </span>
    </div>
  );
}

/** Active zones nearest first; broken and invalidated ones are kept in a collapsed history. */
export function ZoneList({ zones, price, empty = "No zones detected." }: { zones: Zone[]; price?: number | null; empty?: string }) {
  if (!zones.length) return <span className="text-xs text-muted-foreground">{empty}</span>;
  const byDistance = (a: Zone, b: Zone) => (price ? zoneDistancePct(a, price) - zoneDistancePct(b, price) : b.upper - a.upper);
  const active = zones.filter((z) => z.status === "ACTIVE").sort(byDistance);
  const history = zones.filter((z) => z.status !== "ACTIVE").sort(byDistance);
  return (
    <div className="space-y-1 text-xs" data-testid="zone-list">
      {price != null && <div className="mb-1 tabular text-muted-foreground" data-testid="zone-list-price">Live price {fmtPrice(price)}, nearest first</div>}
      {active.map((z) => <ZoneRow key={z.id + z.timeframe} z={z} price={price} />)}
      {active.length === 0 && <p className="text-muted-foreground">None active.</p>}
      {history.length > 0 && (
        <details className="pt-1">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">History ({history.length})</summary>
          <div className="mt-1 space-y-1 opacity-75">
            {history.map((z) => <ZoneRow key={z.id + z.timeframe} z={z} price={price} history />)}
            <p className="pt-1 text-[11px] text-muted-foreground">Broken zones have flipped role and the rules can still use them. Invalidated zones are ignored.</p>
          </div>
        </details>
      )}
      <p className="pt-1 text-[11px] text-muted-foreground">Hover a zone for how it formed. Strength (0–100) ranks zones; it is not a probability of a reaction.</p>
    </div>
  );
}
