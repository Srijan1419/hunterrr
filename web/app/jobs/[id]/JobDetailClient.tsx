"use client";

import { Button } from "@/components/ui/button";
import { SkillBadge } from "@/components/jobs/SkillBadge";
import { ShortlistButton } from "@/components/jobs/ShortlistButton";
import type { JobWithSkills } from "@/lib/queries/jobs";

interface JobDetailClientProps {
  job: JobWithSkills;
  userSignedIn: boolean;
}

export function JobDetailClient({ job, userSignedIn }: JobDetailClientProps) {
  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleDateString("en-US", {
        year: "numeric",
        month: "long",
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

  // These JSON-TEXT columns (tags, countries_all, timezone_offsets_all_minutes) are
  // nullable in the schema, and field_provenance may not parse - fall back to null
  // rather than assuming a non-null string.
  const parseJsonField = (field: string | null) => {
    if (!field) return null;
    try {
      return JSON.parse(field);
    } catch {
      return null;
    }
  };

  const tags = parseJsonField(job.tags);
  const countriesAll = parseJsonField(job.countriesAll);
  const timezoneOffsetsAllMinutes = parseJsonField(job.timezoneOffsetsAllMinutes);
  const fieldProvenance = parseJsonField(job.fieldProvenance);

  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      <div className="mb-6">
        <a
          href="/jobs"
          className="text-sm text-muted-foreground hover:underline inline-flex items-center gap-1"
        >
          ← Back to search
        </a>
      </div>

      <article className="space-y-6">
        <header className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="px-2 py-1 bg-primary/10 text-primary text-sm font-medium rounded">
              {job.source}
            </span>
            <time className="text-sm text-muted-foreground">
              Posted {formatDate(job.postedAt)}
            </time>
          </div>

          <h1 className="text-3xl font-bold tracking-tight">{job.title}</h1>

          <p className="text-xl text-muted-foreground">{job.company}</p>

          <div className="flex flex-wrap items-center gap-3 text-sm">
            {job.country && (
              <span className="px-3 py-1 bg-secondary rounded-full">
                {job.country}
              </span>
            )}
            <span className="px-3 py-1 bg-secondary rounded-full">
              {job.seniority.charAt(0).toUpperCase() + job.seniority.slice(1)}
            </span>
            <span className="px-3 py-1 bg-secondary rounded-full">
              {job.roleType.replace("_", " ")}
            </span>
            <span className="px-3 py-1 bg-secondary rounded-full">
              {job.remoteScope.replace("_", " ")}
            </span>
          </div>

          <p className="text-lg font-medium">{formatSalary()}</p>
        </header>

        <section className="border-t pt-6">
          <h2 className="text-xl font-semibold mb-3">Description</h2>
          <div className="prose prose-sm max-w-none text-muted-foreground whitespace-pre-wrap">
            {job.description}
          </div>
        </section>

        {job.skills.length > 0 && (
          <section className="border-t pt-6">
            <h2 className="text-xl font-semibold mb-3">
              Extracted Skills ({job.skills.length})
            </h2>
            <p className="text-sm text-muted-foreground mb-4">
              Skills are tagged with their extraction source:{" "}
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300 text-xs rounded">
                tag
              </span>{" "}
              = from source tags,{" "}
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300 text-xs rounded">
                llm
              </span>{" "}
              = LLM-derived. Confidence scores shown where available.
            </p>
            <div className="flex flex-wrap gap-2">
              {job.skills.map((skill) => (
                <SkillBadge key={`${skill.skill}-${skill.extractionSource}`} skill={skill} size="md" />
              ))}
            </div>
            <details className="mt-4">
              <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
                Show skill details table
              </summary>
              <table className="w-full mt-4 text-sm border-collapse">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4">Skill</th>
                    <th className="pb-2 pr-4">Label</th>
                    <th className="pb-2 pr-4">Source</th>
                    <th className="pb=2 pr-4">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {job.skills.map((skill) => (
                    <tr key={`${skill.skill}-${skill.extractionSource}`} className="border-b">
                      <td className="py-2 pr-4 font-mono">{skill.skill}</td>
                      <td className="py-2 pr-4">{skill.skillLabel}</td>
                      <td className="py-2 pr-4">
                        <span
                          className={skill.extractionSource === "source_tags"
                            ? "px-2 py-0.5 bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300 text-xs rounded"
                            : "px-2 py-0.5 bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300 text-xs rounded"}
                        >
                          {skill.extractionSource === "source_tags" ? "Source tags" : "LLM-derived"}
                        </span>
                      </td>
                      <td className="py-2 pr-4">{skill.confidence}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </section>
        )}

        <section className="border-t pt-6">
          <h2 className="text-xl font-semibold mb-3">Details</h2>
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-muted-foreground">Source ID</dt>
              <dd className="font-mono text-xs">{job.sourceId}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Content Hash</dt>
              <dd className="font-mono text-xs">{job.contentHash.slice(0, 16)}...</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Fetched At</dt>
              <dd>{formatDate(job.fetchedAt)}</dd>
            </div>
            {job.locationRaw && (
              <div>
                <dt className="text-muted-foreground">Location (raw)</dt>
                <dd>{job.locationRaw}</dd>
              </div>
            )}
            {countriesAll && countriesAll.length > 0 && (
              <div>
                <dt className="text-muted-foreground">All Countries</dt>
                <dd>{countriesAll.join(", ")}</dd>
              </div>
            )}
            {job.timezoneOffset !== null && (
              <div>
                <dt className="text-muted-foreground">Timezone Offset</dt>
                <dd>{job.timezoneOffset} minutes</dd>
              </div>
            )}
            {timezoneOffsetsAllMinutes && timezoneOffsetsAllMinutes.length > 0 && (
              <div>
                <dt className="text-muted-foreground">All Timezone Offsets</dt>
                <dd>{timezoneOffsetsAllMinutes.join(", ")} minutes</dd>
              </div>
            )}
            {job.locationEncodingRepaired > 0 && (
              <div>
                <dt className="text-muted-foreground">Encoding Repairs</dt>
                <dd>{job.locationEncodingRepaired}</dd>
              </div>
            )}
            <div>
              <dt className="text-muted-foreground">Description Length</dt>
              <dd>{job.descriptionChars} characters</dd>
            </div>
          </dl>

          {fieldProvenance && Object.keys(fieldProvenance).length > 0 && (
            <details className="mt-4">
              <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
                Show field provenance
              </summary>
              <pre className="mt-2 p-4 bg-muted rounded text-xs overflow-auto max-h-64">
                {JSON.stringify(fieldProvenance, null, 2)}
              </pre>
            </details>
          )}

          {tags && tags.length > 0 && (
            <div className="mt-4">
              <dt className="text-muted-foreground">Tags</dt>
              <dd className="flex flex-wrap gap-1 mt-1">
                {tags.map((tag: string) => (
                  <span key={tag} className="px-2 py-0.5 bg-secondary rounded text-xs">
                    {tag}
                  </span>
                ))}
              </dd>
            </div>
          )}
        </section>

        <footer className="border-t pt-6 flex flex-col sm:flex-row gap-4 items-start sm:items-center justify-between">
          <div className="flex gap-2">
            {/* Shortlisting is a write: the affordance is hidden entirely without a
                session, rather than shown and left to fail. */}
            {userSignedIn && <ShortlistButton jobId={job.id} />}
            <Button asChild variant="default" size="lg">
              <a href={job.applyUrl} target="_blank" rel="noopener noreferrer">
                Apply Now →
              </a>
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            Source: {job.source} | ID: {job.sourceId}
          </p>
        </footer>
      </article>
    </div>
  );
}