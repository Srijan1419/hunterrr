import { ProvenanceChip, type ProvenanceSource } from "@/components/atlas/ProvenanceChip";
import { formatEligibility, formatLocation, formatPay, formatPosted } from "@/lib/feed-format";
import type { PostingDetail } from "@/lib/queries/posting";
import styles from "./detail.module.css";

const REMOTE_LABEL = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" } as const;
const WORK_AUTH_LABEL: Record<string, string> = {
  us_work_authorization: "US work authorization",
  uk_right_to_work: "UK right to work",
  eu_work_permit: "EU work permit",
  india_work_permit: "India work permit",
  security_clearance: "Security clearance",
  citizenship: "Citizenship required",
};

/** The database enum calls a hand-entered value `user`; the chip calls it `manual`. */
export function toChipSource(provenance: string): ProvenanceSource {
  if (provenance === "user") return "manual";
  const known: ProvenanceSource[] = ["jsonld", "source", "rule", "llm", "manual", "unknown"];
  return (known as string[]).includes(provenance) ? (provenance as ProvenanceSource) : "unknown";
}

type Row = { label: string; value: string | null; provenance: string };

/** Every fact the page can state, in reading order. A value that is not stated reads "Not stated". */
export function factRows(p: PostingDetail, now?: Date): Row[] {
  const experience =
    p.experienceMin !== null && p.experienceMax !== null && p.experienceMin !== p.experienceMax
      ? `${p.experienceMin}–${p.experienceMax} years`
      : p.experienceMin !== null
        ? `${p.experienceMin}+ years`
        : p.experienceMax !== null
          ? `up to ${p.experienceMax} years`
          : null;
  const visa = p.visaSponsorship === "yes" ? "Sponsorship offered" : p.visaSponsorship === "no" ? "No sponsorship" : null;
  const auth = p.workAuthRequired.map((a) => WORK_AUTH_LABEL[a] ?? a).join(", ") || null;
  return [
    { label: "Work type", value: p.remoteType ? REMOTE_LABEL[p.remoteType] : null, provenance: p.remoteTypeProvenance },
    { label: "Location", value: formatLocation({ locations: p.locations }), provenance: p.locationsProvenance },
    { label: "Who can apply", value: formatEligibility(p), provenance: p.eligibilityProvenance },
    { label: "Pay", value: formatPay(p), provenance: p.payProvenance },
    { label: "Experience", value: experience, provenance: p.experienceProvenance },
    { label: "Seniority", value: p.seniority, provenance: p.seniorityProvenance },
    { label: "Employment", value: p.employmentType?.replace(/_/g, " ") ?? null, provenance: p.employmentTypeProvenance },
    { label: "Visa", value: visa, provenance: p.visaProvenance },
    { label: "Work authorization", value: auth, provenance: p.workAuthRequired.length ? p.visaProvenance : "unknown" },
    { label: "Posted", value: formatPosted(p.postedAt, now), provenance: p.postedAtProvenance },
    {
      label: "Apply by",
      value: p.deadlineAt ? new Date(p.deadlineAt).toISOString().slice(0, 10) : null,
      provenance: p.deadlineProvenance,
    },
  ];
}

export function PostingFacts({ posting, now }: { posting: PostingDetail; now?: Date }) {
  return (
    <dl className={styles.facts}>
      {factRows(posting, now).map((row) => (
        <div key={row.label} className={styles.fact}>
          <dt>{row.label}</dt>
          <dd>
            {row.value ? (
              <>
                <span>{row.value}</span> <ProvenanceChip source={toChipSource(row.provenance)} />
              </>
            ) : (
              <span className={styles.unstated}>Not stated</span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
