import type { Metadata } from "next";
import Link from "next/link";
import { AuthFrame } from "@/components/auth/AuthFrame";
import styles from "@/components/auth/auth.module.css";

export const metadata: Metadata = { title: "Not allowed | hunterrr" };

export default function NotAllowedPage() {
  return (
    <AuthFrame>
      <h1 className={styles.title}>This Google account can&apos;t sign in</h1>
      <p className={styles.text}>
        Hunterrr is a personal tool and only one Google account can use it. No account was created for
        the one you tried. If that was the wrong Google account, choose a different one.
      </p>
      <Link href="/signin" className={styles.link}>
        Choose a different Google account
      </Link>
    </AuthFrame>
  );
}
