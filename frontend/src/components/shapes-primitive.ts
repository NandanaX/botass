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

/** Line segment between two (time, price) points; ``extend`` continues it to the right edge. */
export interface Segment {
  kind: "segment";
  t1: number;
  p1: number;
  t2: number;
  p2: number;
  color: string;
  width?: number;
  dashed?: boolean;
  extend?: boolean;
  label?: string;
}

/** Price band from ``t1`` to ``t2`` (or the right edge when ``t2`` is null). */
export interface Box {
  kind: "box";
  t1: number;
  t2: number | null;
  lower: number;
  upper: number;
  fill: string;
  border: string;
  label?: string;
}

export type Shape = Segment | Box;

class ShapesRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly src: ShapesPrimitive) {}

  draw(target: CanvasRenderingTarget2D) {
    const { chart, series, shapes, firstTime } = this.src;
    if (!chart || !series || !shapes.length) return;
    const ts = chart.timeScale();
    const x = (t: number): number | null => (t <= firstTime ? 0 : ts.timeToCoordinate(t as UTCTimestamp));
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr, bitmapSize }) => {
      ctx.save();
      ctx.font = `${Math.round(10 * vr)}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textBaseline = "bottom";
      for (const s of shapes) {
        if (s.kind === "box") {
          const xa = x(s.t1);
          const xb = s.t2 == null ? bitmapSize.width / hr : x(s.t2);
          const ya = series.priceToCoordinate(s.upper);
          const yb = series.priceToCoordinate(s.lower);
          if (xa == null || xb == null || ya == null || yb == null) continue;
          const left = Math.round(xa * hr);
          const w = Math.max(1, Math.round((xb - xa) * hr));
          const top = Math.round(Math.min(ya, yb) * vr);
          const h = Math.max(1, Math.round(Math.abs(yb - ya) * vr));
          ctx.fillStyle = s.fill;
          ctx.fillRect(left, top, w, h);
          ctx.strokeStyle = s.border;
          ctx.lineWidth = Math.max(1, hr);
          ctx.setLineDash([3 * hr, 3 * hr]);
          ctx.strokeRect(left + 0.5, top + 0.5, w - 1, h - 1);
          if (s.label) {
            ctx.setLineDash([]);
            ctx.fillStyle = s.border;
            ctx.fillText(s.label, left + 3 * hr, top - 1);
          }
          continue;
        }
        const xa = x(s.t1);
        const xb = x(s.t2);
        const ya = series.priceToCoordinate(s.p1);
        const yb = series.priceToCoordinate(s.p2);
        if (xa == null || xb == null || ya == null || yb == null) continue;
        let x2: number = xb;
        let y2: number = yb;
        if (s.extend && xb !== xa) {
          const right = bitmapSize.width / hr;
          y2 = ya + ((yb - ya) * (right - xa)) / (xb - xa);
          x2 = right;
        }
        ctx.strokeStyle = s.color;
        ctx.lineWidth = Math.max(1, Math.round((s.width ?? 1) * hr));
        ctx.setLineDash(s.dashed ? [5 * hr, 4 * hr] : []);
        ctx.beginPath();
        ctx.moveTo(xa * hr, ya * vr);
        ctx.lineTo(x2 * hr, y2 * vr);
        ctx.stroke();
        if (s.label) {
          ctx.setLineDash([]);
          ctx.fillStyle = s.color;
          const w = ctx.measureText(s.label).width;
          ctx.fillText(s.label, Math.min(x2 * hr - w - 4 * hr, bitmapSize.width - w - 60 * hr), y2 * vr - 2 * vr);
        }
      }
      ctx.restore();
    });
  }
}

class ShapesView implements IPrimitivePaneView {
  private readonly r: ShapesRenderer;
  constructor(src: ShapesPrimitive) {
    this.r = new ShapesRenderer(src);
  }
  zOrder() {
    return "normal" as const;
  }
  renderer() {
    return this.r;
  }
}

/** Series primitive drawing analysis annotations (pattern lines, trendlines, Fibonacci, gaps). */
export class ShapesPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null;
  series: ISeriesApi<"Candlestick"> | null = null;
  shapes: Shape[] = [];
  firstTime = 0;
  private requestUpdate: (() => void) | null = null;
  private readonly views = [new ShapesView(this)];

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

  setShapes(shapes: Shape[], firstTime: number) {
    this.shapes = shapes;
    this.firstTime = firstTime;
    this.requestUpdate?.();
  }
}
