import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MatchDial } from "@/components/atlas/MatchDial";

describe("MatchDial", () => {
  it("labels the score for assistive technology", () => {
    render(<MatchDial score={91} />);
    expect(screen.getByRole("img", { name: "Match score 91 out of 100" })).toBeInTheDocument();
  });

  it("defaults to size 54", () => {
    const { container } = render(<MatchDial score={50} />);
    expect(container.querySelector("svg")).toHaveAttribute("width", "54");
  });

  it("clamps out-of-range scores", () => {
    render(<MatchDial score={150} />);
    expect(screen.getByRole("img", { name: "Match score 100 out of 100" })).toBeInTheDocument();
    render(<MatchDial score={-3} />);
    expect(screen.getByRole("img", { name: "Match score 0 out of 100" })).toBeInTheDocument();
  });

  it("rounds non-integers for display", () => {
    render(<MatchDial score={91.6} />);
    expect(screen.getByRole("img", { name: "Match score 92 out of 100" })).toBeInTheDocument();
  });

  it("uses --hot for scores at or above 85 and --accent below", () => {
    const { container: hot } = render(<MatchDial score={85} />);
    const hotArc = hot.querySelectorAll("circle")[1];
    expect(hotArc.getAttribute("stroke")).toBe("var(--hot)");

    const { container: calm } = render(<MatchDial score={84} />);
    const calmArc = calm.querySelectorAll("circle")[1];
    expect(calmArc.getAttribute("stroke")).toBe("var(--accent)");
  });
});
