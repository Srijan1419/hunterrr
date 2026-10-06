import { createAuthClient } from "better-auth/react";

/**
 * Better Auth client for use in React components.
 * Provides hooks and functions for authentication operations.
 */
export const authClient = createAuthClient({
  // Unset means "this site": the client then calls /api/auth on the page's own origin
  // (a localhost fallback made the sign-in button do nothing on the deployed site).
  baseURL: process.env.NEXT_PUBLIC_BETTER_AUTH_URL || undefined,
});

export const {
  signIn,
  signUp,
  signOut,
  useSession,
  getSession,
} = authClient;