import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LegacyOnly, isLegacyPath } from "@/components/LegacyOnly";

const nav = vi.hoisted(() => ({ path: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.path }));

describe("LegacyOnly", () => {
  it("knows which paths are the legacy v1 pages", () => {
    for (const p of ["/", "/skills", "/skills/react", "/trends", "/coverage"]) {
      expect(isLegacyPath(p), p).toBe(true);
    }
    // v2 screens and auth pages must not get the old header and footer
    for (const p of ["/jobs", "/jobs/12", "/today", "/tracker", "/sources", "/signin", "/not-allowed", "/skillset"]) {
      expect(isLegacyPath(p), p).toBe(false);
    }
  });

  it("shows its children on a legacy page and hides them on v2 screens", () => {
    nav.path = "/trends";
    const { unmount } = render(<LegacyOnly><p>old header</p></LegacyOnly>);
    expect(screen.queryByText("old header")).not.toBeNull();
    unmount();

    nav.path = "/jobs";
    render(<LegacyOnly><p>old header</p></LegacyOnly>);
    expect(screen.queryByText("old header")).toBeNull();
  });
});
