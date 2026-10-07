import { headers } from "next/headers";
import { auth, isAllowedEmail } from "@/lib/auth/config";

/**
 * Call this first in every server action and route handler that reads or writes personal data.
 * Throws when there is no session, so the action cannot run for a signed-out visitor even if the
 * middleware were misconfigured.
 */
export async function requireSession() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session) throw new Error("Not signed in");
  if (!isAllowedEmail(session.user.email)) throw new Error("Not on the invite list");
  return session;
}
