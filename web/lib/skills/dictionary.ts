import dictionary from "@/lib/skills/dictionary.json";

/**
 * The canonical skills (config/skills.yaml, exported by `python -m etl.extract.rules.skills --export`): one id per
 * skill, so "JS", "javascript" and "Java Script" are the same thing in a profile and in a posting.
 */
export type SkillEntry = { id: string; label: string; family: string; terms: string[] };

export const SKILLS: SkillEntry[] = dictionary as SkillEntry[];

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").trim();
const BY_ID = new Map(SKILLS.map((s) => [s.id, s]));
const BY_TERM = new Map<string, string>();
for (const s of SKILLS) for (const t of s.terms) BY_TERM.set(t, s.id);

export function skillById(id: string): SkillEntry | undefined {
  return BY_ID.get(id);
}

export function skillLabel(id: string): string {
  return BY_ID.get(id)?.label ?? id;
}

/** The skill ids a free-text skill list names (each entry must be one known spelling; unknown entries are skipped). */
export function skillIdsFromText(items: readonly string[]): string[] {
  const ids = new Set<string>();
  for (const raw of items) {
    const id = BY_TERM.get(norm(raw)) ?? BY_ID.get(norm(raw))?.id;
    if (id) ids.add(id);
  }
  return [...ids];
}

/** Skills that every posting asks for ("communication", "teamwork"): they count, but far less than a tool or a trade. */
export function isGeneralSkill(id: string): boolean {
  return BY_ID.get(id)?.family === "general";
}

export const ROLE_FAMILIES: { id: string; label: string }[] = [
  { id: "software-engineering", label: "Software engineering" },
  { id: "data", label: "Data & analytics" },
  { id: "qa-testing", label: "QA & testing" },
  { id: "devops-cloud", label: "DevOps, cloud & security" },
  { id: "design", label: "Design" },
  { id: "product", label: "Product" },
  { id: "customer-support", label: "Customer support & success" },
  { id: "sales-bd", label: "Sales & business development" },
  { id: "marketing-content", label: "Marketing" },
  { id: "writing-editing", label: "Writing & editing" },
  { id: "community", label: "Community" },
  { id: "operations", label: "Operations & admin" },
  { id: "hr-recruiting", label: "HR & recruiting" },
  { id: "finance-accounting", label: "Finance & accounting" },
];
