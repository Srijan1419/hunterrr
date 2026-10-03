type ScoreBarProps = {
  label: string;
  points: number;
  max: number;
};

/**
 * A labelled score row: label, a bar filled with an `--accent`-to-`--hot`
 * gradient, and `points/max` in mono. The fill width is clamped to 0-100%;
 * `max = 0` renders an empty bar without dividing by zero. Client-safe.
 */
export function ScoreBar({ label, points, max }: ScoreBarProps) {
  const ratio = max <= 0 ? 0 : Math.min(1, Math.max(0, points / max));

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, width: "100%" }}>
      <span style={{ fontSize: 14, color: "var(--fg)", minWidth: 0 }}>{label}</span>
      <span
        role="presentation"
        style={{
          flex: 1,
          height: 8,
          borderRadius: 999,
          background: "var(--surface-2)",
          overflow: "hidden",
          display: "inline-block",
        }}
      >
        <span
          role="presentation"
          style={{
            display: "block",
            height: "100%",
            width: `${ratio * 100}%`,
            borderRadius: 999,
            background: "linear-gradient(to right, var(--accent), var(--hot))",
          }}
        />
      </span>
      <span
        style={{
          fontFamily: "var(--font-dm-mono)",
          fontSize: 12,
          color: "var(--fg-2)",
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
        }}
      >
        {points}/{max}
      </span>
    </div>
  );
}
