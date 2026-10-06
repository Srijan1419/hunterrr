import styles from "@/components/feed/feed.module.css";

/** Shown while a signed-in page loads. It stays invisible for 300 ms, so quick pages never flash it. */
export default function Loading() {
  return (
    <div className={styles.skelPage} role="status" aria-label="Loading">
      <div className={styles.skelBar} />
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} className={styles.skelRow} />
      ))}
    </div>
  );
}
