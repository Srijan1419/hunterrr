import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-24 gap-6">
      <h1 className="text-4xl font-bold tracking-tight text-balance">
        Hunterrr
      </h1>
      <p className="text-lg text-muted-foreground">
        Remote job market analytics dashboard
      </p>
      <div className="flex gap-3">
        <Button asChild variant="default">
          <Link href="/signup">Get Started</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/signin">Sign in</Link>
        </Button>
      </div>
      <nav className="flex gap-6 text-sm underline-offset-4">
        <Link className="hover:underline" href="/skills">Skills</Link>
        <Link className="hover:underline" href="/trends">Trends</Link>
        <Link className="hover:underline" href="/coverage">Coverage</Link>
      </nav>
    </main>
  );
}
