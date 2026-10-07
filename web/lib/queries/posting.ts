import { sql } from "drizzle-orm";
import { safeHttpUrl, type FeedDb, type FeedLocation } from "@/lib/queries/feed";
import { toIso } from "@/lib/queries/time";

/** Everything the detail page shows, each stated fact with where it came from. */
export type PostingDetail = {
  id: number;
  title: string;
  companyName: string | null;
  source: string;
  status: string;
  descriptionMd: string;
  applyUrl: string | null;
  postedAt: string | null;
  postedAtProvenance: string;
  firstSeenAt: string | null;
  lastSeenAt: string | null;
  employmentType: string | null;
  employmentTypeProvenance: string;
  seniority: string | null;
  seniorityProvenance: string;
  experienceMin: number | null;
  experienceMax: number | null;
  experienceProvenance: string;
  remoteType: "remote" | "hybrid" | "onsite" | null;
  remoteTypeProvenance: string;
  locations: FeedLocation[];
  locationsProvenance: string;
  eligibilityScope: "worldwide" | "regions" | "countries" | null;
  eligibleCountries: string[];
  eligibilityProvenance: string;
  visaSponsorship: "yes" | "no" | null;
  visaProvenance: string;
  workAuthRequired: string[];
  /** The stored decision: can a person in India take this job, and why (null until decided). */
  indiaEligible: "yes" | "no" | "unknown" | null;
  indiaReason: string | null;
  flags: string[];
  labels: string[];
  roleFamily: string | null;
  /** Skills the skills pass found (config/skills.yaml ids), for the fit score. */
  skills: { skill: string; importance: "must" | "nice" }[];
  payMin: number | null;
  payMax: number | null;
  payCurrency: string | null;
  payPeriod: "hour" | "day" | "month" | "year" | null;
  payProvenance: string;
  deadlineAt: string | null;
  deadlineProvenance: string;
};

const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const num = (v: unknown): number | null => {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};
const iso = toIso;
const prov = (v: unknown): string => (typeof v === "string" && v !== "" ? v : "unknown");
const list = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

function locations(v: unknown): FeedLocation[] {
  let value = v;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return [];
    }
  }
  if (!Array.isArray(value)) return [];
  return value
    .filter((x): x is Record<string, unknown> => typeof x === "object" && x !== null)
    .map((x) => ({ raw: str(x.raw) ?? "", city: str(x.city), region: str(x.region), country: str(x.country) }))
    .filter((x) => x.raw !== "");
}

/** One posting by id, or null when it does not exist (or the id is not a positive integer). */
export async function queryPosting(db: FeedDb, id: number): Promise<PostingDetail | null> {
  if (!Number.isInteger(id) || id < 1 || id > 2_147_483_647) return null;
  const res = await db.execute(sql`
    SELECT p.*, c.name AS company_name
    FROM hunterrr.postings p
    LEFT JOIN hunterrr.companies c ON c.id = p.company_id
    WHERE p.id = ${id}`);
  const r = res.rows[0];
  if (!r) return null;
  const skillRows = await db.execute(sql`SELECT skill, importance FROM hunterrr.posting_skills WHERE posting_id = ${id} ORDER BY skill`);
  return {
    id: Number(r.id),
    title: String(r.title),
    companyName: str(r.company_name),
    source: String(r.source),
    status: String(r.status),
    descriptionMd: typeof r.description_md === "string" ? r.description_md : "",
    applyUrl: safeHttpUrl(r.apply_url_raw),
    postedAt: iso(r.posted_at),
    postedAtProvenance: prov(r.posted_at_provenance),
    firstSeenAt: iso(r.first_seen_at),
    lastSeenAt: iso(r.last_seen_at),
    employmentType: str(r.employment_type),
    employmentTypeProvenance: prov(r.employment_type_provenance),
    seniority: str(r.seniority),
    seniorityProvenance: prov(r.seniority_provenance),
    experienceMin: num(r.experience_min_years),
    experienceMax: num(r.experience_max_years),
    experienceProvenance: prov(r.experience_min_years_provenance),
    remoteType: (str(r.remote_type) as PostingDetail["remoteType"]) ?? null,
    remoteTypeProvenance: prov(r.remote_type_provenance),
    locations: locations(r.locations),
    locationsProvenance: prov(r.locations_provenance),
    eligibilityScope: (str(r.eligibility_scope) as PostingDetail["eligibilityScope"]) ?? null,
    eligibleCountries: list(r.eligible_countries),
    eligibilityProvenance: prov(r.eligibility_scope_provenance),
    visaSponsorship: (str(r.visa_sponsorship) as PostingDetail["visaSponsorship"]) ?? null,
    visaProvenance: prov(r.visa_sponsorship_provenance),
    workAuthRequired: list(r.work_auth_required),
    indiaEligible: r.decision_key ? ((str(r.india_eligible) as PostingDetail["indiaEligible"]) ?? null) : null,
    indiaReason: r.decision_key ? str(r.india_reason) : null,
    flags: list(r.flags),
    labels: list(r.labels),
    roleFamily: str(r.role_family),
    skills: skillRows.rows.map((k) => ({ skill: String(k.skill), importance: k.importance === "nice" ? "nice" as const : "must" as const })),
    payMin: num(r.pay_min),
    payMax: num(r.pay_max),
    payCurrency: str(r.pay_currency),
    payPeriod: (str(r.pay_period) as PostingDetail["payPeriod"]) ?? null,
    payProvenance: prov(r.pay_provenance),
    deadlineAt: iso(r.deadline_at),
    deadlineProvenance: prov(r.deadline_at_provenance),
  };
}
