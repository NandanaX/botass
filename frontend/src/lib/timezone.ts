import { useSyncExternalStore } from "react";

/** The one display time zone. Stored per browser; timestamps themselves are always UTC. */
export const LOCAL_TZ = "local";
const KEY = "display-timezone";

const listeners = new Set<() => void>();
let choice: string | null = null;
let version = 0;

const valid = (tz: string) => {
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
};

export function browserTz(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/** "local" (follow the browser) or an IANA zone. */
export function tzChoice(): string {
  if (choice === null) {
    let saved: string | null = null;
    try {
      saved = typeof window === "undefined" ? null : window.localStorage.getItem(KEY);
    } catch {
      // Storage can be blocked; fall back to the browser's zone.
    }
    choice = saved && (saved === LOCAL_TZ || valid(saved)) ? saved : LOCAL_TZ;
  }
  return choice;
}

export function displayTz(): string {
  if (typeof window === "undefined") return "UTC"; // the server doesn't know the viewer's zone
  const c = tzChoice();
  return c === LOCAL_TZ ? browserTz() : c;
}

export function setTzChoice(next: string): void {
  if (next !== LOCAL_TZ && !valid(next)) return;
  choice = next;
  version += 1;
  try {
    window.localStorage.setItem(KEY, next);
  } catch {
    // Applies for this visit only.
  }
  listeners.forEach((fn) => fn());
}

const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
};

export function useDisplayTz(): string {
  return useSyncExternalStore(subscribe, displayTz, () => "UTC");
}

export function useTzChoice(): string {
  return useSyncExternalStore(subscribe, tzChoice, () => LOCAL_TZ);
}

/** Changes only when the user picks a zone, so views can redraw without remounting on page load. */
export function useTzVersion(): number {
  return useSyncExternalStore(subscribe, () => version, () => 0);
}

export function tzOffset(tz: string, at: Date = new Date()): string {
  const part = new Intl.DateTimeFormat("en-GB", { timeZone: tz, timeZoneName: "longOffset" }).formatToParts(at).find((p) => p.type === "timeZoneName")?.value ?? "GMT";
  return part === "GMT" ? "UTC+00:00" : part.replace("GMT", "UTC");
}

/** e.g. "Asia/Colombo (UTC+05:30)"; plain "UTC" for UTC. */
export function tzLabel(tz: string = displayTz()): string {
  return tz === "UTC" ? "UTC" : `${tz} (${tzOffset(tz)})`;
}

export function allTimeZones(): string[] {
  const all = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  return ["UTC", ...all.filter((z) => z !== "UTC")];
}
