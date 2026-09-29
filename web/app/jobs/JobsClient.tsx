"use client";

import { FilterBar } from "@/components/jobs/FilterBar";
import { JobCard } from "@/components/jobs/JobCard";
import { Pagination } from "@/components/jobs/Pagination";
import { SaveSearchModal } from "@/components/jobs/SaveSearchModal";
import { useState } from "react";
import type { JobWithSkills } from "@/lib/queries/jobs";

interface JobsClientProps {
  initialJobs: JobWithSkills[];
  initialTotalCount: number;
  initialPage: number;
  initialTotalPages: number;
  initialFilters: Record<string, string | undefined>;
  countries: string[];
  sources: string[];
  userSignedIn: boolean;
}

export function JobsClient({
  initialJobs,
  initialTotalCount,
  initialPage,
  initialTotalPages,
  initialFilters,
  countries,
  sources,
  userSignedIn,
}: JobsClientProps) {
  const [saveSearchOpen, setSaveSearchOpen] = useState(false);

  const handleSaveSearch = () => {
    setSaveSearchOpen(true);
  };

  const handleSaveSearchSuccess = () => {
    setSaveSearchOpen(false);
  };

  const hasActiveFilters = Object.values(initialFilters).some(Boolean);

  return (
    <div className="container mx-auto px-4 py-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">Job Search</h1>
        <p className="text-muted-foreground mt-1">
          Search and filter remote job postings.{" "}
          {userSignedIn && "Save searches and shortlist favorites."}
        </p>
      </div>

      <FilterBar
        countries={countries}
        sources={sources}
        currentFilters={initialFilters}
        totalCount={initialTotalCount}
        onSaveSearch={handleSaveSearch}
        userSignedIn={userSignedIn}
      />

      <div id="jobs-results" className="mt-6">
        {initialJobs.length === 0 && (
          <div className="text-center py-12">
            <p className="text-muted-foreground text-lg">No jobs found matching your criteria.</p>
            {hasActiveFilters && (
              <a
                href="/jobs"
                className="mt-4 inline-block text-primary hover:underline"
              >
                Clear all filters
              </a>
            )}
          </div>
        )}

        {initialJobs.length > 0 && (
          <div className="space-y-4" role="list" aria-label="Job listings">
            {initialJobs.map((job) => (
              <JobCard key={job.id} job={job} userSignedIn={userSignedIn} />
            ))}
          </div>
        )}

        {initialTotalPages > 1 && (
          <Pagination
            currentPage={initialPage}
            totalPages={initialTotalPages}
            totalCount={initialTotalCount}
            pageSize={20}
          />
        )}
      </div>

      {/* Saving a search is a write: the modal is not mounted at all without a
          session. The "Save this search" button in FilterBar is gated the same way. */}
      {userSignedIn && (
        <SaveSearchModal
          isOpen={saveSearchOpen}
          onClose={() => setSaveSearchOpen(false)}
          filters={initialFilters}
          onSuccess={handleSaveSearchSuccess}
        />
      )}
    </div>
  );
}