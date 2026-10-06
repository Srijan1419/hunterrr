import type { Metadata } from "next";
import { AuthFrame } from "@/components/auth/AuthFrame";
import { GoogleButton } from "@/components/GoogleButton";
import styles from "@/components/auth/auth.module.css";

export const metadata: Metadata = { title: "Sign in | hunterrr" };

/** Google is the only way in (email and password sign-up is disabled), so it is the only control. */
export default function SigninPage() {
  return (
    <AuthFrame>
      <h1 className={styles.title}>Sign in</h1>
      <p className={styles.text}>Hunterrr is a private tool. Only its owner&apos;s Google account can sign in.</p>
      <GoogleButton />
      <p className={styles.small}>No account is created for any other Google account.</p>
    </AuthFrame>
  );
}
