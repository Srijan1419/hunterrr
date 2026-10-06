import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const nav = vi.hoisted(() => ({ push: vi.fn(), path: { current: "/" } }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.path.current,
  useRouter: () => ({
    push: nav.push,
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
}));

import { TopBar } from "@/components/shell/TopBar";

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
  nav.path.current = "/";
});

function currentTabs() {
  return screen
    .getAllByRole("link")
    .filter((a) => a.closest("nav"))
    .map((a) => ({ name: a.textContent, current: a.getAttribute("aria-current") }));
}

const more = () => screen.getByRole("button", { name: /^More/ });

describe("TopBar", () => {
  it("marks the Tracker tab current on /tracker", () => {
    nav.path.current = "/tracker";
    render(<TopBar />);
    const tabs = currentTabs();
    expect(tabs.map((t) => t.name)).toEqual(["Today", "Jobs", "Tracker"]); // the rest sit under More
    expect(tabs.find((t) => t.name === "Tracker")).toMatchObject({ current: "page" });
    for (const t of tabs.filter((t) => t.name !== "Tracker")) {
      expect(t.current).toBeNull();
    }
  });

  it("marks Jobs current for a nested /jobs/123", () => {
    nav.path.current = "/jobs/123";
    render(<TopBar />);
    const tabs = currentTabs();
    expect(tabs.find((t) => t.name === "Jobs")).toMatchObject({ current: "page" });
    for (const t of tabs.filter((t) => t.name !== "Jobs")) {
      expect(t.current).toBeNull();
    }
  });

  it("keeps Companies, Sources and Profile under More, closed until opened", () => {
    render(<TopBar />);
    expect(more().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("link", { name: /Companies/ })).toBeNull();
    fireEvent.click(more());
    expect(more().getAttribute("aria-expanded")).toBe("true");
    for (const name of ["Companies", "Sources", "Profile"]) {
      expect(screen.getByRole("link", { name: new RegExp(name) })).toBeInTheDocument();
    }
  });

  it("closes More on Escape and on an outside click", () => {
    render(<TopBar />);
    fireEvent.click(more());
    fireEvent.keyDown(document, { key: "Escape" });
    expect(more().getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more());
    fireEvent.mouseDown(document.body);
    expect(more().getAttribute("aria-expanded")).toBe("false");
  });

  it("marks More current when the page is one of its destinations, and that item inside it", () => {
    nav.path.current = "/profile";
    render(<TopBar />);
    expect(more().getAttribute("aria-current")).toBe("page");
    fireEvent.click(more());
    expect(screen.getByRole("link", { name: /Profile/ }).getAttribute("aria-current")).toBe("page");
  });

  it("marks nothing current on /", () => {
    nav.path.current = "/";
    render(<TopBar />);
    for (const t of currentTabs()) {
      expect(t.current).toBeNull();
    }
  });

  it("renders the nav with an accessible name", () => {
    render(<TopBar />);
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("puts the skip link first in tab order and targets #main", () => {
    const { container } = render(<TopBar />);
    const header = container.querySelector("header")!;
    const focusable = Array.from(
      header.querySelectorAll('a[href], button:not([disabled]), input, [tabindex]:not([tabindex="-1"])')
    );
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveAttribute("href", "#main");
    expect(focusable[0]).toBe(skip);
    expect(header.firstElementChild).toBe(skip);
  });

  it("opens the palette from the search button and focuses the input", () => {
    render(<TopBar />);
    fireEvent.click(screen.getByRole("button", { name: "Search or jump to…" }));
    expect(screen.getByLabelText("Search pages and actions")).toHaveFocus();
  });

  it("returns focus to the search button when the palette closes", () => {
    render(<TopBar />);
    const trigger = screen.getByRole("button", { name: "Search or jump to…" });
    trigger.focus();
    fireEvent.click(trigger);
    const input = screen.getByLabelText("Search pages and actions");
    expect(input).toHaveFocus();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(trigger).toHaveFocus();
  });
});
