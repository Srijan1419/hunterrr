"use client";

import { Button } from "@/components/ui/button";
import { SkillBadge } from "./SkillBadge";
import { ShortlistButton } from "./ShortlistButton";
import type { JobWithSkills } from "@/lib/queries/jobs";

interface JobCardProps {
  job: JobWithSkills;
  /** Whether a signed-in session exists. Gates the shortlist write affordance. */
  userSignedIn: boolean;
}

export function JobCard({ job, userSignedIn }: JobCardProps) {
  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return dateStr;
    }
  };

  const formatSalary = () => {
    if (job.salaryMin && job.salaryMax) {
      return `${job.salaryCurrency} ${job.salaryMin.toLocaleString()} - ${job.salaryMax.toLocaleString()} / ${job.salaryPeriod}`;
    }
    if (job.salaryMin) {
      return `${job.salaryCurrency} ${job.salaryMin.toLocaleString()}+ / ${job.salaryPeriod}`;
    }
    if (job.salaryMax) {
      return `Up to ${job.salaryCurrency} ${job.salaryMax.toLocaleString()} / ${job.salaryPeriod}`;
    }
    return "Salary not specified";
  };

  return (
    <article className="border rounded-lg p-6 bg-card hover:shadow-md transition-shadow">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className="text-sm font-medium text-muted-foreground">
              {job.source}
            </span>
            <span className="text-xs text-muted-foreground">
              Posted {formatDate(job.postedAt)}
            </span>
          </div>
          <h3 className="text-lg font-semibold text-foreground line-clamp-2">
            <a
              href={`/jobs/${job.id}`}
              className="hover:underline focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 rounded"
            >
              {job.title}
            </a>
          </h3>
          <p className="text-muted-foreground mt-1">{job.company}</p>
          <p className="text-sm text-muted-foreground mt-2 line-clamp-3">
            {job.description.slice(0, 200)}{job.description.length > 200 ? "..." : ""}
          </p>
          <div className="flex flex-wrap items-center gap-2 mt-3 text-sm text-muted-foreground">
            {job.country && (
              <span className="px-2 py-0.5 bg-secondary rounded">
                {job.country}
              </span>
            )}
            <span className="px-2 py-0.5 bg-secondary rounded">
              {job.seniority}
            </span>
            <span className="px-2 py-0.5 bg-secondary rounded">
              {job.roleType.replace("_", " ")}
            </span>
            <span className="px-2 py-0.5 bg-secondary rounded">
              {job.remoteScope.replace("_", " ")}
            </span>
          </div>
          {job.skills.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-3">
              {job.skills.slice(0, 5).map((skill) => (
                <SkillBadge key={`${skill.skill}-${skill.extractionSource}`} skill={skill} />
              ))}
              {job.skills.length > 5 && (
                <span className="px-2 py-0.5 text-xs text-muted-foreground bg-secondary rounded">
                  +{job.skills.length - 5} more
                </span>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-2 sm:ml-4">
          <p className="text-sm font-medium text-foreground">{formatSalary()}</p>
          <div className="flex gap-2">
            {/* Shortlisting is a write: hidden entirely without a session. */}
            {userSignedIn && <ShortlistButton jobId={job.id} />}
            <Button
              asChild
              variant="outline"
              size="sm"
              className="whitespace-nowrap"
            >
              <a href={job.applyUrl} target="_blank" rel="noopener noreferrer">
                Apply
              </a>
            </Button>
            <Button
              asChild
              variant="ghost"
              size="sm"
              className="whitespace-nowrap"
            >
              <a href={`/jobs/${job.id}`}>Details</a>
            </Button>
          </div>
        </div>
      </div>
    </article>
  );
}