import { describe, expect, it } from "vitest";
import { DESTINATIONS } from "@/components/shell/destinations";

describe("destinations", () => {
  it("is exactly seven (more than seven is a navigation smell)", () => {
    expect(DESTINATIONS).toHaveLength(7);
  });

  it("is in the required order with the required hrefs", () => {
    expect(DESTINATIONS.map((d) => `${d.label} ${d.href}`)).toEqual([
      "Today /today",
      "Jobs /jobs",
      "Tracker /tracker",
      "Inbox /inbox",
      "Companies /companies",
      "Sources /sources",
      "Profile /profile",
    ]);
  });

  it("shows Today, Jobs and Tracker as tabs and the other four under More", () => {
    expect(DESTINATIONS.filter((d) => d.group === "main").map((d) => d.label)).toEqual(["Today", "Jobs", "Tracker"]);
    expect(DESTINATIONS.filter((d) => d.group === "more").map((d) => d.label)).toEqual(["Inbox", "Companies", "Sources", "Profile"]);
  });

  it("gives every destination a one-line description for the palette", () => {
    for (const d of DESTINATIONS) {
      expect(d.description.trim().length).toBeGreaterThan(0);
      expect(d.description).not.toContain("\n");
    }
  });
});
