import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SOURCES_EMPTY, SourcesView } from "@/components/sources/SourcesView";
import type { SourcesOverview } from "@/lib/queries/sources";

const NOW = new Date("2026-10-04T12:00:00Z");
const empty: SourcesOverview = { totals: { boards: 0, openPostings: 0, neverPolled: 0, problemBoards: 0 }, byAts: [], problems: [], runs: [] };

const full: SourcesOverview = {
  totals: { boards: 62, openPostings: 6711, neverPolled: 0, problemBoards: 1 },
  byAts: [{ ats: "greenhouse", boards: 47, byStatus: { active: 45, quiet: 1, blocked: 0, dead: 1 }, postings: 6000, lastPolledAt: "2026-10-04T08:00:00Z" }],
  problems: [{ id: 1, ats: "greenhouse", slug: "oldco", companyName: "Old Co", status: "dead", consecutiveFailures: 6, lastPolledAt: "2026-09-20T00:00:00Z", lastOkAt: null }],
  runs: [
    { id: 2, workflow: "process", status: "ok", startedAt: "2026-10-04T08:10:00Z", finishedAt: null, counts: { written: 6711 }, errorSummary: "" },
    { id: 1, workflow: "collect", status: "degraded", startedAt: "2026-10-04T08:00:00Z", finishedAt: null, counts: {}, errorSummary: "2 errors" },
  ],
};

describe("SourcesView", () => {
  it("empty: the heading, the exact empty-state sentence and no tables", () => {
    const { container } = render(<SourcesView data={empty} now={NOW} />);
    expect(screen.getByRole("heading", { level: 1, name: "Sources" })).toBeInTheDocument();
    expect(screen.getByText(SOURCES_EMPTY)).toBeInTheDocument();
    expect(container.querySelector("table")).toBeNull();
  });

  it("shows tiles, a table by system, problem boards and recent runs", () => {
    render(<SourcesView data={full} now={NOW} />);
    expect(screen.getByText("62")).toBeInTheDocument();
    expect(screen.getByText("6,711", { selector: "div,span,p" })).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByRole("rowheader", { name: "Greenhouse" })).toBeInTheDocument();
    expect(within(table).getByText("1 / 0 / 1")).toBeInTheDocument();
    expect(screen.getByText("Old Co")).toBeInTheDocument();
    expect(screen.getByText("6 failed in a row · last polled 2w ago")).toBeInTheDocument();
    expect(screen.getByText("6,711 postings written")).toBeInTheDocument();
    expect(screen.getByText("2 errors")).toBeInTheDocument();
  });

  it("says so when every board is healthy and when no run exists", () => {
    render(<SourcesView data={{ ...full, problems: [], totals: { ...full.totals, problemBoards: 0 }, runs: [] }} now={NOW} />);
    expect(screen.getByText("Every board is polling normally.")).toBeInTheDocument();
    expect(screen.getByText("No run has been recorded yet.")).toBeInTheDocument();
  });
});
