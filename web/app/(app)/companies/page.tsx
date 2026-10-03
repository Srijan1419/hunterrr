import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Companies | hunterrr",
  description: "Companies you watch or ignore, with boards discovered automatically.",
};

export default function CompaniesPage() {
  return (
    <div>
      <h1>Companies</h1>
      <p>Companies you watch or ignore will appear here. Boards are discovered automatically.</p>
      <p>Nothing here yet — watched companies will show up here.</p>
    </div>
  );
}
