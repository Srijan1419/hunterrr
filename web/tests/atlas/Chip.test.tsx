import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Chip } from "@/components/atlas/Chip";

describe("Chip", () => {
  it("renders children in every tone", () => {
    render(
      <>
        <Chip>plain</Chip>
        <Chip tone="ok">ok chip</Chip>
        <Chip tone="hot">hot chip</Chip>
      </>
    );
    expect(screen.getByText("plain")).toBeInTheDocument();
    expect(screen.getByText("ok chip")).toBeInTheDocument();
    expect(screen.getByText("hot chip")).toBeInTheDocument();
  });

  it("uses the accent pair for ok and the hot pair for hot", () => {
    render(
      <>
        <Chip tone="ok">ok chip</Chip>
        <Chip tone="hot">hot chip</Chip>
      </>
    );
    expect(screen.getByText("ok chip")).toHaveStyle({
      background: "var(--accent-soft)",
      color: "var(--accent)",
    });
    expect(screen.getByText("hot chip")).toHaveStyle({
      background: "var(--hot-soft)",
      color: "var(--hot)",
      "font-weight": "600",
    });
  });

  it("is not interactive: a span with no button role, pointer cursor or hover style", () => {
    const { container } = render(<Chip tone="hot">hot chip</Chip>);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    const el = container.firstElementChild as HTMLElement;
    expect(el.tagName).toBe("SPAN");
    expect(el.style.cursor).not.toBe("pointer");
  });
});
