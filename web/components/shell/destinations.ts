/**
 * The ONE list of signed-in destinations. Order matters: Today, Jobs, Tracker,
 * Inbox, Companies, Sources, Profile. Exactly seven — more than seven is a
 * navigation smell, and a test asserts the count. The one-line description is
 * shown and matched by the command palette.
 */
export type Destination = {
  label: string;
  href: string;
  description: string;
};

export const DESTINATIONS: readonly Destination[] = [
  { label: "Today", href: "/today", description: "Your day at a glance: matches, replies and follow-ups" },
  { label: "Jobs", href: "/jobs", description: "Browse every collected job" },
  { label: "Tracker", href: "/tracker", description: "Applications you saved or marked as applied" },
  { label: "Inbox", href: "/inbox", description: "Job emails from your connected Gmail" },
  { label: "Companies", href: "/companies", description: "Companies you watch or ignore" },
  { label: "Sources", href: "/sources", description: "Where each job feed stands" },
  { label: "Profile", href: "/profile", description: "Your skills, locations and pay floor" },
];
