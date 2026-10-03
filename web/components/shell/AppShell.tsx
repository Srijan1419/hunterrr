import type { ReactNode } from "react";
import { TopBar } from "./TopBar";
import styles from "./shell.module.css";

/**
 * The frame every signed-in screen lives in: top bar, `<main id="main">`,
 * and the footer with the required Remote OK attribution link (followed —
 * their terms require it, so no `nofollow`).
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className={styles.shell}>
      <TopBar />
      <main id="main" className={styles.main}>
        {children}
      </main>
      <footer className={styles.footer}>
        Job data from{" "}
        <a href="https://remoteok.com" target="_blank" rel="noopener" className={styles.footerLink}>
          Remote OK
        </a>{" "}
        and public job boards.
      </footer>
    </div>
  );
}
