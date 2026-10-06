import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SkillTag } from "@/components/atlas/SkillTag";

describe("SkillTag", () => {
  it("renders mono text at the smallest scale step (--fs-1, 13px)", () => {
    render(<SkillTag skill="python" />);
    const el = screen.getByText("python");
    expect(el).toHaveStyle({ "font-size": "var(--fs-1)" });
    expect(el.tagName).toBe("SPAN");
  });

  it("fills matched skills with --accent", () => {
    render(<SkillTag skill="python" matched />);
    expect(screen.getByText("python")).toHaveStyle({ background: "var(--accent)" });
  });

  it("leaves unmatched skills quiet", () => {
    render(<SkillTag skill="cobol" />);
    expect(screen.getByText("cobol")).not.toHaveStyle({ background: "var(--accent)" });
  });
});
