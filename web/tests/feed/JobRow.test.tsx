import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { JobRow, initials } from "@/components/feed/JobRow";
import type { FeedRow } from "@/lib/queries/feed";

const base: FeedRow = {
  id: 1, title: "Backend Engineer", companyName: "Acme", source: "greenhouse", locations: [],
  remoteType: null, eligibilityScope: null, eligibleCountries: [], payMin: null, payMax: null,
  payCurrency: null, payPeriod: null, payProvenance: "unknown", postedAt: null, applyUrl: null, seniority: null,
  descriptionSnippet: "", experienceMin: null, experienceMax: null, indiaReason: null, labels: [],
};

describe("JobRow reasons", () => {
  it("says why an Indian can take the job and shows soft labels", () => {
    render(<ul><JobRow row={{ ...base, indiaReason: "Names India", labels: ["night_shift", "lang_nice:french"] }} /></ul>);
    expect(screen.getByText("Open to India: Names India")).toBeTruthy();
    expect(screen.getByText("US-hours overlap")).toBeTruthy();
    expect(screen.getByText("French is a plus")).toBeTruthy();
  });
  it("shows no reason or label chips for an undecided posting", () => {
    render(<ul><JobRow row={base} /></ul>);
    expect(screen.queryByText(/Open to India/)).toBeNull();
  });
});

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

describe("JobRow fit breakdown", () => {
  it("shows the score dial and why, only when the row was scored", () => {
    const { rerender } = render(<ul><JobRow row={base} /></ul>);
    expect(screen.queryByText("Why this score")).toBeNull();
    const match = {
      score: 82, blocked: null, flags: ["Level not stated"],
      parts: [{ key: "skills", label: "Skills", points: 30, max: 40, note: "Names SQL, Python" }],
    };
    rerender(<ul><JobRow row={{ ...base, match }} /></ul>);
    expect(screen.getByRole("img", { name: "Match score 82 out of 100" })).toBeTruthy();
    expect(screen.getByText("Why this score")).toBeTruthy();
    expect(screen.getByText("Names SQL, Python")).toBeTruthy();
    expect(screen.getByText(/Not counted \(not stated\): Level not stated/)).toBeTruthy();
  });

  it("says plainly why a blocked job is held back", () => {
    const match = { score: 20, blocked: "Only open to US", flags: [], parts: [] };
    render(<ul><JobRow row={{ ...base, match }} /></ul>);
    expect(screen.getByText("Only open to US")).toBeTruthy();
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


describe("JobRow attribution", () => {
  it("names an aggregator source next to the job", () => {
    render(<ul><JobRow row={{ ...base, source: "himalayas" }} /></ul>);
    expect(screen.getByText("via Himalayas")).toBeTruthy();
  });
  it("names no source for a company board", () => {
    render(<ul><JobRow row={base} /></ul>);
    expect(screen.queryByText(/^via /)).toBeNull();
  });
});
