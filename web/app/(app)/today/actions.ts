"use server";

import { revalidatePath } from "next/cache";
import { requireSession } from "@/lib/auth/session";
import { db } from "@/lib/db/client.v2";
import { saveCheckin } from "@/lib/queries/checkin";

export type CheckinResult = { ok: true } | { ok: false; error: string };

/** The weekly three-question check-in. Every action starts with the session check. */
export async function submitCheckin(input: { applied: unknown; interviews: unknown; feedback: unknown }): Promise<CheckinResult> {
  const { user } = await requireSession();
  const saved = await saveCheckin(db as never, user.id, input);
  if (!saved) return { ok: false, error: "Check the numbers (whole numbers from 0) and try again." };
  revalidatePath("/today");
  return { ok: true };
}
