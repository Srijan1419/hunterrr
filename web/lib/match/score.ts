import type { Profile } from "@/lib/profile/schema";
import { ROLE_FAMILIES, isGeneralSkill, skillIdsFromText, skillLabel } from "@/lib/skills/dictionary";

/**
 * Fit score (0-100) of one posting for the owner's profile, with a breakdown a person can read.
 *
 * Honesty rules (roadmap h2-41): a part only counts when the posting gives evidence for it. A part
 * the posting does not speak to (or the profile leaves empty) is excluded and listed under `flags`;
 * it is never guessed and never boosts the score. The total is normalised over the parts that
 * could be judged, so a posting that states little is not punished or flattered for the rest.
 */

export type MatchInput = {
  title: string;
  /** The start of the description (about 4,000 characters is plenty to find skills). */
  description: string;
  seniority: string | null;
  experienceMin: number | null;
  experienceMax: number | null;
  remoteType: "remote" | "hybrid" | "onsite" | null;
  locations: { raw: string; city: string | null; country: string | null }[];
  eligibilityScope: "worldwide" | "regions" | "countries" | null;
  eligibleCountries: string[];
  payMin: number | null;
  payMax: number | null;
  payCurrency: string | null;
  payPeriod: "hour" | "day" | "month" | "year" | null;
  /** Skills the posting names (config/skills.yaml ids), when the skills pass has run on it. */
  skills?: { skill: string; importance: "must" | "nice" }[];
  /** The role family the decision layer gave the title (e.g. "customer-support"). */
  roleFamily?: string | null;
  postedAt?: string | null;
};

export type MatchPart = { key: string; label: string; points: number; max: number; note: string };
/** Strong fit = a high score at a level you can reach; worth a shot = a middling score or a small stretch; the rest only under "All". */
export type MatchBucket = "strong" | "worth" | "other";
export type MatchResult = { score: number; parts: MatchPart[]; flags: string[]; blocked: string | null; bucket: MatchBucket };

const WEIGHTS = { skills: 40, role: 25, level: 15, place: 10, eligibility: 10, pay: 5, freshness: 5 } as const;
/** A tool or a trade counts fully, a "nice to have" less, and everyday skills (communication) least. */
const IMPORTANCE = { must: 1, nice: 0.4 } as const;
const GENERAL = 0.25;
/** Covering this share of what a posting asks is full credit (entry-level postings list more than anyone has). */
const FULL_CREDIT = 0.8;
const STRONG_SCORE = 65;
const WORTH_SCORE = 40;
const SENIOR = new Set(["senior", "lead", "staff", "principal", "director"]);
/** A job blocked by a hard rule is shown, but never ranks above this. */
const BLOCKED_CAP = 25;

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ");

