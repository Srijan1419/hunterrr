import type { Metadata } from "next";
import { DataQuality } from "@/components/sources/DataQuality";
import { Reports } from "@/components/sources/Reports";
import { SourcesView } from "@/components/sources/SourcesView";
import { sql } from "drizzle-orm";
import { db } from "@/lib/db/client.v2";
import { queryQuality } from "@/lib/queries/quality";
import { openReports } from "@/lib/queries/review";
import { querySources } from "@/lib/queries/sources";

export const metadata: Metadata = {
  title: "Sources | hunterrr",
  description: "Where each job feed stands: last run, how many jobs, and what failed.",
};
export const dynamic = "force-dynamic";

export default async function SourcesPage() {
  const [data, quality, reports] = await Promise.all([querySources(db as never), queryQuality(db as never), openReports(db as never)]);
  // The free Neon plan holds 512 MB; say how full it is (watch this before the first public launch).
  const size = await (db as never as { execute: (q: ReturnType<typeof sql>) => Promise<{ rows: Record<string, unknown>[] }> }).execute(sql`SELECT pg_database_size(current_database()) AS bytes`);
  const usedMb = Math.round(Number(size.rows[0]?.bytes ?? 0) / 1_048_576);
  return (
    <>
      <p style={{ fontSize: "var(--fs-1)", color: "var(--fg-2)", margin: "0 0 12px" }}>
        Database: {usedMb} MB of the 512 MB free plan ({Math.round((usedMb / 512) * 100)}% full).
      </p>
      <SourcesView data={data} />
      <DataQuality q={quality} />
      <Reports items={reports} />
    </>
  );
}
