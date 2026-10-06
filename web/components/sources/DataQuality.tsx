import { StatTile } from "@/components/atlas/StatTile";
import { share, type Quality } from "@/lib/queries/quality";
import styles from "./sources.module.css";

/** The accuracy scoreboard: how much of the open data states what the feed filters on. */
export function DataQuality({ q }: { q: Quality }) {
  return (
    <section className={styles.qualityCard} aria-labelledby="quality">
      <h2 id="quality" className={styles.sectionTitle}>Data quality</h2>
      <p className={styles.qualityLead}>
        Of {q.open.toLocaleString("en-US")} open postings, how many state what the feed filters on. A low number means the
        filter hides many jobs because they do not say, never because they were guessed.
      </p>
      <div className={styles.qualityGrid}>
        <StatTile value={share(q.modeKnown, q.open)} label="Work mode stated (remote, hybrid, on-site)" />
        <StatTile value={share(q.levelKnown, q.open)} label="Level or years of experience stated" />
        <StatTile value={share(q.eligibilityKnown, q.open)} label="Who may apply stated" />
        <StatTile value={share(q.payKnown, q.open)} label="Pay stated" />
        <StatTile value={q.inDefaultFeed.toLocaleString("en-US")} label="In your default feed (remote, India, entry level)" />
        <StatTile value={q.closed.toLocaleString("en-US")} label="Closed: taken down by the company" />
        <StatTile value={q.stale.toLocaleString("en-US")} label="Open but not confirmed in 7 days" tone={q.stale > 0 ? "hot" : "default"} />
        <StatTile value={q.noBoard.toLocaleString("en-US")} label="Open with no board linked (cannot be closed)" tone={q.noBoard > 0 ? "hot" : "default"} />
      </div>
    </section>
  );
}
