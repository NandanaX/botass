import type { RuntimeSettings } from "./types";

type Json = unknown;

const isObject = (v: Json): v is Record<string, Json> => typeof v === "object" && v !== null && !Array.isArray(v);

export function getPath(root: Json, path: string): Json {
  return path.split(".").reduce<Json>((cur, key) => (cur == null ? undefined : (cur as Record<string, Json>)[key]), root);
}

/** A copy of ``root`` with one value replaced; the original isn't touched. */
export function setPath<T>(root: T, path: string, value: Json): T {
  const next = structuredClone(root);
  const keys = path.split(".");
  let cur = next as Record<string, Json>;
  for (const key of keys.slice(0, -1)) cur = cur[key] as Record<string, Json>;
  cur[keys[keys.length - 1]] = value;
  return next;
}

/** Same value? Lists of strings (watchlist, timeframes) compare as sets: ticking a box off and on is no change. */
export function sameValue(a: Json, b: Json): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    if (a.every((x) => typeof x === "string") && b.every((x) => typeof x === "string")) {
      return [...a].sort().join("|") === [...b].sort().join("|");
    }
    return a.every((x, k) => sameValue(x, b[k]));
  }
  if (isObject(a) && isObject(b)) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    return [...keys].every((k) => sameValue(a[k], b[k]));
  }
  return a === b;
}

/** Dotted paths of the individual settings that differ. A list counts as one setting per changed entry or as a whole. */
export function changedPaths(a: Json, b: Json, prefix = ""): string[] {
  if (isObject(a) && isObject(b)) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    return [...keys].flatMap((k) => changedPaths(a[k], b[k], prefix ? `${prefix}.${k}` : k));
  }
  if (Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x) => typeof x === "number")) {
    return a.flatMap((x, k) => (x === b[k] ? [] : [`${prefix}.${k}`]));
  }
  return sameValue(a, b) ? [] : [prefix];
}

export const TABS = [
  { id: "markets", label: "Markets & alerts" },
  { id: "strategy", label: "Strategy" },
  { id: "risk", label: "Stops & risk" },
  { id: "indicators", label: "Indicators" },
  { id: "decision", label: "Decision engine" },
  { id: "validation", label: "Validation" },
  { id: "analysis", label: "Technical analysis" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export interface RiskyConfig {
  path: string;
  tab: TabId;
  text: string;
}

/** Configurations that are allowed but deserve a second look. Hard errors live in ``settingsProblems``. */
export function riskyConfigurations(s: RuntimeSettings): RiskyConfig[] {
  const out: RiskyConfig[] = [];
  const add = (cond: boolean, path: string, tab: TabId, text: string) => {
    if (cond) out.push({ path, tab, text });
  };
  const st = s.strategy;
  add(s.alerts.min_score === 0, "alerts.min_score", "markets", "Minimum signal score is 0 — every qualified setup will be alerted");
  add(!s.alerts.telegram_enabled, "alerts.telegram_enabled", "markets", "Telegram alerts are off — no signal will reach you");
  add(s.decision.mode === "shadow", "decision.mode", "decision", "Decision engine in Shadow mode — nothing is blocked");
  add(s.decision.mode === "enforce", "decision.mode", "decision", "Decision engine in Enforce mode — alerts it doesn't qualify are blocked");
  add(st.breakout.enabled && st.breakout.min_rvol === 0, "strategy.breakout.min_rvol", "strategy", "Minimum relative volume is 0 — breakouts need no volume confirmation");
  add(st.breakout.enabled && st.breakout.max_extension_atr === 0, "strategy.breakout.max_extension_atr", "strategy", "Maximum extension is 0 — overextended breakouts aren't filtered");
  add(st.qualification.min_rr === 0, "strategy.qualification.min_rr", "risk", "Minimum room is 0 — setups can trigger right under an opposing zone");
  add(st.qualification.min_net_rr === 0, "strategy.qualification.min_net_rr", "risk", "Minimum net R:R is 0 — fees and slippage can consume the whole reward");
  add(!st.qualification.reject_outside_volatility, "strategy.qualification.reject_outside_volatility", "risk", "Volatility band is a warning only — setups outside it can still qualify");
  add(s.risk.risk_per_trade_pct > 2, "risk.risk_per_trade_pct", "risk", `Risking ${s.risk.risk_per_trade_pct}% per trade — a losing streak compounds quickly`);
  add(s.risk.leverage > 10, "risk.leverage", "risk", `Leverage of ${s.risk.leverage}x brings liquidation closer to the entry`);
  return out;
}

/** Errors that block saving; mirrors the backend's validation so problems show first. */
export function settingsProblems(s: RuntimeSettings): string[] {
  const out: string[] = [];
  const st = s.strategy;
  const alloc = st.targets.allocation_pct.reduce((a, b) => a + b, 0);
  if (Math.abs(alloc - 100) > 1e-6) out.push(`Target allocations sum to ${alloc}%, they must sum to 100%.`);
  const r = st.targets.r_multiples;
  if (r.some((x, k) => x <= 0 || (k > 0 && x <= r[k - 1]))) out.push("Take-profit R multiples must be positive and increasing.");
  if (s.risk.leverage > s.risk.max_leverage) out.push("Leverage is above the maximum leverage.");
  if (s.risk.risk_per_trade_pct > 10) out.push("Risk per trade is limited to 10%.");
  if (s.risk.risk_per_trade_pct > 5 && s.risk.leverage > 10) out.push("Unsafe combination: risk above 5% with leverage above 10x.");
  if (!st.breakout.enabled && !st.pullback.enabled) out.push("Enable at least one entry strategy.");
  if (st.stops.min_stop_atr >= st.stops.max_stop_atr) out.push("Minimum stop distance must be below the maximum.");
  return out;
}
