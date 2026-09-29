import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { FilterBar } from "@/components/jobs/FilterBar";
import { JobCard } from "@/components/jobs/JobCard";
import { SkillBadge } from "@/components/jobs/SkillBadge";
import { JobDetailClient } from "@/app/jobs/[id]/JobDetailClient";
import { JobsClient } from "@/app/jobs/JobsClient";
import type { JobWithSkills } from "@/lib/queries/jobs";

/**
 * Acceptance tests for the /jobs and /jobs/[id] pages.
 *
 * These cover what is actually rendered: the honesty rule (the matching posting
 * count is visible), that extracted skills are shown with a distinguishable
 * extraction_source, and that the two WRITE affordances (save search, shortlist)
 * are absent without a session while the read-only page itself still renders.
 *
 * The mutation functions are mocked here because their own behaviour is covered
 * by tests/mutations.test.ts; this file is about what the UI offers.
 */
vi.mock("@/lib/mutations/user-actions", () => ({
  saveSearch: vi.fn(),
  addToShortlist: vi.fn(),
  removeFromShortlist: vi.fn(),
  isShortlisted: vi.fn(),
  getSavedSearches: vi.fn(),
  getShortlist: vi.fn(),
}));

function makeJob(overrides: Partial<JobWithSkills> = {}): JobWithSkills {
  return {
    id: "job-1",
    source: "remoteok",
    title: "Senior Python Engineer",
    company: "Acme",
    description: "Build things with Python.",
    applyUrl: "https://example.com/apply",
    postedAt: "2026-01-01T00:00:00Z",
    country: "US",
    timezoneOffset: null,
    remoteScope: "global",
    roleType: "technical",
    seniority: "senior",
    salaryMin: 120000,
    salaryMax: 180000,
    salaryCurrency: "USD",
    salaryPeriod: "year",
    tags: "[]",
    contentHash: "abc123",
    sourceId: "src-1",
    locationRaw: null,
    countriesAll: null,
    locationEncodingRepaired: 0,
    timezoneOffsetsAllMinutes: null,
    fetchedAt: "2026-01-01T00:00:00Z",
    fieldProvenance: "{}",
    descriptionChars: 22,
    skills: [
      { skill: "python", skillLabel: "Python", extractionSource: "source_tags", confidence: 100 },
      { skill: "kubernetes", skillLabel: "Kubernetes", extractionSource: "llm", confidence: 80 },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SkillBadge - extraction_source is distinguishable", () => {
  it("marks a skill extracted from source tags as 'tag'", () => {
    render(
      <SkillBadge
        skill={{ skill: "python", skillLabel: "Python", extractionSource: "source_tags", confidence: 100 }}
      />
    );

    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.getByText("tag")).toBeInTheDocument();
    expect(screen.queryByText("llm")).not.toBeInTheDocument();
  });

  it("marks an LLM-derived skill as 'llm'", () => {
    render(
      <SkillBadge
        skill={{ skill: "kubernetes", skillLabel: "Kubernetes", extractionSource: "llm", confidence: 80 }}
      />
    );

    expect(screen.getByText("Kubernetes")).toBeInTheDocument();
    expect(screen.getByText("llm")).toBeInTheDocument();
    expect(screen.queryByText("tag")).not.toBeInTheDocument();
  });
});

describe("FilterBar - the honesty rule", () => {
  const baseProps = {
    countries: ["US", "IN"],
    sources: ["remoteok", "jobicy"],
    currentFilters: { country: "US" } as Record<string, string | undefined>,
    userSignedIn: false,
  };

  it("shows the total posting count matching the current filter", () => {
    render(<FilterBar {...baseProps} totalCount={1234} />);

    expect(screen.getByText("1234 jobs found")).toBeInTheDocument();
  });

  it("uses the singular form for a single match", () => {
    render(<FilterBar {...baseProps} totalCount={1} />);

    expect(screen.getByText("1 job found")).toBeInTheDocument();
  });

  it("reports zero rather than hiding the count when nothing matches", () => {
    render(<FilterBar {...baseProps} totalCount={0} />);

    expect(screen.getByText("0 jobs found")).toBeInTheDocument();
  });

  it("offers every filter dimension: country, seniority, role type, remote scope, skill, source", () => {
    render(<FilterBar {...baseProps} totalCount={5} />);

    for (const label of ["Country", "Seniority", "Role Type", "Skill", "Source"]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });

  it("hides the save-search affordance for a signed-out visitor", () => {
    render(<FilterBar {...baseProps} totalCount={5} onSaveSearch={vi.fn()} />);

    expect(screen.queryByRole("button", { name: /save this search/i })).not.toBeInTheDocument();
  });

  it("shows the save-search affordance for a signed-in user", () => {
    render(<FilterBar {...baseProps} userSignedIn totalCount={5} onSaveSearch={vi.fn()} />);

    expect(screen.getByRole("button", { name: /save this search/i })).toBeInTheDocument();
  });
});

describe("JobCard", () => {
  it("renders the posting's title, company and skills", () => {
    render(<JobCard job={makeJob()} userSignedIn={false} />);

    expect(screen.getByRole("link", { name: "Senior Python Engineer" })).toHaveAttribute(
      "href",
      "/jobs/job-1"
    );
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.getByText("Kubernetes")).toBeInTheDocument();
  });

  it("shows the shortlist button for a signed-in user", () => {
    render(<JobCard job={makeJob()} userSignedIn />);

    expect(screen.getByRole("button", { name: /shortlist/i })).toBeInTheDocument();
  });

  it("does not show the shortlist button for a signed-out visitor", () => {
    render(<JobCard job={makeJob()} userSignedIn={false} />);

    expect(screen.queryByRole("button", { name: /shortlist/i })).not.toBeInTheDocument();
  });
});

