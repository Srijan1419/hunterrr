import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PostingFacts } from "@/components/feed/PostingFacts";
import styles from "@/components/feed/detail.module.css";
import { db } from "@/lib/db/client.v2";
import { SaveButton } from "@/components/tracker/SaveButton";
import { queryPosting } from "@/lib/queries/posting";
import { savedPostingIds } from "@/lib/queries/tracker";

export const dynamic = "force-dynamic";

type Params = Promise<{ id: string }>;

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
  const saved = (await savedPostingIds(db as never, [posting.id])).has(posting.id);

  return (
    <article className={styles.page}>
      <Link href="/jobs" className={styles.back}>
        ← All jobs
      </Link>
      <header className={styles.head}>
        <h1 className={styles.title}>{posting.title}</h1>
        {posting.companyName ? <p className={styles.company}>{posting.companyName}</p> : null}
        <div className={styles.actions}>
          {posting.applyUrl ? (
            <a className={styles.apply} href={posting.applyUrl} target="_blank" rel="noopener noreferrer">
              Apply on the employer&apos;s page
            </a>
          ) : (
            <span className={styles.noApply}>No apply link was found for this posting.</span>
          )}
          <SaveButton postingId={posting.id} saved={saved} />
          {posting.status !== "open" ? <span className={styles.closed}>No longer listed</span> : null}
        </div>
      </header>
      <div className={styles.layout}>
        <section className={styles.card} aria-labelledby="about">
          <h2 id="about" className={styles.cardTitle}>
            About the role
          </h2>
          {posting.descriptionMd ? (
            <p className={styles.description}>{posting.descriptionMd}</p>
          ) : (
            <p className={styles.noApply}>The posting has no description text.</p>
          )}
        </section>
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
    </article>
  );
}
