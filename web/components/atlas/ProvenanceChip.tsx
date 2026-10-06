export type ProvenanceSource = "jsonld" | "source" | "rule" | "llm" | "manual" | "unknown";

const EXPLANATIONS: Record<ProvenanceSource, string> = {
  jsonld: "Read from the job page's own data",
  source: "From the job board's data fields",
  rule: "Worked out by a fixed rule",
  llm: "Guessed by an AI model",
  manual: "Entered by you",
  unknown: "Not stated",
};

type ProvenanceChipProps = {
  source: ProvenanceSource;
};

/**
 * A tiny outlined mono tag naming where a value came from. The `title` holds
 * the plain-words explanation. Non-interactive (`<span>`). Client-safe.
 */
export function ProvenanceChip({ source }: ProvenanceChipProps) {
  return (
    <span
      title={EXPLANATIONS[source]}
      style={{
        display: "inline-block",
        fontFamily: "var(--font-dm-mono)",
        fontSize: "var(--fs-1)",
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        lineHeight: 1.5,
        padding: "1px 6px",
        borderRadius: "var(--r-sm)",
        border: "1px solid var(--line)",
        color: "var(--fg-3)",
        background: "transparent",
      }}
    >
      {source}
    </span>
  );
}