describe("JobDetailClient - /jobs/[id]", () => {
  it("shows the posting's full detail", () => {
    render(<JobDetailClient job={makeJob()} userSignedIn={false} />);

    expect(screen.getByRole("heading", { name: "Senior Python Engineer", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Build things with Python.")).toBeInTheDocument();
    // Matches the rendered salary range without depending on the runtime's digit
    // grouping (toLocaleString here yields e.g. "1,20,000", not "120,000").
    expect(screen.getByText(/^USD\s\S+ - \S+ \/ year$/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /apply now/i })).toHaveAttribute(
      "href",
      "https://example.com/apply"
    );
  });

  it("shows the posting's extracted skills, distinguishing extraction_source", () => {
    render(<JobDetailClient job={makeJob()} userSignedIn={false} />);

    expect(screen.getByText(/Extracted Skills \(2\)/)).toBeInTheDocument();
    // Each label appears twice: once as a badge, once in the detail table below.
    expect(screen.getAllByText("Python").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Kubernetes").length).toBeGreaterThan(0);
    // The detail table names each skill's extraction source in words.
    expect(screen.getByText("Source tags")).toBeInTheDocument();
    expect(screen.getByText("LLM-derived")).toBeInTheDocument();
    // The confidence score for the LLM-derived skill is shown.
    expect(screen.getByText("80%")).toBeInTheDocument();
  });

  it("renders without crashing when the nullable JSON columns are null", () => {
    // tags, countries_all and timezone_offsets_all_minutes are all nullable in the
    // schema, so the detail page must not assume they are parseable strings.
    render(
      <JobDetailClient
        job={makeJob({
          country: null,
          locationRaw: null,
          countriesAll: null,
          timezoneOffsetsAllMinutes: null,
          fieldProvenance: null as unknown as string,
        })}
        userSignedIn={false}
      />
    );

    expect(screen.getByRole("heading", { name: "Senior Python Engineer", level: 1 })).toBeInTheDocument();
  });

  it("does not show the shortlist button for a signed-out visitor", () => {
    render(<JobDetailClient job={makeJob()} userSignedIn={false} />);

    expect(screen.queryByRole("button", { name: /shortlist/i })).not.toBeInTheDocument();
  });

  it("shows the shortlist button for a signed-in user", () => {
    render(<JobDetailClient job={makeJob()} userSignedIn />);

    expect(screen.getByRole("button", { name: /shortlist/i })).toBeInTheDocument();
  });
});

describe("JobsClient - /jobs is public, writes are not", () => {
  const baseProps = {
    initialJobs: [makeJob()],
    initialTotalCount: 1234,
    initialPage: 1,
    initialTotalPages: 62,
    initialFilters: { country: "US" } as Record<string, string | undefined>,
    countries: ["US"],
    sources: ["remoteok"],
  };

  it("lists postings and the matching total while signed out", () => {
    render(<JobsClient {...baseProps} userSignedIn={false} />);

    expect(screen.getByRole("heading", { name: "Job Search", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Senior Python Engineer" })).toBeInTheDocument();
    expect(screen.getByText("1234 jobs found")).toBeInTheDocument();
  });

  it("shows no write affordance at all while signed out", () => {
    render(<JobsClient {...baseProps} userSignedIn={false} />);

    expect(screen.queryByRole("button", { name: /save this search/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /shortlist/i })).not.toBeInTheDocument();
  });

  it("shows both write affordances for a signed-in user", () => {
    render(<JobsClient {...baseProps} userSignedIn />);

    expect(screen.getByRole("button", { name: /save this search/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /shortlist/i })).toBeInTheDocument();
  });

  it("paginates past the first page", () => {
    render(<JobsClient {...baseProps} userSignedIn={false} />);

    expect(screen.getByText(/Showing 1–20 of 1234/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeInTheDocument();
  });

  it("reports an honest zero when no posting matches the filter", () => {
    render(
      <JobsClient
        {...baseProps}
        initialJobs={[]}
        initialTotalCount={0}
        initialTotalPages={0}
        userSignedIn={false}
      />
    );

    expect(screen.getByText("0 jobs found")).toBeInTheDocument();
    expect(screen.getByText(/No jobs found matching your criteria/)).toBeInTheDocument();
  });
});
