type StatTileProps = {
  value: string | number;
  label: string;
  tone?: "default" | "hot";
};

/**
 * A big display-font number plus a small label. The `hot` tone colours the
 * number `--hot` (for values that need attention). Client-safe.
 */
export function StatTile({ value, label, tone = "default" }: StatTileProps) {
  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: "var(--r-md)",
        padding: "12px 16px",
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-display)",
          fontWeight: 800,
          fontSize: "var(--fs-4)",
          lineHeight: 1.2,
          color: tone === "hot" ? "var(--hot)" : "var(--ink)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
      <div
        style={{
          fontSize: "var(--fs-1)",
          color: "var(--fg-2)",
          marginTop: 2,
        }}
      >
        {label}
      </div>
    </div>
  );
}
