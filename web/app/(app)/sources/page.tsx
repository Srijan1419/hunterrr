import type { Metadata } from "next";
import { SourcesView } from "@/components/sources/SourcesView";
import { db } from "@/lib/db/client.v2";
import { querySources } from "@/lib/queries/sources";

export const metadata: Metadata = {
  title: "Sources | hunterrr",
  description: "Where each job feed stands: last run, how many jobs, and what failed.",
};
export const dynamic = "force-dynamic";

export default async function SourcesPage() {
  const data = await querySources(db as never);
  return <SourcesView data={data} />;
}