function has(text: string, term: string): boolean {
  const t = norm(term).trim();
  if (!t) return false;
  const escaped = t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(^|[^\\p{L}\\p{N}+#])${escaped}($|[^\\p{L}\\p{N}+#])`, "u").test(text);
}

function roleWords(role: string): string[] {
  return norm(role).split(/[^\p{L}\p{N}+#]+/u).filter((w) => w.length > 2 && !["and", "the", "for", "with"].includes(w));
}

function part(key: keyof typeof WEIGHTS, label: string, points: number, note: string): MatchPart {
  return { key, label, points: Math.round(points * 10) / 10, max: WEIGHTS[key], note };
}

export function scoreMatch(profile: Profile, job: MatchInput): MatchResult {
  const parts: MatchPart[] = [];
  const flags: string[] = [];
  let blocked: string | null = null;
  const title = norm(job.title);
  const text = norm(`${job.title}\n${job.description}`);

  // Skills. A posting with stored skills is judged against the dictionary: how much of what it asks (must-have
  // fully, nice-to-have less, everyday skills least) you cover. Otherwise your skills are looked up in its text.
  const mine = profile.experienceYears ?? 0;
  const myIds = new Set(skillIdsFromText(profile.skills));
  if (profile.skills.length > 0 && job.skills && job.skills.length > 0 && myIds.size > 0) {
    const weight = (s: { skill: string; importance: "must" | "nice" }) => IMPORTANCE[s.importance] * (isGeneralSkill(s.skill) ? GENERAL : 1);
    const total = job.skills.reduce((a, s) => a + weight(s), 0);
    const got = job.skills.filter((s) => myIds.has(s.skill));
    const covered = got.reduce((a, s) => a + weight(s), 0);
    const share = total > 0 ? Math.min(1, covered / total / FULL_CREDIT) : 0;
    const must = job.skills.filter((s) => s.importance === "must" && !isGeneralSkill(s.skill));
    const mustGot = must.filter((s) => myIds.has(s.skill));
    const missing = must.filter((s) => !myIds.has(s.skill)).slice(0, 3).map((s) => skillLabel(s.skill));
    const named = got.filter((s) => !isGeneralSkill(s.skill)).slice(0, 5).map((s) => skillLabel(s.skill));
    const note = must.length > 0
      ? `Matches ${mustGot.length} of ${must.length} must-have skills${named.length ? ` (${named.join(", ")})` : ""}${missing.length ? `. Missing: ${missing.join(", ")}` : ""}`
      : got.length ? `Matches ${got.length} of ${job.skills.length} skills it names` : "Names none of your skills";
    parts.push(part("skills", "Skills", WEIGHTS.skills * share, note));
  } else if (profile.skills.length > 0) {
    const hit = profile.skills.filter((s) => has(text, s));
    const need = Math.min(profile.skills.length, 6);
    const share = Math.min(1, hit.length / need);
    parts.push(part("skills", "Skills", WEIGHTS.skills * share,
      hit.length ? `Names ${hit.slice(0, 6).join(", ")}${hit.length > 6 ? ` +${hit.length - 6}` : ""}` : "Names none of your skills"));
  } else flags.push("Add skills to your profile to score this");

  // Role: the title says a role you are looking for, or the posting is in a role family you chose.
  const familyHit = !!job.roleFamily && profile.targetFamilies.includes(job.roleFamily);
  const familyLabel = ROLE_FAMILIES.find((f) => f.id === job.roleFamily)?.label ?? job.roleFamily ?? "";
  if (profile.targetRoles.length > 0 || profile.targetFamilies.length > 0) {
    let best = 0;
    let which = "";
    for (const role of profile.targetRoles) {
      const words = roleWords(role);
      if (words.length === 0) continue;
      const matched = words.filter((w) => has(title, w)).length;
      const share = has(title, role) ? 1 : matched / words.length >= 0.5 ? 0.5 : 0;
      if (share > best) { best = share; which = role; }
    }
    if (familyHit && best < 0.8) {
      parts.push(part("role", "Role", WEIGHTS.role * 0.8, `In ${familyLabel}, a field you chose`));
    } else {
      parts.push(part("role", "Role", WEIGHTS.role * best,
        best === 1 ? `Title matches "${which}"` : best > 0 ? `Title is close to "${which}"` : "Title is not one of your target roles"));
    }
  } else flags.push("Add target roles to your profile to score this");

  // Level: what the posting asks of experience, against yours.
  if (job.seniority && SENIOR.has(job.seniority)) {
    parts.push(part("level", "Level", 0, `Asks for a ${job.seniority} level`));
    if (mine < 5) blocked ??= `This is a ${job.seniority}-level role`;
  } else if (job.seniority === "intern" || job.seniority === "entry") {
    parts.push(part("level", "Level", WEIGHTS.level, job.seniority === "intern" ? "Internship" : "Entry level"));
  } else if (job.experienceMin !== null) {
    const fits = job.experienceMin <= mine + 1;
    parts.push(part("level", "Level", fits ? WEIGHTS.level : 0, fits ? `Asks ${job.experienceMin}+ years: within reach` : `Asks ${job.experienceMin}+ years`));
  } else flags.push("Level not stated");

  // Place: remote if you are open to it, or a place you named.
  const wantsPlaces = profile.locations.map(norm);
  const placeText = job.locations.map((l) => norm(`${l.raw} ${l.city ?? ""}`)).join(" | ");
  const inPlace = wantsPlaces.some((w) => w && placeText.includes(w));
  const inCountry = job.locations.some((l) => l.country && profile.workCountries.includes(l.country));
  if (job.remoteType === "remote" && profile.remoteOk) {
    parts.push(part("place", "Place", WEIGHTS.place, "Remote"));
  } else if (job.remoteType === "remote" && !profile.remoteOk) {
    parts.push(part("place", "Place", 0, "Remote, and you prefer on-site"));
  } else if (inPlace) {
    parts.push(part("place", "Place", WEIGHTS.place, "In a place you named"));
  } else if (job.locations.length > 0) {
    parts.push(part("place", "Place", inCountry ? WEIGHTS.place * 0.6 : 0, inCountry ? "In a country you can work in" : "Not in your places"));
  } else flags.push("Location not stated");

  // Eligibility: may you apply from where you can work?
  if (job.eligibilityScope === "worldwide") {
    parts.push(part("eligibility", "Eligibility", WEIGHTS.eligibility, "Open worldwide"));
  } else if (job.eligibleCountries.length > 0) {
    const ok = job.eligibleCountries.some((c) => profile.workCountries.includes(c));
    parts.push(part("eligibility", "Eligibility", ok ? WEIGHTS.eligibility : 0,
      ok ? `Open to ${job.eligibleCountries.slice(0, 4).join(", ")}` : `Only ${job.eligibleCountries.slice(0, 4).join(", ")}`));
    if (!ok) blocked ??= `Only open to ${job.eligibleCountries.slice(0, 4).join(", ")}`;
  } else flags.push("Who may apply is not stated");

  // Pay: only judged when you set a floor and the posting states yearly rupee pay.
  if (profile.minPayLpa !== null) {
    const top = job.payMax ?? job.payMin;
    if (top !== null && job.payCurrency === "INR" && job.payPeriod === "year") {
      const lpa = top / 100_000;
      parts.push(part("pay", "Pay", lpa >= profile.minPayLpa ? WEIGHTS.pay : 0, `Up to ₹${+lpa.toFixed(1)} LPA vs your ₹${profile.minPayLpa} LPA floor`));
    } else flags.push("Pay not stated in rupees per year");
  }

  // Freshness: a job posted today is worth applying to today.
  if (job.postedAt) {
    const hours = (Date.now() - new Date(job.postedAt).getTime()) / 3_600_000;
    if (Number.isFinite(hours) && hours >= 0) {
      const f = hours <= 48 ? 1 : hours <= 24 * 7 ? 0.6 : hours <= 24 * 30 ? 0.3 : 0;
      parts.push(part("freshness", "Freshness", WEIGHTS.freshness * f,
        hours <= 48 ? "Posted in the last 2 days" : hours <= 24 * 7 ? "Posted this week" : hours <= 24 * 30 ? "Posted this month" : "Posted over a month ago"));
    }
  }

  const max = parts.reduce((s, p) => s + p.max, 0);
  const pts = parts.reduce((s, p) => s + p.points, 0);
  let score = max > 0 ? Math.round((pts / max) * 100) : 0;
  if (blocked) score = Math.min(score, BLOCKED_CAP);
  const reachable = job.experienceMin === null || job.experienceMin <= mine + 1;
  const stretch = job.experienceMin !== null && job.experienceMin <= mine + 2;
  const bucket: MatchBucket = blocked ? "other" : score >= STRONG_SCORE && reachable ? "strong" : score >= WORTH_SCORE || stretch ? "worth" : "other";
  return { score, parts, flags, blocked, bucket };
}
