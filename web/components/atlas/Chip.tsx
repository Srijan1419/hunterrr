import type { ReactNode } from "react";

type ChipProps = {
  tone?: "default" | "ok" | "hot";
  children: ReactNode;
};

/**
 * A small non-interactive pill. Renders a `<span>`: no hover style, no pointer
 * cursor, no button role. `ok` uses the accent pair, `hot` the tangerine pair.
 */
export function Chip({ tone = "default", children }: ChipProps) {
  const palette =
    tone === "ok"
      ? { background: "var(--accent-soft)", color: "var(--accent)", fontWeight: 500 }
      : tone === "hot"
        ? { background: "var(--hot-soft)", color: "var(--hot)", fontWeight: 600 }
        : { background: "var(--surface-2)", color: "var(--fg-2)", fontWeight: 500 };

  return (
    <span
      style={{
        display: "inline-block",
        fontSize: 12,
        lineHeight: 1.5,
        padding: "2px 10px",
        borderRadius: 999,
        ...palette,
      }}
    >
      {children}
    </span>
  );
}
