"use client";

import { cn } from "@/lib/utils";

interface SkillBadgeProps {
  skill: {
    skill: string;
    skillLabel: string;
    extractionSource: "source_tags" | "llm";
    confidence: number;
  };
  size?: "sm" | "md";
}

export function SkillBadge({ skill, size = "sm" }: SkillBadgeProps) {
  const isSourceTags = skill.extractionSource === "source_tags";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded font-medium transition-colors",
        size === "sm"
          ? "text-xs"
          : "text-sm",
        isSourceTags
          ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300"
          : "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300"
      )}
      title={
        `${skill.skillLabel} (${isSourceTags ? "source tags" : "LLM-derived"}, confidence: ${skill.confidence}%)`
      }
    >
      {skill.skillLabel}
      <span
        className={cn(
          "text-[10px] font-mono px-1 rounded",
          isSourceTags
            ? "bg-green-200 dark:bg-green-800"
            : "bg-blue-200 dark:bg-blue-800"
        )}
      >
        {isSourceTags ? "tag" : "llm"}
      </span>
    </span>
  );
}