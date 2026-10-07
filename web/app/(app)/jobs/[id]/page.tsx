import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Chip } from "@/components/atlas/Chip";
import { MatchDial } from "@/components/atlas/MatchDial";
import { ScoreBar } from "@/components/atlas/ScoreBar";
import { Description } from "@/components/feed/Description";
import { initials } from "@/components/feed/JobRow";
import { PostingFacts } from "@/components/feed/PostingFacts";
import styles from "@/components/feed/detail.module.css";
import { requireSession } from "@/lib/auth/session";
import { db } from "@/lib/db/client.v2";
import { AppliedButton } from "@/components/tracker/AppliedButton";
import { SaveButton } from "@/components/tracker/SaveButton";
import { WrongButton } from "@/components/feed/WrongButton";
import { SOURCE_VIA, labelText } from "@/components/feed/JobRow";
import { formatEligibility, formatLocation, formatPay } from "@/lib/feed-format";
import { scoreMatch } from "@/lib/match/score";
import { getActiveProfile } from "@/lib/queries/profile";
import { queryPosting } from "@/lib/queries/posting";
import { postingApplicationState, savedPostingIds } from "@/lib/queries/tracker";

export const dynamic = "force-dynamic";

type Params = Promise<{ id: string }>;

const REMOTE_LABEL = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" } as const;

function parseId(raw: string): number {
  return /^[0-9]{1,10}$/.test(raw) ? Number(raw) : NaN;
}

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const posting = await queryPosting(db as never, parseId((await params).id));
  return { title: posting ? `${posting.title} | hunterrr` : "Job not found | hunterrr" };
}

