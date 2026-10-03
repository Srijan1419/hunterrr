import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Sources | hunterrr",
  description: "Where each job feed stands: last run, how many jobs, and what failed.",
};

export default function SourcesPage() {
  return (
    <div>
      <h1>Sources</h1>
      <p>Where each job feed stands will appear here: last run, how many jobs, and what failed.</p>
      <p>Nothing here yet — feed status will show up after the first collection.</p>
    </div>
  );
}
