import Link from "next/link";
import { sql } from "drizzle-orm";
import { db } from "@/lib/db/client";
import { jobs, skillsDaily } from "@/lib/db/schema";
import { getTopSkills } from "@/app/skills/queries";

// Rebuilt at most hourly: the pipeline refreshes the data every six hours.
export const revalidate = 3600;

interface HomeStats {
  jobs: number;
  sources: number;
  skills: number;
  lastDay: string | null;
  top: { label: string; count: number }[];
}

async function loadStats(): Promise<HomeStats | null> {
  try {
    const [j] = await db
      .select({
        n: sql<number>`count(*)`,
        s: sql<number>`count(distinct ${jobs.source})`,
      })
      .from(jobs);
    const [d] = await db
      .select({
        k: sql<number>`count(distinct ${skillsDaily.skill})`,
        last: sql<string | null>`max(${skillsDaily.day})`,
      })
      .from(skillsDaily);
    const top = await getTopSkills({}, 6);
    return {
      jobs: Number(j.n),
      sources: Number(j.s),
      skills: Number(d.k),
      lastDay: d.last,
      top: top.map((t) => ({ label: t.skillLabel, count: t.postingsCount })),
    };
  } catch {
    return null; // the page still renders without the numbers
  }
}

const fmt = (n: number) => new Intl.NumberFormat("en-US").format(n);

export default async function Home() {
  const stats = await loadStats();
  const max = Math.max(1, ...(stats?.top.map((t) => t.count) ?? [1]));

  return (
    <main>
      <section className="relative overflow-hidden">
        <div className="grid-fade pointer-events-none absolute inset-0" aria-hidden />
        <div className="relative mx-auto max-w-6xl px-4 pb-16 pt-20 text-center sm:pt-28">
          <p className="mx-auto mb-5 inline-flex items-center gap-2 rounded-full border bg-card/60 px-3 py-1 text-xs text-muted-foreground">
            <span className="size-1.5 rounded-full bg-emerald-400" />
            Refreshed every 6 hours from public job feeds
          </p>
          <h1 className="mx-auto max-w-3xl text-balance text-5xl font-bold tracking-tight sm:text-6xl">
            See what the <span className="text-gradient">remote job market</span> is
            really asking for
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-balance text-lg text-muted-foreground">
            Skills in demand, hiring trends and pay, pulled from live postings and
            normalized into one clean dataset, including where the data is thin.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Link
              href="/skills"
              className="rounded-lg bg-primary px-5 py-2.5 font-medium text-primary-foreground shadow-lg shadow-primary/30 transition hover:opacity-90"
            >
              Explore skills
            </Link>
            <Link
              href="/signup"
              className="rounded-lg border bg-card/60 px-5 py-2.5 font-medium transition hover:bg-muted"
            >
              Create free account
            </Link>
          </div>
        </div>
      </section>

      {stats && (
        <section className="mx-auto max-w-6xl px-4 pb-16">
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat value={fmt(stats.jobs)} label="Postings tracked" />
            <Stat value={fmt(stats.skills)} label="Distinct skills" />
            <Stat value={fmt(stats.sources)} label="Job sources" />
          </div>

          <div className="mt-6 grid gap-6 lg:grid-cols-5">
            <div className="rounded-xl border bg-card p-6 lg:col-span-3">
              <div className="mb-5 flex items-baseline justify-between">
                <h2 className="font-semibold">Top skills right now</h2>
                <Link href="/skills" className="text-sm text-primary hover:underline">
                  See all →
                </Link>
              </div>
              <ul className="space-y-3">
                {stats.top.map((t) => (
                  <li key={t.label} className="grid grid-cols-[7rem_1fr_3.5rem] items-center gap-3 text-sm">
                    <span className="truncate">{t.label}</span>
                    <span className="h-2.5 overflow-hidden rounded-full bg-muted">
                      <span
                        className="block h-full rounded-full bg-gradient-to-r from-violet-500 to-cyan-400"
                        style={{ width: `${Math.max(4, (t.count / max) * 100)}%` }}
                      />
                    </span>
                    <span className="text-right tabular-nums text-muted-foreground">{fmt(t.count)}</span>
                  </li>
                ))}
              </ul>
              {stats.lastDay && (
                <p className="mt-5 text-xs text-muted-foreground">Latest data: {stats.lastDay}</p>
              )}
            </div>

            <div className="grid gap-4 lg:col-span-2">
              <Tile href="/trends" title="Trends" body="Posting volume by day, source and weekday." />
              <Tile href="/coverage" title="Coverage" body="Which sources and countries we can and can't see, stated openly." />
              <Tile href="/jobs" title="Browse jobs" body="Filter postings, save searches, shortlist roles." />
            </div>
          </div>
        </section>
      )}
    </main>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-xl border bg-card p-6">
      <p className="text-gradient text-4xl font-bold tabular-nums tracking-tight">{value}</p>
      <p className="mt-1 text-sm text-muted-foreground">{label}</p>
    </div>
  );
}

function Tile({ href, title, body }: { href: string; title: string; body: string }) {
  return (
    <Link
      href={href}
      className="group rounded-xl border bg-card p-5 transition hover:border-primary/60 hover:bg-muted/40"
    >
      <h3 className="font-semibold">
        {title} <span className="inline-block transition group-hover:translate-x-1">→</span>
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">{body}</p>
    </Link>
  );
}
