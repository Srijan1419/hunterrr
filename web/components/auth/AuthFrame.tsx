import type { ReactNode } from "react";
import styles from "./auth.module.css";

/**
 * The signed-out frame (sign-in, not-allowed): what Hunterrr is on the left, one card with one
 * action on the right. Every fact on the brand panel is something the app really does.
 */
export function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <main className={styles.frame}>
      <section className={styles.brand} aria-label="About Hunterrr">
        <span className={styles.logo}>
          <span className={styles.logoWord}>hunterrr</span>
          <span className={styles.logoDot} aria-hidden="true" />
        </span>
        <div className={styles.pitch}>
          <h2 className={styles.headline}>Entry-level jobs you can actually apply to.</h2>
          <p className={styles.sub}>
            Hunterrr reads company job boards every few hours and keeps the roles a fresher can apply
            for, each with a link to the company&apos;s own application page.
          </p>
          <ul className={styles.facts}>
            <li><span className={styles.tick} aria-hidden="true">✓</span>Intern, fresher and entry-level roles first</li>
            <li><span className={styles.tick} aria-hidden="true">✓</span>Direct links to the company&apos;s own job page</li>
            <li><span className={styles.tick} aria-hidden="true">✓</span>A tracker for every application you send</li>
          </ul>
        </div>
        <p className={`${styles.small} ${styles.brandFoot}`}>A personal tool. Job data from public company job boards.</p>
      </section>
      <div className={styles.side}>
        <div className={styles.card}>{children}</div>
      </div>
    </main>
  );
}
