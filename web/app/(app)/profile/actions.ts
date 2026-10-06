"use server";

import { revalidatePath } from "next/cache";
import { db } from "@/lib/db/client.v2";
import { requireSession } from "@/lib/auth/session";
import { MAX_PDF_BYTES, ResumeError, pdfText, readResumeText } from "@/lib/profile/resume";
import type { ResumeFields } from "@/lib/profile/schema";
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
  await requireSession();
  const saved = await saveProfile(db as never, input);
  if (!saved) return { ok: false, error: "Some fields are not valid. Check them and save again." };
  revalidatePath("/profile");
  revalidatePath("/today");
  return { ok: true, version: saved.version };
}
