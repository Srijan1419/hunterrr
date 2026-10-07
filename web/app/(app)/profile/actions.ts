"use server";

import { revalidatePath } from "next/cache";
import { db } from "@/lib/db/client.v2";
import { requireSession } from "@/lib/auth/session";
import { MAX_PDF_BYTES, ResumeError, pdfText, readResumeText } from "@/lib/profile/resume";
import type { ResumeFields } from "@/lib/profile/schema";
import { eraseUserData } from "@/lib/queries/erase";
import { saveProfile } from "@/lib/queries/profile";

export type ResumeResult =
  | { ok: true; fields: ResumeFields; dropped: number; provider: string }
  | { ok: false; error: string };

/** Read a résumé PDF into profile fields for review. Nothing is saved and the file is not kept. */
export async function readResume(form: FormData): Promise<ResumeResult> {
  await requireSession();
  const file = form.get("resume");
  if (!(file instanceof File)) return { ok: false, error: "Choose a PDF file first." };
  if (file.size > MAX_PDF_BYTES) return { ok: false, error: "The PDF is larger than 4 MB." };
  try {
    const text = await pdfText(new Uint8Array(await file.arrayBuffer()));
    const reading = await readResumeText(text);
    return { ok: true, ...reading };
  } catch (err) {
    return { ok: false, error: err instanceof ResumeError ? err.message : "Something went wrong reading that PDF." };
  }
}

export type SaveResult = { ok: true; version: number } | { ok: false; error: string };

export async function saveProfileAction(input: unknown): Promise<SaveResult> {
  const { user } = await requireSession();
  const saved = await saveProfile(db as never, user.id, input);
  if (!saved) return { ok: false, error: "Some fields are not valid. Check them and save again." };
  revalidatePath("/profile");
  revalidatePath("/today");
  return { ok: true, version: saved.version };
}

export type EraseResult = { ok: true; profiles: number; applications: number; checkins: number } | { ok: false; error: string };

/** Delete the signed-in person's profile, tracker and check-ins. The word DELETE must be sent back as typed. */
export async function eraseMyData(confirmation: string): Promise<EraseResult> {
  const { user } = await requireSession();
  if (confirmation !== "DELETE") return { ok: false, error: "Type DELETE exactly to confirm." };
  try {
    const erased = await eraseUserData(db as never, user.id);
    if (!erased) return { ok: false, error: "That request was not valid." };
    revalidatePath("/profile");
    revalidatePath("/tracker");
    revalidatePath("/today");
    return { ok: true, ...erased };
  } catch {
    return { ok: false, error: "Could not delete right now. Ask the owner to delete your data." };
  }
}
