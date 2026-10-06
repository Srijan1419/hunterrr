import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import InboxPage from "@/app/(app)/inbox/page";
import CompaniesPage from "@/app/(app)/companies/page";
import ProfilePage from "@/app/(app)/profile/page";

const PAGES: Array<{
  name: string;
  Page: () => React.JSX.Element;
  sentence: string;
}> = [
  {
    name: "Inbox",
    Page: InboxPage,
    sentence:
      "Job emails from your connected Gmail will appear here, with anything uncertain waiting for your decision.",
  },
  {
    name: "Companies",
    Page: CompaniesPage,
    sentence: "Companies you watch or ignore will appear here. Boards are discovered automatically.",
  },
  {
    name: "Profile",
    Page: ProfilePage,
    sentence: "Your skills, locations and pay floor will appear here and decide how jobs are ranked.",
  },
];

// Today is a real screen now (tests/today); these three are still placeholders.
describe("empty-state pages", () => {
  for (const { name, Page, sentence } of PAGES) {
    it(`${name} renders its heading and its exact empty-state sentence`, () => {
      const { unmount } = render(<Page />);
      expect(screen.getByRole("heading", { level: 1, name })).toBeInTheDocument();
      expect(screen.getByText(sentence)).toBeInTheDocument();
      unmount();
    });
  }

  it("shows no filled (primary-looking) button, since no action is wired yet", () => {
    for (const { Page } of PAGES) {
      const { container, unmount } = render(<Page />);
      expect(container.querySelector("button")).toBeNull();
      unmount();
    }
  });
});
