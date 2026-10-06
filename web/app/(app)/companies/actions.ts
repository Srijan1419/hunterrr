"use server";

import { revalidatePath } from "next/cache";
import { db } from "@/lib/db/client.v2";
import { requireSession } from "@/lib/auth/session";
import { isWatchState, setWatch } from "@/lib/queries/companies";

export type WatchResult = { ok: true } | { ok: false; error: string };

/** Watch, ignore or reset one company. Ignored companies disappear from the feed and Today. */
export async function setCompanyWatch(companyId: number, state: string): Promise<WatchResult> {
  await requireSession();
  if (typeof companyId !== "number" || !isWatchState(state)) return { ok: false, error: "That request was not valid." };
  const done = await setWatch(db as never, companyId, state);
  if (!done) return { ok: false, error: "That company was not found." };
  revalidatePath("/companies");
  revalidatePath("/jobs");
  revalidatePath("/today");
  return { ok: true };
}
