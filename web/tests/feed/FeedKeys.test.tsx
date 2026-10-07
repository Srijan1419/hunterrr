import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FeedKeys } from "@/components/feed/FeedKeys";

function page() {
  return render(
    <div>
      <input aria-label="search" />
      <a href="/jobs/1" data-job-link>One</a>
      <a href="/jobs/2" data-job-link>Two</a>
      <a href="/jobs/3" data-job-link>Three</a>
      <FeedKeys />
    </div>,
  );
}

describe("FeedKeys", () => {
  it("j moves to the next job, k to the previous, and stays inside the list", () => {
    const { getByText } = page();
    fireEvent.keyDown(window, { key: "j" });
    expect(document.activeElement).toBe(getByText("One"));
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "j" });
    expect(document.activeElement).toBe(getByText("Three"));
    fireEvent.keyDown(window, { key: "k" });
    expect(document.activeElement).toBe(getByText("Two"));
  });

  it("does nothing while typing in a field or with a modifier key", () => {
    const { getByLabelText, getByText } = page();
    getByLabelText("search").focus();
    fireEvent.keyDown(window, { key: "j" });
    expect(document.activeElement).toBe(getByLabelText("search"));
    getByText("One").focus();
    fireEvent.keyDown(window, { key: "j", ctrlKey: true });
    expect(document.activeElement).toBe(getByText("One"));
  });
});
