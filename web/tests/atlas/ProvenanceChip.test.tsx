import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProvenanceChip } from "@/components/atlas/ProvenanceChip";

const CASES = [
  ["jsonld", "Read from the job page's own data"],
  ["source", "From the job board's data fields"],
  ["rule", "Worked out by a fixed rule"],
  ["llm", "Guessed by an AI model"],
  ["manual", "Entered by you"],
  ["unknown", "Not stated"],
] as const;

describe("ProvenanceChip", () => {
  for (const [source, explanation] of CASES) {
    it(`explains "${source}" in plain words`, () => {
      render(<ProvenanceChip source={source} />);
      expect(screen.getByText(source)).toHaveAttribute("title", explanation);
    });
  }

  it("is not interactive", () => {
    const { container } = render(<ProvenanceChip source="llm" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect((container.firstElementChild as HTMLElement).tagName).toBe("SPAN");
  });
});
