import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const nav = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({
    push: nav.push,
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
}));

import { CommandPalette } from "@/components/shell/CommandPalette";

// jsdom may not implement showModal/close: stub them so the palette works.
if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
}

beforeEach(() => {
  nav.push.mockClear();
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

function openPalette(props?: { onSignOut?: () => void }) {
  const onOpenChange = vi.fn();
  render(<CommandPalette open={false} onOpenChange={onOpenChange} onSignOut={props?.onSignOut} />);
  return onOpenChange;
}

describe("CommandPalette", () => {
  it("opens with Ctrl+K", () => {
    const onOpenChange = openPalette();
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });

  it("opens with Meta+K (Cmd+K)", () => {
    const onOpenChange = openPalette();
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });

  it("does not open on a bare k", () => {
    const onOpenChange = openPalette();
    fireEvent.keyDown(document, { key: "k" });
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("focuses the input on open and lists seven destinations plus two actions", () => {
    render(<CommandPalette open onOpenChange={() => {}} />);
    expect(screen.getByLabelText("Search pages and actions")).toHaveFocus();
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(9);
    for (const label of ["Today", "Jobs", "Tracker", "Inbox", "Companies", "Sources", "Profile", "Switch theme", "Sign out"]) {
      expect(screen.getByRole("option", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it("filters case-insensitively on label or description", () => {
    render(<CommandPalette open onOpenChange={() => {}} />);
    fireEvent.change(screen.getByLabelText("Search pages and actions"), { target: { value: "TRACK" } });
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("Tracker");
  });

  it("shows Nothing matches when the filter is empty", () => {
    render(<CommandPalette open onOpenChange={() => {}} />);
    fireEvent.change(screen.getByLabelText("Search pages and actions"), { target: { value: "zzz-no-such-page" } });
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByText("Nothing matches")).toBeInTheDocument();
  });

  it("wraps the highlight with ArrowUp and ArrowDown", () => {
    render(<CommandPalette open onOpenChange={() => {}} />);
    const input = screen.getByLabelText("Search pages and actions");
    const options = () => screen.getAllByRole("option");
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
    // Up from the first wraps to the last (Sign out).
    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(options()[8]).toHaveAttribute("aria-selected", "true");
    expect(options()[8]).toHaveTextContent("Sign out");
    // Down from the last wraps to the first (Today).
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
    expect(options()[0]).toHaveTextContent("Today");
  });

  it("runs the highlighted item on Enter, navigating to the right href", () => {
    const onOpenChange = vi.fn();
    render(<CommandPalette open onOpenChange={onOpenChange} />);
    const input = screen.getByLabelText("Search pages and actions");
    fireEvent.change(input, { target: { value: "tracker" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(nav.push).toHaveBeenCalledWith("/tracker");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("closes on Esc", () => {
    const onOpenChange = vi.fn();
    render(<CommandPalette open onOpenChange={onOpenChange} />);
    fireEvent.keyDown(screen.getByLabelText("Search pages and actions"), { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("closes on a click outside (on the dialog backdrop)", () => {
    const onOpenChange = vi.fn();
    const { container } = render(<CommandPalette open onOpenChange={onOpenChange} />);
    fireEvent.click(container.querySelector("dialog")!);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("restores focus to the element that opened it", () => {
    const onOpenChange = vi.fn();
    function Tree({ open }: { open: boolean }) {
      return (
        <>
          <button type="button">trigger</button>
          <CommandPalette open={open} onOpenChange={onOpenChange} />
        </>
      );
    }
    const { rerender } = render(<Tree open={false} />);
    const trigger = screen.getByRole("button", { name: "trigger" });
    trigger.focus();
    expect(trigger).toHaveFocus();
    rerender(<Tree open />);
    expect(screen.getByLabelText("Search pages and actions")).toHaveFocus();
    rerender(<Tree open={false} />);
    expect(screen.getByRole("button", { name: "trigger" })).toHaveFocus();
  });

  it("calls onSignOut for the Sign out action", () => {
    const onSignOut = vi.fn();
    const onOpenChange = vi.fn();
    render(<CommandPalette open onOpenChange={onOpenChange} onSignOut={onSignOut} />);
    const input = screen.getByLabelText("Search pages and actions");
    fireEvent.change(input, { target: { value: "sign out" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSignOut).toHaveBeenCalledTimes(1);
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("defaults onSignOut to a no-op that does not throw", () => {
    const onOpenChange = vi.fn();
    render(<CommandPalette open onOpenChange={onOpenChange} />);
    const input = screen.getByLabelText("Search pages and actions");
    fireEvent.change(input, { target: { value: "sign out" } });
    expect(() => fireEvent.keyDown(input, { key: "Enter" })).not.toThrow();
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("switches the theme through the palette action using nextTheme", () => {
    const onOpenChange = vi.fn();
    render(<CommandPalette open onOpenChange={onOpenChange} />);
    const input = screen.getByLabelText("Search pages and actions");
    fireEvent.change(input, { target: { value: "switch theme" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("theme")).toBe("dark");
  });
});