export default async function JobDetailPage({ params }: { params: Params }) {
  const posting = await queryPosting(db as never, parseId((await params).id));
  if (!posting) notFound();
  const { user } = await requireSession();
  const saved = (await savedPostingIds(db as never, user.id, [posting.id])).has(posting.id);
  const applicationState = await postingApplicationState(db as never, user.id, posting.id);
  const stored = await getActiveProfile(db as never, user.id);
  const match = stored
    ? scoreMatch(stored.data, {
        title: posting.title, description: posting.descriptionMd.slice(0, 4000), seniority: posting.seniority,
        experienceMin: posting.experienceMin, experienceMax: posting.experienceMax, remoteType: posting.remoteType,
        locations: posting.locations, eligibilityScope: posting.eligibilityScope, eligibleCountries: posting.eligibleCountries,
        payMin: posting.payMin, payMax: posting.payMax, payCurrency: posting.payCurrency, payPeriod: posting.payPeriod,
        skills: posting.skills, roleFamily: posting.roleFamily, postedAt: posting.postedAt,
      })
    : null;
  const location = formatLocation(posting);
  const pay = formatPay(posting);
  const eligibility = formatEligibility(posting);

  return (
    <article className={styles.page}>
      <Link href="/jobs" className={styles.back}>
        ← All jobs
      </Link>
      <header className={styles.head}>
        <div className={styles.headRow}>
          <span className={styles.mark} aria-hidden="true">{initials(posting.companyName)}</span>
          <div className={styles.headText}>
            <h1 className={styles.title}>{posting.title}</h1>
            {posting.companyName ? <p className={styles.company}>{posting.companyName}</p> : null}
          </div>
        </div>
        <div className={styles.quick}>
          {posting.remoteType ? <Chip tone={posting.remoteType === "remote" ? "ok" : "default"}>{REMOTE_LABEL[posting.remoteType]}</Chip> : null}
          {location ? <Chip>{location}</Chip> : null}
          {eligibility ? <Chip tone={posting.eligibilityScope === "worldwide" ? "ok" : "default"}>{eligibility}</Chip> : null}
          {posting.indiaReason ? <Chip tone={posting.indiaEligible === "yes" ? "ok" : "default"}>{`India: ${posting.indiaReason}`}</Chip> : null}
          {posting.labels.map((l) => { const text = labelText(l); return text ? <Chip key={l}>{text}</Chip> : null; })}
          {pay ? <Chip>{pay}</Chip> : null}
          {posting.seniority ? <Chip>{posting.seniority}</Chip> : null}
          {posting.status !== "open" ? <span className={styles.closed}>No longer listed</span> : null}
        </div>
        <div className={styles.actions}>
          {posting.applyUrl ? (
            <a className={styles.apply} href={posting.applyUrl} target="_blank" rel="noopener noreferrer">
              {SOURCE_VIA[posting.source] ? `Apply on ${SOURCE_VIA[posting.source]}` : "Apply on the employer's page"}
            </a>
          ) : (
            <span className={styles.noApply}>No apply link was found for this posting.</span>
          )}
          <SaveButton postingId={posting.id} saved={saved} />
          <AppliedButton postingId={posting.id} state={applicationState} />
          <WrongButton postingId={posting.id} />
        </div>
      </header>
      <section className={styles.card} aria-labelledby="why">
        <h2 id="why" className={styles.cardTitle}>Why this is on your list</h2>
        <ul className={styles.reasons}>
          <li>
            {posting.indiaEligible === "yes" && posting.indiaReason ? <><strong>Open to India.</strong> {posting.indiaReason}.</> : null}
            {posting.indiaEligible === "unknown" ? <><strong>Not confirmed for India.</strong> The posting does not say who may apply. Check before you apply.</> : null}
            {posting.indiaEligible === "no" && posting.indiaReason ? <><strong>Not open to India.</strong> {posting.indiaReason}.</> : null}
            {posting.indiaEligible === null ? <><strong>Not checked yet.</strong> This posting has not been through the India rule.</> : null}
          </li>
          {match ? (
            <li>
              {match.blocked ? <><strong>Held back.</strong> {match.blocked}.</> : (
                <><strong>{match.bucket === "strong" ? "Strong fit" : match.bucket === "worth" ? "Worth a shot" : "Weak fit"}.</strong>{" "}
                  {match.parts.find((x) => x.key === "skills")?.note ?? `${match.score} out of 100`}.</>
              )}
            </li>
          ) : (
            <li><Link href="/profile">Add your profile</Link> to see how this fits you.</li>
          )}
          {posting.experienceMin !== null ? (
            <li><strong>Experience.</strong> Asks {posting.experienceMin}{posting.experienceMax && posting.experienceMax !== posting.experienceMin ? `–${posting.experienceMax}` : "+"} years{posting.experienceMin <= 2 ? ": within reach for a fresher" : ""}.</li>
          ) : null}
        </ul>
      </section>
      <div className={styles.layout}>
        <section className={styles.card} aria-labelledby="about">
          <h2 id="about" className={styles.cardTitle}>
            About the role
          </h2>
          {posting.descriptionMd ? (
            <Description text={posting.descriptionMd} />
          ) : (
            <p className={styles.noApply}>The posting has no description text.</p>
          )}
        </section>
        <div className={styles.side}>
          {match ? (
            <aside className={styles.card} aria-labelledby="fit">
              <h2 id="fit" className={styles.cardTitle}>
                Your fit
              </h2>
              <div className={styles.fitHead}>
                <MatchDial score={match.score} size={64} />
                <p className={styles.fitLine}>
                  {match.blocked ? <strong className={styles.fitBlocked}>{match.blocked}</strong> : <strong>{match.score} out of 100</strong>}
                  <span>from {match.parts.length} signal{match.parts.length === 1 ? "" : "s"} the posting states</span>
                </p>
              </div>
              <div className={styles.fitParts}>
                {match.parts.map((p) => (
                  <div key={p.key} className={styles.fitPart}>
                    <ScoreBar label={p.label} points={p.points} max={p.max} />
                    <span className={styles.fitNote}>{p.note}</span>
                  </div>
                ))}
              </div>
              {match.flags.length > 0 ? <p className={styles.source}>Not counted (not stated): {match.flags.join(" · ")}</p> : null}
            </aside>
          ) : (
            <aside className={styles.card} aria-labelledby="fit">
              <h2 id="fit" className={styles.cardTitle}>
                Your fit
              </h2>
              <p className={styles.noApply}>
                <Link href="/profile">Add your profile</Link> to see how well this job fits you, and why.
              </p>
            </aside>
          )}
          <aside className={styles.card} aria-labelledby="facts">
            <h2 id="facts" className={styles.cardTitle}>
              Key facts
            </h2>
            <PostingFacts posting={posting} />
            <p className={styles.source}>
              Each tag shows where a fact came from. &quot;Not stated&quot; means the posting does not say; nothing is guessed.
            </p>
          </aside>
        </div>
      </div>
    </article>
  );
}
