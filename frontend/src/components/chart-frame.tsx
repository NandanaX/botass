"use client";

import { Maximize2, Minimize2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Chart container with a full-screen toggle. Uses the browser Fullscreen API when it is
 * available and falls back to a fixed full-window overlay. Esc (or the button) exits.
 * ``children`` receives ``full`` so the chart can fill the available height.
 */
export function ChartFrame({ toolbar, children, className }: {
  toolbar?: ReactNode;
  children: (full: boolean) => ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false);

  const exit = useCallback(() => {
    setFull(false);
    if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
  }, []);

  const enter = useCallback(() => {
    setFull(true);
    ref.current?.requestFullscreen?.().catch(() => undefined); // overlay still works if refused
  }, []);

  useEffect(() => {
    if (!full) return;
    const onChange = () => {
      if (!document.fullscreenElement) setFull(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") exit();
    };
    document.addEventListener("fullscreenchange", onChange);
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [full, exit]);

  return (
    <div
      ref={ref}
      className={cn(
        "flex flex-col",
        full ? "fixed inset-0 z-50 bg-background p-3" : "rounded-lg border bg-card p-2 shadow-sm",
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-3 px-2 pb-2 pt-1">
        <div className="min-w-0 flex-1">{toolbar}</div>
        <Button
          size="sm"
          variant="outline"
          onClick={full ? exit : enter}
          title={full ? "Exit full screen (Esc)" : "Full screen"}
          aria-label={full ? "Exit full screen" : "Full screen"}
        >
          {full ? <Minimize2 /> : <Maximize2 />}
          {full ? "Exit" : "Full screen"}
        </Button>
      </div>
      <div className={full ? "min-h-0 flex-1" : undefined}>{children(full)}</div>
    </div>
  );
}
