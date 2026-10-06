/**
 * The ONE list of signed-in destinations. Order matters: Today, Jobs, Tracker,
 * Companies, Sources, Profile. Exactly six — more than seven is a
 * navigation smell, and a test asserts the count. The one-line description is
 * shown and matched by the command palette.
 */
export type Destination = {
  label: string;
  href: string;
  description: string;
  /** "main" tabs are always visible in the top bar; "more" ones sit under the More menu. */
  group: "main" | "more";
};

export const DESTINATIONS: readonly Destination[] = [
  { label: "Today", href: "/today", description: "Your day at a glance: new jobs, follow-ups and your pipeline", group: "main" },
  { label: "Jobs", href: "/jobs", description: "Browse every collected job", group: "main" },
  { label: "Tracker", href: "/tracker", description: "Applications you saved or marked as applied", group: "main" },
  { label: "Companies", href: "/companies", description: "Companies you watch or ignore", group: "more" },
  { label: "Sources", href: "/sources", description: "Where each job feed stands", group: "more" },
  { label: "Profile", href: "/profile", description: "Your skills, locations and pay floor", group: "more" },
];
