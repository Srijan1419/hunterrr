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
      <Button variant="default">Get Started</Button>
    </main>
  );
}