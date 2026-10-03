import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScoreBar } from "@/components/atlas/ScoreBar";

describe("ScoreBar", () => {
  it("renders the label and points/max in mono", () => {
    const { container } = render(<ScoreBar label="Skills" points={7} max={10} />);
    expect(screen.getByText("Skills")).toBeInTheDocument();
    expect(screen.getByText("7/10")).toBeInTheDocument();
    const fill = container.querySelectorAll("span")[2] as HTMLElement;
    expect(fill.style.width).toBe("70%");
    expect(fill.style.background).toContain("var(--accent)");
    expect(fill.style.background).toContain("var(--hot)");
  });

  it("renders an empty bar without dividing by zero when max is 0", () => {
    const { container } = render(<ScoreBar label="Skills" points={5} max={0} />);
    expect(screen.getByText("5/0")).toBeInTheDocument();
    const fill = container.querySelectorAll("span")[2] as HTMLElement;
    expect(fill.style.width).toBe("0%");
  });

  it("clamps overflow to a full bar", () => {
    const { container } = render(<ScoreBar label="Skills" points={12} max={10} />);
    const fill = container.querySelectorAll("span")[2] as HTMLElement;
    expect(fill.style.width).toBe("100%");
  });
});
