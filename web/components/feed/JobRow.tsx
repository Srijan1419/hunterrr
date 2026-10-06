import Link from "next/link";
import { Chip } from "@/components/atlas/Chip";
import { SaveButton } from "@/components/tracker/SaveButton";
import type { FeedRow } from "@/lib/queries/feed";
import { formatEligibility, formatLocation, formatPay, formatPosted } from "@/lib/feed-format";
import styles from "./feed.module.css";

const REMOTE_LABEL = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" } as const;

/** Up to two initials from the company name ("Acme Corp" -> "AC"); "?" when the company is unknown. */
export function initials(name: string | null): string {
  const words = (name ?? "").replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  const letters = words.length === 1 ? words[0].slice(0, 2) : words[0][0] + words[1][0];
  return letters.toUpperCase();
}

/** One posting in the feed. Chips only appear for facts the posting states; nothing is guessed. */
export function JobRow({ row, now, saved = false }: { row: FeedRow; now?: Date; saved?: boolean }) {
  const pay = formatPay(row);
  const eligibility = formatEligibility(row);
  const location = formatLocation(row);
  const posted = formatPosted(row.postedAt, now);

  return (
    <li className={styles.row}>
      <span className={styles.mark} aria-hidden="true">{initials(row.companyName)}</span>
      <div className={styles.main}>
        <h2 className={styles.jobTitle}>
          <Link href={`/jobs/${row.id}`} className={styles.titleLink}>{row.title}</Link>
        </h2>
        <div className={styles.meta}>
          {row.companyName ? <span>{row.companyName}</span> : null}
          {location ? <span>{location}</span> : null}
          {row.seniority ? <span>{row.seniority}</span> : null}
        </div>
        <div className={styles.chips}>
          {row.remoteType ? (
            <Chip tone={row.remoteType === "remote" ? "ok" : "default"}>{REMOTE_LABEL[row.remoteType]}</Chip>
          ) : null}
          {eligibility ? <Chip tone={row.eligibilityScope === "worldwide" ? "ok" : "default"}>{eligibility}</Chip> : null}
        </div>
      </div>
      <div className={styles.side}>
        {pay ? <span className={styles.pay}>{pay}</span> : null}
        {posted ? <span className={styles.posted}>{posted}</span> : null}
        <div className={styles.actions}>
        {row.applyUrl ? (
          <a
            className={styles.apply}
            href={row.applyUrl}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Apply to ${row.title}`}
          >
            Apply
          </a>
        ) : (
          <span className={styles.noApply}>No apply link</span>
        )}
        <SaveButton postingId={row.id} saved={saved} />
        </div>
      </div>
    </li>
  );
}
