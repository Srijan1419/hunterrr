import { DEFAULT_COUNTRY, feedWhere, queryScored, type FeedDb } from "@/lib/queries/feed";
import type { Profile } from "@/lib/profile/schema";
import { isGeneralSkill, skillIdsFromText, skillLabel } from "@/lib/skills/dictionary";

export type SkillGap = {
  /** Strong-fit and worth-a-shot jobs the gap is counted over. */
  considered: number;
  /** The must-have skills you lack that block the most of those jobs, most common first. */
  gaps: { skill: string; label: string; jobs: number }[];
};

/**
 * "Missing in 34 of your 80 matches: SQL": across the jobs that fit you (strong or worth a shot), which must-have
 * skills do you not list? It turns "keep learning everything" into "learn the one thing the market asks for".
 * Everyday skills (communication) are never reported: they are not what blocks a fresher.
 */
export async function querySkillGap(db: FeedDb, profile: Profile, limit = 5): Promise<SkillGap> {
  const rows = await queryScored(db, feedWhere({ remote: true, country: DEFAULT_COUNTRY, entryLevel: true }), profile);
  const mine = new Set(skillIdsFromText(profile.skills));
  const fit = rows.filter((r) => r.match?.bucket === "strong" || r.match?.bucket === "worth");
  const counts = new Map<string, number>();
  for (const r of fit) {
    for (const k of new Set(r.skills.filter((x) => x.importance === "must" && !isGeneralSkill(x.skill)).map((x) => x.skill))) {
      if (!mine.has(k)) counts.set(k, (counts.get(k) ?? 0) + 1);
    }
  }
  const gaps = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([skill, jobs]) => ({ skill, label: skillLabel(skill), jobs }));
  return { considered: fit.length, gaps };
}
