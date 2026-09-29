import { Metadata } from "next";
import { auth } from "@/lib/auth/config";
import { headers } from "next/headers";
import { queryJobs } from "@/lib/queries/jobs";
import { db } from "@/lib/db/client";
import { jobs } from "@/lib/db/schema";
import { JobsClient } from "./JobsClient";

export const metadata: Metadata = {
  title: "Job Search | Hunterrr",
  description: "Search and filter remote job postings by country, seniority, role type, skill, and source.",
};

const PAGE_SIZE = 20;

async function getFilterOptions() {
  // selectDistinct already applies DISTINCT - no separate distinct() call needed.
  const [countriesResult, sourcesResult] = await Promise.all([
    db
      .selectDistinct({ country: jobs.country })
      .from(jobs),
    db
      .selectDistinct({ source: jobs.source })
      .from(jobs),
  ]);

  const countries = countriesResult
    .map((r) => r.country)
    .filter((c): c is string => c !== null)
    .sort();
  const sources = sourcesResult.map((r) => r.source).sort();

  return { countries, sources };
}

export default async function JobsPage({
  searchParams,
}: {
  searchParams: Promise<{
    country?: string;
    seniority?: string;
    roleType?: string;
    skill?: string;
    source?: string;
    page?: string;
  }>;
}) {
  const session = await auth.api.getSession({
    headers: await headers(),
  });

  const resolvedSearchParams = await searchParams;
  const page = Math.max(1, parseInt(resolvedSearchParams.page || "1", 10));
  const offset = (page - 1) * PAGE_SIZE;

  const filters = {
    country: resolvedSearchParams.country,
    seniority: resolvedSearchParams.seniority as
      | "entry"
      | "mid"
      | "senior"
      | "lead"
      | "executive"
      | "unknown"
      | undefined,
    roleType: resolvedSearchParams.roleType as
      | "technical"
      | "non_technical"
      | "mixed"
      | "unknown"
      | undefined,
    skill: resolvedSearchParams.skill,
    source: resolvedSearchParams.source,
  };

  const { countries, sources } = await getFilterOptions();
  const { jobs: jobsList, totalCount } = await queryJobs(filters, {
    limit: PAGE_SIZE,
    offset,
  });

  const totalPages = Math.ceil(totalCount / PAGE_SIZE);

  return (
    <JobsClient
      initialJobs={jobsList}
      initialTotalCount={totalCount}
      initialPage={page}
      initialTotalPages={totalPages}
      initialFilters={filters}
      countries={countries}
      sources={sources}
      userSignedIn={!!session}
    />
  );
}