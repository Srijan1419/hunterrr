import { createAuthClient } from "better-auth/react";

/**
 * Better Auth client for use in React components.
 * Provides hooks and functions for authentication operations.
 */
export const authClient = createAuthClient({
  baseURL: process.env.NEXT_PUBLIC_BETTER_AUTH_URL || "http://localhost:3000",
});

export const {
  signIn,
  signUp,
  signOut,
  useSession,
  getSession,
} = authClient;