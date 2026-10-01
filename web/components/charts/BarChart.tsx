/**
 * A horizontal bar chart, rendered as plain server-rendered SVG.
 *
 * Why no charting library: this task's allowed-file list does not include
 * package.json, so a new dependency is not available. A dependency-free SVG
 * server component is also strictly safer here - it cannot break `next build`'s
 * server-render pass, and there is no client/server bundling boundary for a
 * hydration mismatch to leak through. See Worker notes in tasks/f1-17.md.
 *
 * All geometry is computed from the data at render time, so there is no window
 * measurement and no client JS at all.
 */

export interface BarDatum {
  label: string;
  value: number;
  /** Optional secondary figure rendered after the value (e.g. a share). */
  suffix?: string;
}

export interface BarChartProps {
  data: BarDatum[];
  /** Accessible description of what the bars measure. */
  ariaLabel: string;
  /** Shown above the bars; keep it short. */
  valueLabel?: string;
  maxBars?: number;
}

const ROW_HEIGHT = 32;
const BAR_HEIGHT = 18;
const LABEL_WIDTH = 168;
const VALUE_WIDTH = 76;

function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

export function BarChart({
  data,
  ariaLabel,
  valueLabel,
  maxBars = 12,
}: BarChartProps) {
  const rows = data.slice(0, maxBars);
  const max = rows.reduce((acc, d) => Math.max(acc, d.value), 0);
  // A zero max would make every width NaN; with all-zero data there is nothing
  // to draw anyway, and the caller renders an empty state instead.
  const scale = max > 0 ? max : 1;
  const width = 640;
  const plotWidth = width - LABEL_WIDTH - VALUE_WIDTH;
  const height = rows.length * ROW_HEIGHT;

  return (
    <div>
      {valueLabel && (
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {valueLabel}
        </p>
      )}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-label={ariaLabel}
        preserveAspectRatio="xMinYMin meet"
      >
        {rows.map((row, i) => {
          const y = i * ROW_HEIGHT;
          const barWidth = Math.max(row.value > 0 ? 2 : 0, (row.value / scale) * plotWidth);
          return (
            <g key={`${row.label}-${i}`}>
              <text
                x={0}
                y={y + ROW_HEIGHT / 2}
                dominantBaseline="middle"
                className="fill-foreground text-[13px]"
              >
                {row.label.length > 24
                  ? `${row.label.slice(0, 23)}…`
                  : row.label}
              </text>
              <rect
                x={LABEL_WIDTH}
                y={y + (ROW_HEIGHT - BAR_HEIGHT) / 2}
                width={barWidth}
                height={BAR_HEIGHT}
                rx={3}
                className="fill-primary"
              />
              <text
                x={LABEL_WIDTH + barWidth + 8}
                y={y + ROW_HEIGHT / 2}
                dominantBaseline="middle"
                className="fill-muted-foreground text-[12px] tabular-nums"
              >
                {formatNumber(row.value)}
                {row.suffix ? ` ${row.suffix}` : ""}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
