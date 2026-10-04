import type { Metadata } from "next";
import { TrackerView } from "@/components/tracker/TrackerView";
import { db } from "@/lib/db/client.v2";
import { listApplications } from "@/lib/queries/tracker";

export const metadata: Metadata = {
  title: "Tracker | hunterrr",
  description: "Applications you saved or marked as applied, moved along by inbox replies.",
};
export const dynamic = "force-dynamic";

export default async function TrackerPage() {
  const board = await listApplications(db as never);
  return <TrackerView board={board} />;
}
