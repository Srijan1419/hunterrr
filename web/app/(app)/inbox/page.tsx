import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Inbox | hunterrr",
  description: "Job emails from your connected Gmail, with uncertain ones waiting for your decision.",
};

export default function InboxPage() {
  return (
    <div>
      <h1>Inbox</h1>
      <p>Job emails from your connected Gmail will appear here, with anything uncertain waiting for your decision.</p>
      <p>Nothing here yet — connect Gmail to start receiving job emails.</p>
    </div>
  );
}
