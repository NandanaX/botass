"use client";

import { fmtTime, otherTime } from "@/lib/format";
import { useDisplayTz } from "@/lib/timezone";

/** A timestamp in the display time zone, with the other time (UTC, or local when showing UTC) on hover. */
export function Time({ iso, seconds = false, text, className }: { iso: string | null | undefined; seconds?: boolean; text?: string; className?: string }) {
  useDisplayTz();
  if (!iso) return <span className={className}>—</span>;
  return (
    <time dateTime={iso} title={otherTime(iso) ?? undefined} className={className}>
      {text ?? fmtTime(iso, seconds)}
    </time>
  );
}
