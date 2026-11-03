import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
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
  label: string;
  dashed: boolean;
  emphasis: boolean;
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
        if (b.label) {
          ctx.setLineDash([]);
          ctx.fillStyle = b.border;
          const w = ctx.measureText(b.label).width;
          ctx.fillText(b.label, Math.max(left + 4 * hr, right - w - 8 * hr), top + height / 2);
        }
      }
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

/** Series primitive that shades support/resistance zones behind the candles. */
export class ZonesPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null;
  series: ISeriesApi<"Candlestick"> | null = null;
  bands: ZoneBand[] = [];
  firstTime = 0;
  private requestUpdate: (() => void) | null = null;
  private readonly views = [new ZonesPaneView(this)];

  attached(param: SeriesAttachedParameter<Time>) {
    this.chart = param.chart as IChartApi;
    this.series = param.series as ISeriesApi<"Candlestick">;
    this.requestUpdate = param.requestUpdate;
  }

  detached() {
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
