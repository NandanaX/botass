import type { IChartApi, LogicalRange } from "lightweight-charts";

const MIN_BARS = 10;

/** Visible range after zooming around ``anchor``. Scrolling up (negative delta) zooms in. */
export function zoomRange(range: { from: number; to: number }, anchor: number, deltaY: number): { from: number; to: number } {
  const k = Math.exp(Math.max(-200, Math.min(200, deltaY)) * 0.002);
  const from = anchor - (anchor - range.from) * k;
  const to = anchor + (range.to - anchor) * k;
  return to - from < MIN_BARS ? range : { from, to };
}

/**
 * The wheel scrolls the page unless the user asks to zoom: Ctrl/⌘ + wheel, or wheel after clicking
 * into the chart (until the pointer leaves it). The chart's built-in wheel handling must be off.
 */
export function attachWheelZoom(el: HTMLElement, chart: IChartApi): () => void {
  let engaged = false;
  const down = () => {
    engaged = true;
  };
  const leave = () => {
    engaged = false;
  };
  const wheel = (e: WheelEvent) => {
    if (!e.ctrlKey && !e.metaKey && !engaged) return;
    e.preventDefault();
    const scale = chart.timeScale();
    const range = scale.getVisibleLogicalRange();
    if (!range) return;
    const anchor = scale.coordinateToLogical(e.clientX - el.getBoundingClientRect().left) ?? (range.from + range.to) / 2;
    scale.setVisibleLogicalRange(zoomRange({ from: range.from, to: range.to }, anchor, e.deltaY) as unknown as LogicalRange);
  };
  el.addEventListener("pointerdown", down);
  el.addEventListener("pointerleave", leave);
  el.addEventListener("wheel", wheel, { passive: false });
  return () => {
    el.removeEventListener("pointerdown", down);
    el.removeEventListener("pointerleave", leave);
    el.removeEventListener("wheel", wheel);
  };
}
