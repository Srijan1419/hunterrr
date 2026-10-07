import type { SkillGap as Gap } from "@/lib/queries/gap";
import styles from "./profile.module.css";

/** The skills missing from the profile that block the most fitting jobs. Plain numbers, no advice beyond them. */
export function SkillGap({ gap }: { gap: Gap }) {
  if (gap.considered === 0) {
    return (
      <section className={styles.card} aria-labelledby="gap">
        <h2 id="gap" className={styles.cardTitle}>What to learn next</h2>
        <p className={styles.hint}>Once some jobs fit your profile, this shows the skill that blocks the most of them.</p>
      </section>
    );
  }
  return (
    <section className={styles.card} aria-labelledby="gap">
      <h2 id="gap" className={styles.cardTitle}>What to learn next</h2>
      {gap.gaps.length === 0 ? (
        <p className={styles.hint}>Across your {gap.considered} fitting jobs, no must-have skill is missing from your profile.</p>
      ) : (
        <>
          <p className={styles.hint}>Must-have skills you do not list, across your {gap.considered} strong-fit and worth-a-shot jobs:</p>
          <ul className={styles.gapList}>
            {gap.gaps.map((g) => (
              <li key={g.skill}>
                <strong>{g.label}</strong> <span className={styles.hint}>asked in {g.jobs} of {gap.considered}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
