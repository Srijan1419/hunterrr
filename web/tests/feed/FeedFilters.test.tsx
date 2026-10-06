import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const nav = vi.hoisted(() => ({ replace: vi.fn(), query: { current: "" } }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(nav.query.current),
}));

import { FeedFilters } from "@/components/feed/FeedFilters";

beforeEach(() => {
  nav.replace.mockClear();
  nav.query.current = "";
});

describe("FeedFilters", () => {
  it("has no Clear control while only the defaults are on (entry level, best match)", () => {
    render(<FeedFilters />);
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull();
    expect(screen.getByRole("button", { name: "Entry level" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows Clear once anything narrows the list, and Clear returns to the defaults", () => {
    nav.query.current = "remote=1&country=IN&q=data&sort=newest";
    render(<FeedFilters />);
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(nav.replace).toHaveBeenCalledWith("?", { scroll: false });
  });

  it("treats 'all levels' as a change worth clearing", () => {
    nav.query.current = "level=all";
    render(<FeedFilters />);
    expect(screen.getByRole("button", { name: "Entry level" }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeTruthy();
  });

  it("turning Entry level off writes level=all, turning it on removes it", () => {
    render(<FeedFilters />);
    fireEvent.click(screen.getByRole("button", { name: "Entry level" }));
    expect(nav.replace).toHaveBeenLastCalledWith("?level=all", { scroll: false });
  });
});
