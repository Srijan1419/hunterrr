"use server";

import { revalidatePath } from "next/cache";
import { db } from "@/lib/db/client.v2";
import { requireSession } from "@/lib/auth/session";
import { fileReport, isReportField } from "@/lib/queries/review";

export type ReportActionResult = { ok: true; duplicate: boolean } | { ok: false; error: string };

/** "Something wrong?" on a job. Every action starts with the session check. */
export async function reportWrong(postingId: number, field: string, note: string): Promise<ReportActionResult> {
  await requireSession();
  if (typeof postingId !== "number" || !Number.isSafeInteger(postingId) || postingId <= 0 || !isReportField(field)) {
    return { ok: false, error: "That request was not valid." };
  }
  const result = await fileReport(db as never, postingId, field, note);
  if (result === "missing") return { ok: false, error: "That job no longer exists." };
  revalidatePath("/sources");
  return { ok: true, duplicate: result === "duplicate" };
}
