"use client";

import { useState } from "react";
import { authClient } from "@/lib/auth/client";
import styles from "@/components/auth/auth.module.css";

/**
 * "Continue with Google", the only way in. Shows that it is working the moment it is pressed
 * (the redirect to Google can take a second), and comes back if starting the redirect fails.
 * Rendered only when NEXT_PUBLIC_GOOGLE_AUTH=1, which is set together with GOOGLE_CLIENT_ID/SECRET.
 */
export function GoogleButton() {
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);

  if (process.env.NEXT_PUBLIC_GOOGLE_AUTH !== "1") {
    return <p className={styles.error}>Google sign-in is not set up on this deployment.</p>;
  }

  async function go() {
    setPending(true);
    setFailed(false);
    try {
      const result = await authClient.signIn.social({ provider: "google", callbackURL: "/jobs" });
      if (result?.error) throw new Error(result.error.message);
    } catch {
      setPending(false);
      setFailed(true);
    }
  }

  return (
    <>
      <button type="button" className={styles.google} onClick={go} disabled={pending}>
        <span className={styles.gMark} aria-hidden="true">
          <svg viewBox="0 0 48 48" width="16" height="16">
            <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.5 17.7 9.5 24 9.5z"/>
            <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.6 5.9c4.4-4.1 7-10.1 7-17.6z"/>
            <path fill="#FBBC05" d="M10.5 28.7c-.5-1.5-.8-3-.8-4.7s.3-3.2.8-4.7l-7.9-6.1C.9 16.4 0 20.1 0 24s.9 7.6 2.6 10.8l7.9-6.1z"/>
            <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.9 2.3-8.3 2.3-6.3 0-11.6-4-13.5-9.8l-7.9 6.1C6.5 42.6 14.6 48 24 48z"/>
          </svg>
        </span>
        {pending ? "Opening Google…" : "Continue with Google"}
      </button>
      {failed ? (
        <p role="alert" className={styles.error}>
          Could not reach Google sign-in. Check your connection and try again.
        </p>
      ) : null}
    </>
  );
}
