import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Tracker | hunterrr",
  description: "Applications you saved or marked as applied, moved along by inbox replies.",
};

export default function TrackerPage() {
  return (
    <div>
      <h1>Tracker</h1>
      <p>Applications you save or mark as applied will appear here, and replies from your inbox will move them along.</p>
      <p>Nothing here yet — saved applications will show up here.</p>
    </div>
  );
}
