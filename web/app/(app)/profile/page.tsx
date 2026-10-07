import type { Metadata } from "next";
import { ProfileEditor } from "@/components/profile/ProfileEditor";
import { SkillGap } from "@/components/profile/SkillGap";
import { requireSession } from "@/lib/auth/session";
import { db } from "@/lib/db/client.v2";
import { EMPTY_PROFILE } from "@/lib/profile/schema";
import { querySkillGap } from "@/lib/queries/gap";
import { getActiveProfile } from "@/lib/queries/profile";

export const metadata: Metadata = {
  title: "Profile | hunterrr",
  description: "What you are looking for: roles, skills, locations, pay floor and work authorisation.",
};
export const dynamic = "force-dynamic";
// Reading a résumé calls one AI service (up to ~25 s each, two at most); the Hobby plan allows 60 s.
export const maxDuration = 60;

export default async function ProfilePage() {
  const { user } = await requireSession();
  const stored = await getActiveProfile(db as never, user.id);
  const gap = stored ? await querySkillGap(db as never, stored.data) : null;
  return (
    <>
      <ProfileEditor
        initial={stored?.data ?? EMPTY_PROFILE}
        version={stored?.version ?? null}
        savedAt={stored?.savedAt ?? null}
      />
      {gap ? <SkillGap gap={gap} /> : null}
    </>
  );
}
