import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatTile } from "@/components/atlas/StatTile";

describe("StatTile", () => {
  it("renders the value and label", () => {
    render(<StatTile value={128} label="Matches" />);
    expect(screen.getByText("128")).toBeInTheDocument();
    expect(screen.getByText("Matches")).toBeInTheDocument();
  });

  it("colours the number --hot in the hot tone", () => {
    render(<StatTile value="3" label="Closing soon" tone="hot" />);
    expect(screen.getByText("3")).toHaveStyle({ color: "var(--hot)" });
  });

  it("uses the default tone without --hot", () => {
    render(<StatTile value="3" label="Closing soon" />);
    expect(screen.getByText("3")).not.toHaveStyle({ color: "var(--hot)" });
  });
});
