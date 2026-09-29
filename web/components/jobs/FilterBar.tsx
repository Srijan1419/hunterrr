"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  SENIORITY_VALUES,
  ROLE_TYPE_VALUES,
} from "@/lib/db/schema";

interface FilterBarProps {
  countries: string[];
  sources: string[];
  currentFilters: Record<string, string | undefined>;
  totalCount: number;
  onSaveSearch?: () => void;
  userSignedIn: boolean;
}

export function FilterBar({
  countries,
  sources,
  currentFilters,
  totalCount,
  onSaveSearch,
  userSignedIn,
}: FilterBarProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const updateFilter = (key: string, value: string | undefined) => {
    const params = new URLSearchParams(searchParams.toString());
    if (value) {
      params.set(key, value);
    } else {
      params.delete(key);
    }
    // Reset to page 1 when filters change
    params.delete("page");
    router.push(`/jobs?${params.toString()}`);
  };

  const clearAllFilters = () => {
    router.push("/jobs");
  };

  const hasActiveFilters = Object.values(currentFilters).some(Boolean);

  return (
    <div className="border rounded-lg p-4 bg-card">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold">Filters</h2>
          <span className="text-sm text-muted-foreground">
            {totalCount} {totalCount === 1 ? "job" : "jobs"} found
          </span>
        </div>
        {hasActiveFilters && (
          <Button variant="ghost" size="sm" onClick={clearAllFilters}>
            Clear all
          </Button>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3">
        <div>
          <label htmlFor="country" className="block text-xs font-medium text-muted-foreground mb-1">
            Country
          </label>
          <select
            id="country"
            value={currentFilters.country || ""}
            onChange={(e) => updateFilter("country", e.target.value || undefined)}
            className={cn(
              "w-full h-10 px-3 py-2 text-sm border border-input bg-background rounded-md",
              "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            )}
          >
            <option value="">All countries</option>
            {countries.map((country) => (
              <option key={country} value={country}>
                {country}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="seniority" className="block text-xs font-medium text-muted-foreground mb-1">
            Seniority
          </label>
          <select
            id="seniority"
            value={currentFilters.seniority || ""}
            onChange={(e) => updateFilter("seniority", e.target.value || undefined)}
            className={cn(
              "w-full h-10 px-3 py-2 text-sm border border-input bg-background rounded-md",
              "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            )}
          >
            <option value="">All levels</option>
            {SENIORITY_VALUES.map((s) => (
              <option key={s} value={s}>
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="roleType" className="block text-xs font-medium text-muted-foreground mb-1">
            Role Type
          </label>
          <select
            id="roleType"
            value={currentFilters.roleType || ""}
            onChange={(e) => updateFilter("roleType", e.target.value || undefined)}
            className={cn(
              "w-full h-10 px-3 py-2 text-sm border border-input bg-background rounded-md",
              "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            )}
          >
            <option value="">All types</option>
            {ROLE_TYPE_VALUES.map((r) => (
              <option key={r} value={r}>
                {r.replace("_", " ").replace(/\b\w/g, (c) => c.toUpperCase())}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="skill" className="block text-xs font-medium text-muted-foreground mb-1">
            Skill
          </label>
          <Input
            id="skill"
            type="text"
            placeholder="e.g. python, react..."
            value={currentFilters.skill || ""}
            onChange={(e) => updateFilter("skill", e.target.value || undefined)}
          />
        </div>

        <div>
          <label htmlFor="source" className="block text-xs font-medium text-muted-foreground mb-1">
            Source
          </label>
          <select
            id="source"
            value={currentFilters.source || ""}
            onChange={(e) => updateFilter("source", e.target.value || undefined)}
            className={cn(
              "w-full h-10 px-3 py-2 text-sm border border-input bg-background rounded-md",
              "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            )}
          >
            <option value="">All sources</option>
            {sources.map((source) => (
              <option key={source} value={source}>
                {source}
              </option>
            ))}
          </select>
        </div>
      </div>

      {userSignedIn && onSaveSearch && (
        <div className="mt-4 pt-4 border-t">
          <Button variant="outline" onClick={onSaveSearch} className="w-full sm:w-auto">
            Save this search
          </Button>
        </div>
      )}
    </div>
  );
}