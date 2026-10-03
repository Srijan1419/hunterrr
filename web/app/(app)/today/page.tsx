import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Today | hunterrr",
  description: "Your matches, replies and follow-ups for today.",
};

export default function TodayPage() {
  return (
    <div>
      <h1>Today</h1>
      <p>Your matches, replies and follow-ups will appear here once the first collection has run.</p>
      <p>Nothing here yet — check back after the first collection.</p>
    </div>
  );
}
