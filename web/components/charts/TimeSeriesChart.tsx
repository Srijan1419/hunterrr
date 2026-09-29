/**
 * A time-series line chart, rendered as plain server-rendered SVG.
 *
 * Same reasoning as BarChart: no charting library (not available in this task's
 * allowed files), no client JS, no hydration boundary. Everything below is
 * computed from the series at render time.
 *
 * Days with zero postings are gaps in the data, not zeroes to plot. The line
 * therefore breaks at a gap instead of drawing a straight run through it -
 * interpolating across a day with no postings would overstate the trend, which
 * is exactly what the ADR's honesty rules are against.
 */

export interface TimePoint {
  /** YYYY-MM-DD */
  day: string;
  value: number;
}

export interface TimeSeriesChartProps {
  points: TimePoint[];
  ariaLabel: string;
  valueLabel?: string;
  height?: number;
}

const WIDTH = 640;
const PADDING = { top: 12, right: 12, bottom: 28, left: 44 };

function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

/** Short axis label from a YYYY-MM-DD day. */
function shortDay(day: string): string {
  return day.length >= 10 ? day.slice(5) : day;
}

export function TimeSeriesChart({
  points,
  ariaLabel,
  valueLabel,
  height = 220,
}: TimeSeriesChartProps) {
  const max = points.reduce((acc, p) => Math.max(acc, p.value), 0);
  const scaleMax = max > 0 ? max : 1;
  const innerWidth = WIDTH - PADDING.left - PADDING.right;
  const innerHeight = height - PADDING.top - PADDING.bottom;
  const stepX = points.length > 1 ? innerWidth / (points.length - 1) : 0;

  const x = (i: number) => PADDING.left + i * stepX;
  const y = (v: number) => PADDING.top + innerHeight - (v / scaleMax) * innerHeight;

  // Break the path at any zero-value day: that day has no postings, so there is
  // no point to interpolate through.
  const segments: string[] = [];
  let current: string[] = [];
  points.forEach((p, i) => {
    if (p.value <= 0) {
      if (current.length > 0) segments.push(current.join(" "));
      current = [];
      return;
    }
    current.push(`${current.length === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(p.value).toFixed(2)}`);
  });
  if (current.length > 0) segments.push(current.join(" "));

  // Label at most 5 x-axis ticks so the axis stays readable.
  const tickCount = Math.min(5, points.length);
  const tickIndices =
    tickCount <= 1
      ? [0]
      : Array.from({ length: tickCount }, (_, i) =>
          Math.round((i * (points.length - 1)) / (tickCount - 1))
        );

  return (
    <div>
      {valueLabel && (
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {valueLabel}
        </p>
      )}
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-label={ariaLabel}
        preserveAspectRatio="xMinYMin meet"
      >
        {/* baseline + top gridline with value labels */}
        <line
          x1={PADDING.left}
          y1={PADDING.top + innerHeight}
          x2={WIDTH - PADDING.right}
          y2={PADDING.top + innerHeight}
          className="stroke-foreground/25"
          strokeWidth={1}
        />
        <line
          x1={PADDING.left}
          y1={PADDING.top}
          x2={WIDTH - PADDING.right}
          y2={PADDING.top}
          className="stroke-foreground/15"
          strokeWidth={1}
          strokeDasharray="3 3"
        />
        <text
          x={PADDING.left - 8}
          y={PADDING.top}
          textAnchor="end"
          dominantBaseline="middle"
          className="fill-muted-foreground text-[11px] tabular-nums"
        >
          {formatNumber(scaleMax)}
        </text>
        <text
          x={PADDING.left - 8}
          y={PADDING.top + innerHeight}
          textAnchor="end"
          dominantBaseline="middle"
          className="fill-muted-foreground text-[11px] tabular-nums"
        >
          0
        </text>

        {segments.map((d, i) => (
          <path
            key={i}
            d={d}
            fill="none"
            className="stroke-foreground"
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}

        {points.map((p, i) =>
          p.value > 0 ? (
            <circle
              key={p.day}
              cx={x(i)}
              cy={y(p.value)}
              r={2.5}
              className="fill-foreground"
            />
          ) : null
        )}

        {tickIndices.map((i) => (
          <text
            key={`tick-${i}`}
            x={x(i)}
            y={height - 8}
            textAnchor={i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}
            className="fill-muted-foreground text-[11px]"
          >
            {shortDay(points[i].day)}
          </text>
        ))}
      </svg>
    </div>
  );
}
