import Link from "next/link";
import { Chip } from "@/components/atlas/Chip";
import type { FeedRow } from "@/lib/queries/feed";
import { formatEligibility, formatLocation, formatPay, formatPosted } from "@/lib/feed-format";
import styles from "./feed.module.css";

const REMOTE_LABEL = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" } as const;

/** One posting in the feed. Chips only appear for facts the posting states; nothing is guessed. */
export function JobRow({ row, now }: { row: FeedRow; now?: Date }) {
  const pay = formatPay(row);
  const eligibility = formatEligibility(row);
  const location = formatLocation(row);
  const posted = formatPosted(row.postedAt, now);

  return (
    <li className={styles.row}>
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
      </div>
    </li>
  );
}
