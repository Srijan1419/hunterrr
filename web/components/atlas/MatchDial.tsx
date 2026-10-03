type MatchDialProps = {
  /** Match score from 0 to 100. Out-of-range values are clamped. */
  score: number;
  /** Width and height of the SVG in px. Defaults to 54. */
  size?: number;
};

/**
 * An SVG arc showing a match score. The track is `--surface-2`; the arc is
 * `--accent`, turning `--hot` at 85 and above. Client-safe: no data fetching.
 */
export function MatchDial({ score, size = 54 }: MatchDialProps) {
  const clamped = Math.min(100, Math.max(0, score));
  const display = Math.round(clamped);
  const hot = clamped >= 85;

  const strokeWidth = Math.max(4, size / 8);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = (clamped / 100) * circumference;

  return (
    <div
      role="img"
      aria-label={`Match score ${display} out of 100`}
      style={{ width: size, height: size, position: "relative", display: "inline-block" }}
    >
      <svg width={size} height={size} aria-hidden="true" focusable="false">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--surface-2)"
          strokeWidth={strokeWidth}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={hot ? "var(--hot)" : "var(--accent)"}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={`${filled} ${circumference}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <span
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "var(--font-display)",
          fontWeight: 700,
          fontSize: Math.max(11, Math.round(size * 0.28)),
          color: "var(--fg)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {display}
      </span>
    </div>
  );
}
