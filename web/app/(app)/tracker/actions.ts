"use server";

import { revalidatePath } from "next/cache";
import { db } from "@/lib/db/client.v2";
import { requireSession } from "@/lib/auth/session";
import { changeState, isApplicationState, markApplied, saveApplication, setNextAction, setNotes } from "@/lib/queries/tracker";

export type ActionResult = { ok: true; applicationId: number } | { ok: false; error: string };

const BAD_INPUT: ActionResult = { ok: false, error: "That request was not valid." };

const validId = (n: unknown): n is number => typeof n === "number" && Number.isSafeInteger(n) && n > 0;

function refresh() {
  revalidatePath("/tracker");
  revalidatePath("/jobs");
  revalidatePath("/jobs/[id]", "page");
}

/** Save a posting to the tracker (idempotent). Every action starts with the session check. */
export async function saveJob(postingId: number): Promise<ActionResult> {
  await requireSession();
  if (!validId(postingId)) return BAD_INPUT;
  const saved = await saveApplication(db as never, postingId);
  if (!saved) return { ok: false, error: "That job no longer exists." };
  refresh();
  return { ok: true, applicationId: saved.application.id };
}

/** "I applied": save the job, mark it applied and set a follow-up reminder a week ahead (idempotent). */
export async function appliedToJob(postingId: number): Promise<ActionResult> {
  await requireSession();
  if (!validId(postingId)) return BAD_INPUT;
  const app = await markApplied(db as never, postingId);
  if (!app) return { ok: false, error: "That job no longer exists." };
  refresh();
  return { ok: true, applicationId: app.id };
}

export async function moveApplication(applicationId: number, state: string): Promise<ActionResult> {
  await requireSession();
  if (!validId(applicationId) || !isApplicationState(state)) return BAD_INPUT;
  const moved = await changeState(db as never, applicationId, state);
  if (!moved) return { ok: false, error: "That application was not found." };
  refresh();
  return { ok: true, applicationId: moved.id };
}

/** `date` is "YYYY-MM-DD" or an empty string to clear the reminder. */
export async function scheduleNextAction(applicationId: number, date: string): Promise<ActionResult> {
  await requireSession();
  if (!validId(applicationId)) return BAD_INPUT;
  let when: Date | null = null;
  if (date !== "") {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return BAD_INPUT;
    when = new Date(`${date}T09:00:00Z`);
    if (Number.isNaN(when.getTime())) return BAD_INPUT;
  }
  const updated = await setNextAction(db as never, applicationId, when);
  if (!updated) return { ok: false, error: "That application was not found." };
  refresh();
  return { ok: true, applicationId: updated.id };
}

export async function saveNotes(applicationId: number, notes: string): Promise<ActionResult> {
  await requireSession();
  if (!validId(applicationId) || typeof notes !== "string") return BAD_INPUT;
  const updated = await setNotes(db as never, applicationId, notes);
  if (!updated) return { ok: false, error: "That application was not found." };
  refresh();
  return { ok: true, applicationId: updated.id };
}
