import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { JobRow, initials } from "@/components/feed/JobRow";
import type { FeedRow } from "@/lib/queries/feed";

const base: FeedRow = {
  id: 1, title: "Backend Engineer", companyName: "Acme", source: "greenhouse", locations: [],
  remoteType: null, eligibilityScope: null, eligibleCountries: [], payMin: null, payMax: null,
  payCurrency: null, payPeriod: null, payProvenance: "unknown", postedAt: null, applyUrl: null, seniority: null,
};

describe("JobRow", () => {
  it("shows only what is known and says when there is no apply link", () => {
    render(<ul><JobRow row={base} /></ul>);
    expect(screen.getByRole("heading", { name: "Backend Engineer" })).toBeTruthy();
    expect(screen.getByText("No apply link")).toBeTruthy();
    expect(screen.queryByText("Remote")).toBeNull();
    expect(screen.queryByText(/Eligible|worldwide/)).toBeNull();
  });

  it("shows facts and a safe external apply link", () => {
    const row: FeedRow = {
      ...base, remoteType: "remote", eligibilityScope: "countries", eligibleCountries: ["IN"],
      payMin: 1_200_000, payMax: 1_800_000, payCurrency: "INR", payPeriod: "year",
      applyUrl: "https://boards.greenhouse.io/acme/jobs/1", postedAt: "2026-10-03T00:00:00Z",
      locations: [{ raw: "Pune, India", city: "Pune", region: null, country: "IN" }],
    };
    render(<ul><JobRow row={row} now={new Date("2026-10-03T12:00:00Z")} /></ul>);
    expect(screen.getByText("Remote")).toBeTruthy();
    expect(screen.getByText("Eligible: IN")).toBeTruthy();
    expect(screen.getByText("₹12–18 LPA")).toBeTruthy();
    expect(screen.getByText("Pune, IN")).toBeTruthy();
    const apply = screen.getByRole("link", { name: "Apply to Backend Engineer" });
    expect(apply.getAttribute("href")).toBe("https://boards.greenhouse.io/acme/jobs/1");
    expect(apply.getAttribute("rel")).toContain("noopener");
  });

  it("marks the company with its initials, hidden from screen readers (the name is in the text)", () => {
    const { container } = render(<ul><JobRow row={{ ...base, companyName: "Acme Corp" }} /></ul>);
    const mark = container.querySelector('[aria-hidden="true"]');
    expect(mark?.textContent).toBe("AC");
  });
});

describe("initials", () => {
  it("takes two letters, copes with one word, symbols and unknown companies", () => {
    expect(initials("Acme Corp")).toBe("AC");
    expect(initials("openai")).toBe("OP");
    expect(initials("  Zensar  Technologies Ltd ")).toBe("ZT");
    expect(initials("@#$")).toBe("?");
    expect(initials(null)).toBe("?");
  });
});
