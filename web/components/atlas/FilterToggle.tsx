"use client";

type FilterToggleProps = {
  label: string;
  pressed: boolean;
  onPressedChange: (pressed: boolean) => void;
};

const TRACK_WIDTH = 32;
const TRACK_HEIGHT = 18;
const THUMB = 14;
const PAD = 2;
const TRAVEL = TRACK_WIDTH - THUMB - PAD * 2; // 14px

/**
 * A labelled on/off switch built on a real `<button aria-pressed>`, so it is
 * keyboard operable (Tab + Enter/Space) with the label as its accessible name.
 * The thumb travels 14px in 150ms. Client-safe.
 */
export function FilterToggle({ label, pressed, onPressedChange }: FilterToggleProps) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={() => onPressedChange(!pressed)}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        background: "none",
        border: "none",
        padding: 4,
        font: "inherit",
        color: "var(--fg)",
        cursor: "pointer",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: TRACK_WIDTH,
          height: TRACK_HEIGHT,
          borderRadius: 999,
          background: pressed ? "var(--accent)" : "var(--surface-2)",
          border: "1px solid var(--line)",
          position: "relative",
          display: "inline-block",
          flexShrink: 0,
          transition: "background-color 150ms cubic-bezier(.2,.7,.2,1)",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            position: "absolute",
            top: PAD,
            left: PAD,
            width: THUMB,
            height: THUMB,
            borderRadius: "50%",
            background: "var(--surface)",
            border: "1px solid var(--line)",
            transform: pressed ? `translateX(${TRAVEL}px)` : "translateX(0)",
            transition: "transform 150ms cubic-bezier(.2,.7,.2,1)",
          }}
        />
      </span>
      <span>{label}</span>
    </button>
  );
}
