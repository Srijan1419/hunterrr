import Link from "next/link";

const LINKS = [
  { href: "/skills", label: "Skills" },
  { href: "/trends", label: "Trends" },
  { href: "/coverage", label: "Coverage" },
  { href: "/jobs", label: "Jobs" },
];

/** Site-wide top bar. Server component: plain links, no client JS. */
export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/70 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="inline-block size-2.5 rounded-full bg-primary shadow-[0_0_12px_2px_var(--accent)]" />
          Hunterrr
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="rounded-md px-3 py-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
            >
              {l.label}
            </Link>
          ))}
          <Link
            href="/signin"
            className="ml-2 rounded-md bg-primary px-3 py-1.5 font-medium text-primary-foreground transition hover:opacity-90"
          >
            Sign in
          </Link>
        </nav>
      </div>
    </header>
  );
}
