import Link from "next/link";

export const metadata = { title: "Not allowed" };

export default function NotAllowedPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-4">
      <h1 className="text-xl font-semibold">This account is not allowed to sign in</h1>
      <p className="text-sm text-muted-foreground">
        Hunterrr is a personal tool and only one Google account can use it. No account was created for
        the one you tried. If that was the wrong Google account, choose a different one.
      </p>
      <Link href="/signin" className="text-sm underline underline-offset-4">
        Back to sign in
      </Link>
    </main>
  );
}
