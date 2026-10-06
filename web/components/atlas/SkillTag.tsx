type SkillTagProps = {
  skill: string;
  matched?: boolean;
};

/**
 * A mono 11px skill label. Matched skills are filled with `--accent`;
 * unmatched ones are quiet. Non-interactive (`<span>`). Client-safe.
 */
export function SkillTag({ skill, matched = false }: SkillTagProps) {
  return (
    <span
      style={{
        display: "inline-block",
        fontFamily: "var(--font-dm-mono)",
        fontSize: "var(--fs-1)",
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        lineHeight: 1.5,
        padding: "2px 8px",
        borderRadius: "var(--r-sm)",
        background: matched ? "var(--accent)" : "var(--surface-2)",
        color: matched ? "var(--accent-fg)" : "var(--fg-2)",
        border: matched ? "1px solid transparent" : "1px solid var(--line)",
      }}
    >
      {skill}
    </span>
  );
}
