import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FilterToggle } from "@/components/atlas/FilterToggle";

describe("FilterToggle", () => {
  it("is a real button with aria-pressed and the label as its accessible name", () => {
    render(<FilterToggle label="Remote only" pressed={false} onPressedChange={() => {}} />);
    const button = screen.getByRole("button", { name: "Remote only" });
    expect(button).toHaveAttribute("aria-pressed", "false");
  });

  it("reflects the pressed state", () => {
    render(<FilterToggle label="Remote only" pressed onPressedChange={() => {}} />);
    expect(screen.getByRole("button", { name: "Remote only" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("notifies on click and is keyboard operable", () => {
    const onPressedChange = vi.fn();
    render(<FilterToggle label="Remote only" pressed={false} onPressedChange={onPressedChange} />);
    const button = screen.getByRole("button", { name: "Remote only" });
    fireEvent.click(button);
    expect(onPressedChange).toHaveBeenCalledWith(true);
    button.focus();
    expect(document.activeElement).toBe(button);
  });
});
