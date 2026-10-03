import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppShell } from "@/components/shell/AppShell";

describe("AppShell", () => {
  it("wraps children in <main id=\"main\"> under the header", () => {
    const { container } = render(
      <AppShell>
        <p>page body</p>
      </AppShell>
    );
    expect(container.querySelector("header")).toBeInTheDocument();
    const main = container.querySelector("main#main");
    expect(main).toBeInTheDocument();
    expect(main).toHaveTextContent("page body");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });

  it("credits Remote OK with a followed link (no nofollow)", () => {
    const { container } = render(
      <AppShell>
        <p>page body</p>
      </AppShell>
    );
    const link = container.querySelector('footer a[href="https://remoteok.com"]');
    expect(link).not.toBeNull();
    expect(link!.getAttribute("rel")).not.toMatch(/nofollow/);
    expect(link!.getAttribute("rel")).toContain("noopener");
    expect(container.querySelector("footer")!.textContent).toContain(
      "Job data from"
    );
    expect(container.querySelector("footer")!.textContent).toContain("and public job boards.");
  });
});
