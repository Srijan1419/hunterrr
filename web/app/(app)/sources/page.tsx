import type { Metadata } from "next";
import { DataQuality } from "@/components/sources/DataQuality";
import { Reports } from "@/components/sources/Reports";
import { SourcesView } from "@/components/sources/SourcesView";
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
  return (
    <>
      <SourcesView data={data} />
      <DataQuality q={quality} />
      <Reports items={reports} />
    </>
  );
}
