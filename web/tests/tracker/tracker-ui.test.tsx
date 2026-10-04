import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const actions = vi.hoisted(() => ({
  saveJob: vi.fn(),
  moveApplication: vi.fn(),
  scheduleNextAction: vi.fn(),
}));
vi.mock("@/app/(app)/tracker/actions", () => actions);

import { SaveButton } from "@/components/tracker/SaveButton";
import { StateSelect } from "@/components/tracker/StateSelect";
import { TRACKER_EMPTY, TrackerView } from "@/components/tracker/TrackerView";
import { APPLICATION_STATES, type ApplicationState, type TrackedApplication } from "@/lib/queries/tracker";

const NOW = new Date("2026-10-04T12:00:00Z");
const emptyBoard = () =>
  Object.fromEntries(APPLICATION_STATES.map((s) => [s, [] as TrackedApplication[]])) as Record<ApplicationState, TrackedApplication[]>;
const app = (o: Partial<TrackedApplication>): TrackedApplication => ({
  id: 1, postingId: 7, title: "Backend Engineer", companyName: "Acme", state: "applied",
  stateChangedAt: "2026-10-01T12:00:00Z", nextActionAt: null, notes: "", applyUrl: null, createdAt: "2026-10-01T12:00:00Z", ...o,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("TrackerView", () => {
  it("an empty board shows the heading and the exact empty-state sentence, and no controls", () => {
    const { container } = render(<TrackerView board={emptyBoard()} now={NOW} />);
    expect(screen.getByRole("heading", { level: 1, name: "Tracker" })).toBeInTheDocument();
    expect(screen.getByText(TRACKER_EMPTY)).toBeInTheDocument();
    expect(container.querySelector("button, select, input")).toBeNull();
  });

  it("shows the five open stages as columns with counts, cards link to the job, and days in stage", () => {
    const board = emptyBoard();
    board.applied = [app({ id: 1 })];
    board.interview = [app({ id: 2, title: "Data Engineer", state: "interview", stateChangedAt: "2026-10-04T08:00:00Z" })];
    render(<TrackerView board={board} now={NOW} />);
    for (const name of ["Saved", "Applied", "Assessment", "Interview", "Offer"]) {
      expect(screen.getByRole("heading", { level: 2, name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "Backend Engineer" }).getAttribute("href")).toBe("/jobs/7");
    expect(screen.getByText("3d in stage")).toBeInTheDocument();
    expect(screen.getByText("today")).toBeInTheDocument();
  });

  it("marks an overdue follow-up and shows an upcoming one", () => {
    const board = emptyBoard();
    board.applied = [app({ id: 1, nextActionAt: "2026-10-02T09:00:00Z" }), app({ id: 2, title: "Other", nextActionAt: "2026-10-09T09:00:00Z" })];
    render(<TrackerView board={board} now={NOW} />);
    expect(screen.getByText("Follow up overdue")).toBeInTheDocument();
    expect(screen.getByText("Follow up 2026-10-09")).toBeInTheDocument();
  });

  it("folds closed stages into a details section with their count", () => {
    const board = emptyBoard();
    board.rejected = [app({ id: 3, state: "rejected", title: "Gone" })];
    render(<TrackerView board={board} now={NOW} />);
    expect(screen.getByText("Closed (1)")).toBeInTheDocument();
    expect(screen.getByText("0 active · 1 closed")).toBeInTheDocument();
  });
});

describe("SaveButton", () => {
  it("saves once, then reads Saved and links to the tracker", async () => {
    actions.saveJob.mockResolvedValue({ ok: true, applicationId: 5 });
    render(<SaveButton postingId={42} />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const link = await screen.findByRole("link", { name: /Saved/ });
    expect(link.getAttribute("href")).toBe("/tracker");
    expect(actions.saveJob).toHaveBeenCalledWith(42);
    expect(actions.saveJob).toHaveBeenCalledTimes(1);
  });

  it("shows an error and stays a Save button when saving fails", async () => {
    actions.saveJob.mockResolvedValue({ ok: false, error: "That job no longer exists." });
    render(<SaveButton postingId={42} />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That job no longer exists.");
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("an already-saved job starts as Saved with no button", () => {
    render(<SaveButton postingId={42} saved />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("link", { name: /Saved/ })).toBeInTheDocument();
  });
});

describe("StateSelect", () => {
  it("moves the stage and reverts it with an error if the server refuses", async () => {
    actions.moveApplication.mockResolvedValue({ ok: false, error: "That application was not found." });
    render(<StateSelect applicationId={9} state="applied" nextActionDate="" />);
    const select = screen.getByLabelText("Stage") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "interview" } });
    expect(actions.moveApplication).toHaveBeenCalledWith(9, "interview");
    expect(await screen.findByRole("alert")).toHaveTextContent("not found");
    await waitFor(() => expect(select.value).toBe("applied"));
  });

  it("sets and clears the follow-up date", async () => {
    actions.scheduleNextAction.mockResolvedValue({ ok: true, applicationId: 9 });
    render(<StateSelect applicationId={9} state="applied" nextActionDate="2026-10-09" />);
    const date = screen.getByLabelText("Follow up") as HTMLInputElement;
    expect(date.value).toBe("2026-10-09");
    fireEvent.change(date, { target: { value: "" } });
    await waitFor(() => expect(actions.scheduleNextAction).toHaveBeenCalledWith(9, ""));
  });
});
