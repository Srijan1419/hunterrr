import { Chip } from "@/components/atlas/Chip";
import { StatTile } from "@/components/atlas/StatTile";
import { formatPosted } from "@/lib/feed-format";
import { describeRun, type SourcesOverview } from "@/lib/queries/sources";
import styles from "./sources.module.css";

export const SOURCES_EMPTY =
  "Where each job feed stands will appear here: last run, how many jobs, and what failed.";

const ATS_LABEL: Record<string, string> = {
  greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", workable: "Workable", smartrecruiters: "SmartRecruiters",
};

function ago(iso: string | null, now: Date): string {
  return formatPosted(iso, now) ?? "never";
}

export function SourcesView({ data, now = new Date() }: { data: SourcesOverview; now?: Date }) {
  const hasData = data.totals.boards > 0 || data.runs.length > 0;
  const lastCollect = data.runs.find((r) => r.workflow === "collect");
  const lastProcess = data.runs.find((r) => r.workflow === "process");
  return (
    <div className={styles.page}>
      <h1 className={styles.title}>Sources</h1>
      {!hasData ? (
        <p>{SOURCES_EMPTY}</p>
      ) : (
        <>
          <div className={styles.tiles}>
            <StatTile value={data.totals.boards.toLocaleString("en-US")} label="boards watched" />
            <StatTile value={data.totals.openPostings.toLocaleString("en-US")} label="open postings" />
            <StatTile
              value={data.totals.problemBoards}
              label="boards needing a look"
              tone={data.totals.problemBoards > 0 ? "hot" : "default"}
            />
            <StatTile value={lastCollect ? ago(lastCollect.startedAt, now) : "never"} label="last collection" />
            <StatTile value={lastProcess ? ago(lastProcess.startedAt, now) : "never"} label="last processing" />
          </div>

          <section aria-labelledby="by-ats" className={styles.card}>
            <h2 id="by-ats" className={styles.cardTitle}>Job boards by system</h2>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">System</th>
                  <th scope="col">Boards</th>
                  <th scope="col">Postings</th>
                  <th scope="col">Quiet / blocked / dead</th>
                  <th scope="col">Last polled</th>
                </tr>
              </thead>
              <tbody>
                {data.byAts.map((a) => (
                  <tr key={a.ats}>
                    <th scope="row">{ATS_LABEL[a.ats] ?? a.ats}</th>
                    <td>{a.boards}</td>
                    <td>{a.postings.toLocaleString("en-US")}</td>
                    <td>{`${a.byStatus.quiet} / ${a.byStatus.blocked} / ${a.byStatus.dead}`}</td>
                    <td>{ago(a.lastPolledAt, now)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section aria-labelledby="problems" className={styles.card}>
            <h2 id="problems" className={styles.cardTitle}>Boards needing a look</h2>
            {data.problems.length === 0 ? (
              <p className={styles.muted}>Every board is polling normally.</p>
            ) : (
              <ul className={styles.list}>
                {data.problems.map((p) => (
                  <li key={p.id} className={styles.row}>
                    <span className={styles.name}>{p.companyName ?? p.slug}</span>
                    <span className={styles.muted}>{ATS_LABEL[p.ats] ?? p.ats} · {p.slug}</span>
                    <Chip tone={p.status === "active" ? "default" : "hot"}>{p.status}</Chip>
                    <span className={styles.muted}>
                      {p.consecutiveFailures > 0 ? `${p.consecutiveFailures} failed in a row · ` : ""}
                      last polled {ago(p.lastPolledAt, now)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="runs" className={styles.card}>
            <h2 id="runs" className={styles.cardTitle}>Recent runs</h2>
            {data.runs.length === 0 ? (
              <p className={styles.muted}>No run has been recorded yet.</p>
            ) : (
              <ul className={styles.list}>
                {data.runs.map((r) => (
                  <li key={r.id} className={styles.row}>
                    <span className={styles.name}>{r.workflow}</span>
                    <Chip tone={r.status === "ok" ? "ok" : "hot"}>{r.status}</Chip>
                    <span className={styles.muted}>{ago(r.startedAt, now)}</span>
                    <span className={styles.muted}>{describeRun(r) || r.errorSummary}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
