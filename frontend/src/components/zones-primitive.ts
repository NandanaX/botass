import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  MouseEventParams,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

/** One shaded price band drawn from ``start`` to the right edge of the chart. */
export interface ZoneBand {
  lower: number;
  upper: number;
  start: number; // unix seconds; bands that start before the first bar begin at the left edge
  fill: string;
  border: string;
  labelColor: string;
  label: string;
  dashed: boolean;
  emphasis: boolean;
}

export const LABEL_HEIGHT = 13; // CSS pixels

/**
 * Which labels fit without overlapping, most important first: the nearest zones, then active ones,
 * then in the given order. Returns the kept indexes; the others appear on hover.
 */
export function visibleLabels(items: { y: number; emphasis: boolean; dashed: boolean }[], height = LABEL_HEIGHT): Set<number> {
  const order = items.map((_, k) => k).sort((a, b) =>
    Number(items[b].emphasis) - Number(items[a].emphasis) || Number(items[a].dashed) - Number(items[b].dashed) || a - b);
  const kept: number[] = [];
  for (const k of order) {
    if (kept.every((other) => Math.abs(items[other].y - items[k].y) >= height)) kept.push(k);
  }
  return new Set(kept);
}

function drawLabel(ctx: CanvasRenderingContext2D, text: string, color: string, right: number, y: number, hr: number, vr: number, minLeft: number, opaque: boolean) {
  const w = ctx.measureText(text).width;
  const x = Math.max(minLeft, right - w - 8 * hr);
  const h = (LABEL_HEIGHT - 1) * vr;
  ctx.fillStyle = opaque ? "#10141b" : "rgba(16,20,27,0.7)";
  ctx.fillRect(x - 3 * hr, y - h / 2, w + 6 * hr, h);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

class ZonesRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: ZonesPrimitive) {}

  draw(target: CanvasRenderingTarget2D) {
    const { chart, series, bands, firstTime } = this.source;
    if (!chart || !series || !bands.length) return;
    const ts = chart.timeScale();
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr, bitmapSize }) => {
      ctx.save();
      ctx.font = `${Math.round(10 * vr)}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textBaseline = "middle";
      const labels: { band: ZoneBand; left: number; y: number; emphasis: boolean; dashed: boolean }[] = [];
      for (const b of bands) {
        const y1 = series.priceToCoordinate(b.upper);
        const y2 = series.priceToCoordinate(b.lower);
        if (y1 === null || y2 === null) continue;
        let x = b.start <= firstTime ? 0 : ts.timeToCoordinate(b.start as UTCTimestamp);
        if (x === null) x = 0;
        const left = Math.max(0, Math.round(x * hr));
        const right = bitmapSize.width;
        if (left >= right) continue;
        const top = Math.round(Math.min(y1, y2) * vr);
        const height = Math.max(Math.round(Math.abs(y2 - y1) * vr), Math.round(2 * vr));
        ctx.fillStyle = b.fill;
        ctx.fillRect(left, top, right - left, height);
        ctx.strokeStyle = b.border;
        ctx.lineWidth = Math.max(1, Math.round((b.emphasis ? 1.5 : 1) * hr));
        ctx.setLineDash(b.dashed ? [4 * hr, 4 * hr] : []);
        ctx.strokeRect(left + 0.5, top + 0.5, right - left - 1, height - 1);
        if (b.label) labels.push({ band: b, left: left + 4 * hr, y: top + height / 2, emphasis: b.emphasis, dashed: b.dashed });
      }
      ctx.setLineDash([]);
      const keep = visibleLabels(labels.map((l) => ({ y: l.y / vr, emphasis: l.emphasis, dashed: l.dashed })));
      labels.forEach((l, k) => {
        if (keep.has(k)) drawLabel(ctx, l.band.label, l.band.labelColor, bitmapSize.width, l.y, hr, vr, l.left, false);
      });
      ctx.restore();
    });
  }
}

/** Labels of the bands under the crosshair, drawn above the candles and stacked so all of them can be read. */
class HoverRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: ZonesPrimitive) {}

  draw(target: CanvasRenderingTarget2D) {
    const { series, bands, hoverY } = this.source;
    if (!series || hoverY === null || !bands.length) return;
    const under = bands.filter((b) => {
      const y1 = series.priceToCoordinate(b.upper);
      const y2 = series.priceToCoordinate(b.lower);
      return b.label && y1 !== null && y2 !== null && hoverY >= Math.min(y1, y2) - 2 && hoverY <= Math.max(y1, y2) + 2;
    });
    if (!under.length) return;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr, bitmapSize }) => {
      ctx.save();
      ctx.font = `${Math.round(10 * vr)}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textBaseline = "middle";
      under.forEach((b, k) => {
        const y = (hoverY - 10 - k * LABEL_HEIGHT) * vr;
        drawLabel(ctx, b.label, b.labelColor, bitmapSize.width, y, hr, vr, 4 * hr, true);
      });
      ctx.restore();
    });
  }
}

class ZonesPaneView implements IPrimitivePaneView {
  private readonly r: ZonesRenderer;
  constructor(source: ZonesPrimitive) {
    this.r = new ZonesRenderer(source);
  }
  zOrder() {
    return "bottom" as const;
  }
  renderer() {
    return this.r;
  }
}

class HoverPaneView implements IPrimitivePaneView {
  private readonly r: HoverRenderer;
  constructor(source: ZonesPrimitive) {
    this.r = new HoverRenderer(source);
  }
  zOrder() {
    return "top" as const;
  }
  renderer() {
    return this.r;
  }
}

/** Series primitive that shades support/resistance zones behind the candles. */
export class ZonesPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null;
  series: ISeriesApi<"Candlestick"> | null = null;
  bands: ZoneBand[] = [];
  firstTime = 0;
  hoverY: number | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly views = [new ZonesPaneView(this), new HoverPaneView(this)];
  private readonly onMove = (p: MouseEventParams<Time>) => {
    const y = p.point ? p.point.y : null;
    if (y !== this.hoverY) {
      this.hoverY = y;
      this.requestUpdate?.();
    }
  };

  attached(param: SeriesAttachedParameter<Time>) {
    this.chart = param.chart as IChartApi;
    this.series = param.series as ISeriesApi<"Candlestick">;
    this.requestUpdate = param.requestUpdate;
    this.chart.subscribeCrosshairMove(this.onMove);
  }

  detached() {
    this.chart?.unsubscribeCrosshairMove(this.onMove);
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews() {
    return this.views;
  }

  setBands(bands: ZoneBand[], firstTime: number) {
    this.bands = bands;
    this.firstTime = firstTime;
    this.requestUpdate?.();
  }
}
